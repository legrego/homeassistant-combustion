"""Home Assistant connection lifecycle for library-owned sessions.

A repeater monitor connects to one MeatNet repeater, which relays predictions for every probe on
its network. Probe monitors connect directly to probes the repeater is not reporting. Both also
run firmware and hardware revision reads. A revision monitor connects briefly, only to read
revisions of devices no prediction connection covers.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from time import monotonic
from typing import Any

from bleak.backends.device import BLEDevice
from bleak_retry_connector import (
    BLEAK_RETRY_EXCEPTIONS,
    BleakClientWithServiceCache,
    establish_connection,
)
from combustion_ble import (
    MeatNet,
    NodeSession,
    Probe,
    ProbeSession,
    RequestRejectedError,
)
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant, callback

from .const import LOGGER

RETRY_DELAY = 60
"""Seconds to wait after a failed connection or cleanup before trying again."""

RECONNECT_DELAY = 5
"""Seconds to wait after a working session drops, so a flapping link cannot hammer the device."""

CONNECT_TIMEOUT = 20
SUBSCRIBE_TIMEOUT = 30
CLEANUP_TIMEOUT = 10
INFORMATION_TIMEOUT = 30
"""Seconds to allow for reading a device's firmware and hardware revisions.

Through a repeater, each revision is a request that may cross several MeatNet hops."""

_READ_ERRORS = (*BLEAK_RETRY_EXCEPTIONS, OSError, RequestRejectedError, ValueError)
"""Failures of an optional revision read, none of which affect the session."""


type Session = ProbeSession | NodeSession

type Read = Callable[[Session], Coroutine[Any, Any, None]]
"""Reads revisions through a running session."""

type ReadDone = Callable[[bool | None], None]
"""Told whether a read succeeded, or ``None`` when it never ran."""

type ProbeCommand = Callable[[ProbeSession], Coroutine[Any, Any, None]]
"""Sends a command to the directly connected probe."""

type NodeCommand = Callable[[NodeSession, int], Coroutine[Any, Any, None]]
"""Sends a command through a repeater to the probe with this serial number."""


class _ConnectionMonitor:
    """Connect a device while the library owns subscriptions and prediction state."""

    def __init__(self, hass: HomeAssistant, meatnet: MeatNet) -> None:
        """Initialize an idle connection monitor."""
        self.hass = hass
        self._meatnet = meatnet
        self._retry_at = 0.0
        self._task: asyncio.Task | None = None
        self._closed = False
        self._session: Session | None = None
        """The session whose subscriptions are running, if any."""
        self._failed_addresses: set[str] = set()
        """Addresses that failed during the current round of connection attempts."""
        # A client and session whose cleanup has not finished. The session keeps ownership of the
        # device until it is released, so it must be cleaned up before a replacement can start.
        self._unreleased: tuple[BleakClientWithServiceCache, Session | None] | None = (
            None
        )
        self._wake = asyncio.Event()
        """Set when a running session has more work, or has disconnected."""
        self._session_ended: asyncio.Event | None = None
        """Set to end the running session, as a disconnect would."""
        self._reads: dict[str, tuple[Read, ReadDone]] = {}
        """Revision reads waiting for the session, by the name of the device they read."""
        self.address: str | None = None
        """Address of the device the running session is connected to."""
        self._unrun_result: bool | None = None
        """What reads that never ran are reported as."""
        self._connecting = False
        """Whether the running attempt is connecting or subscribing, before its session runs."""

    @callback
    def async_request_read(self, name: str, read: Read, done: ReadDone) -> bool:
        """Queue a revision read on the running session. Return whether it was queued."""
        if self._session is None:
            return False
        self._reads.setdefault(name, (read, done))
        self._wake.set()
        return True

    async def _async_connected(
        self, session: Session, disconnected: asyncio.Event
    ) -> None:
        """Run reads while a session is connected. Return once it disconnects."""
        while not disconnected.is_set():
            await self._async_run_reads(session, disconnected)
            self._wake.clear()
            if not disconnected.is_set() and not self._reads:
                await self._wake.wait()

    async def _async_run_reads(
        self, session: Session, disconnected: asyncio.Event
    ) -> None:
        """Run queued reads one at a time, so they never crowd out relayed status.

        A read stops when the session ends, rather than holding the connection until its own
        timeout: Bluetooth reads don't notice the session closing, for example after a failed
        command.
        """
        while self._reads and not disconnected.is_set():
            name = next(iter(self._reads))
            read, done = self._reads.pop(name)
            reading = asyncio.ensure_future(
                _async_read_information(read(session), name)
            )
            ended = asyncio.ensure_future(disconnected.wait())
            try:
                await asyncio.wait(
                    (reading, ended), return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                ended.cancel()
                if not reading.done():
                    reading.cancel()
                    # Unlike awaiting the read, this doesn't swallow this task's own cancellation.
                    await asyncio.wait((reading,))
                done(
                    None
                    if reading.cancelled() or reading.exception() is not None
                    else reading.result()
                )
            if not reading.cancelled():
                # Raise unexpected read errors, as before. A read stopped because the session
                # ended is not an error: the session ran, so it reconnects after the usual delay.
                reading.result()

    @callback
    def _async_drop_reads(self, result: bool | None) -> None:
        reads, self._reads = self._reads, {}
        for _, done in reads.values():
            done(result)

    def _name(self) -> str:
        raise NotImplementedError

    def _create_session(
        self, client: BleakClientWithServiceCache, address: str
    ) -> Session:
        raise NotImplementedError

    @property
    def can_connect(self) -> bool:
        """Whether a connection attempt may start now: idle, not stopped, and not backing off."""
        return (
            not self._closed
            and not self.hass.is_stopping
            and self._task is None
            and monotonic() >= self._retry_at
        )

    @callback
    def async_connect(self, address: str) -> bool:
        """Attempt a connection through HA's current adapter, backing off on failure.

        Return whether an attempt started. It does not start while another attempt or session is
        running, while backing off, or when no connection-capable adapter can reach ``address``.
        """
        if not self.can_connect:
            return False
        device = bluetooth.async_ble_device_from_address(
            self.hass, address, connectable=True
        )
        if device is None:
            return False
        self._task = self.hass.async_create_background_task(
            self._async_run(device),
            f"Combustion prediction {address}",
            eager_start=False,
        )
        return True

    async def _async_run(self, device: BLEDevice) -> None:
        subscribed = False
        try:
            if await self._async_release_unreleased():
                subscribed = await self._async_session(device)
        finally:
            self._async_drop_reads(self._unrun_result)
            self._retry_at = monotonic() + (
                RECONNECT_DELAY if subscribed else RETRY_DELAY
            )
            if subscribed:
                self._failed_addresses.clear()
            else:
                self._failed_addresses.add(device.address)
            self._task = None

    async def _async_session(self, device: BLEDevice) -> bool:
        """Run one session until it disconnects. Return whether its subscriptions started."""
        client = None
        session = None
        subscribed = False
        disconnected = asyncio.Event()

        def on_disconnect(_client: BleakClientWithServiceCache) -> None:
            if session is not None:
                session.disconnected()
            disconnected.set()
            self._wake.set()

        self._connecting = True
        try:
            client = await establish_connection(
                BleakClientWithServiceCache,
                device,
                self._name(),
                disconnected_callback=on_disconnect,
                timeout=CONNECT_TIMEOUT,
            )
            # Failed attempts inside establish_connection can report disconnects before a retry
            # succeeds. Only disconnects of the connected client end the session; one that was
            # lost before this point is still caught, because start() checks is_connected.
            disconnected.clear()
            session = self._create_session(client, device.address)
            async with asyncio.timeout(SUBSCRIBE_TIMEOUT):
                await session.start()
            subscribed = True
            self._connecting = False
            self._session = session
            self._session_ended = disconnected
            self.address = device.address
            await self._async_connected(session, disconnected)
        except (*BLEAK_RETRY_EXCEPTIONS, OSError) as err:
            LOGGER.debug("Prediction connection to %s failed: %s", device.address, err)
        finally:
            self._connecting = False
            self._session = None
            self._session_ended = None
            self.address = None
            if client is not None:
                # Recorded before cleanup, so cleanup that fails or is cancelled is retried
                # before the next connection and when the monitor stops.
                self._unreleased = (client, session)
                await self._async_release_unreleased()
        return subscribed

    async def _async_release_unreleased(self) -> bool:
        """Finish any outstanding cleanup. Return whether the device is free for a new session."""
        if self._unreleased is None:
            return True
        if not await _async_release(*self._unreleased):
            return False
        self._unreleased = None
        return True

    @callback
    def _async_end_session(self) -> None:
        """Release the running session's connection. The next attempt makes a fresh one."""
        if self._session_ended is not None:
            self._session_ended.set()
            self._wake.set()

    @property
    def cleanup_retry_ready(self) -> bool:
        """Whether failed cleanup may be retried without hammering the device."""
        return monotonic() >= self._retry_at

    async def async_stop(self) -> bool:
        """Cancel outstanding work. Return whether the connection was fully released."""
        self._closed = True
        if (task := self._task) is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                # Only the monitor task's cancellation is expected; propagate the caller's own.
                if (current := asyncio.current_task()) and current.cancelling():
                    raise
        released = await self._async_release_unreleased()
        if not released:
            self._retry_at = monotonic() + RETRY_DELAY
        return released


class PredictionMonitor(_ConnectionMonitor):
    """Connect directly to one probe."""

    def __init__(self, hass: HomeAssistant, meatnet: MeatNet, probe: Probe) -> None:
        """Initialize an idle connection monitor."""
        super().__init__(hass, meatnet)
        self._probe = probe

    @property
    def session(self) -> ProbeSession | None:
        """The probe session whose subscriptions are running, if any."""
        return self._session if isinstance(self._session, ProbeSession) else None

    async def async_command(self, command: ProbeCommand) -> None:
        """Send a command to the probe and await its acknowledgement.

        Raises the library's errors, or ``ConnectionError`` when no session is running.
        """
        if (session := self.session) is None:
            raise ConnectionError("Probe is not connected")
        try:
            await command(session)
        except RequestRejectedError:
            raise
        except BaseException:
            # Direct responses carry no request ID, so the session closes itself after a failed,
            # timed-out, or cancelled command. Release its connection to reconnect.
            self._async_end_session()
            raise

    def _name(self) -> str:
        return self._probe.serial_number_string

    def _create_session(
        self, client: BleakClientWithServiceCache, address: str
    ) -> ProbeSession:
        return ProbeSession(self._meatnet, self._probe.serial_number, client)


class RepeaterMonitor(_ConnectionMonitor):
    """Connect to one MeatNet repeater, which relays predictions for every probe it reaches."""

    def _name(self) -> str:
        return "Combustion repeater"

    def _create_session(
        self, client: BleakClientWithServiceCache, address: str
    ) -> NodeSession:
        return NodeSession(self._meatnet, address, client)

    @property
    def session(self) -> NodeSession | None:
        """The repeater session whose subscriptions are running, if any."""
        return self._session if isinstance(self._session, NodeSession) else None

    async def async_command(self, serial_number: int, command: NodeCommand) -> None:
        """Send a command through the repeater to a probe and await its acknowledgement.

        Raises the library's errors, or ``ConnectionError`` when no session is running. Failures
        leave the session running.
        """
        if (session := self.session) is None:
            raise ConnectionError("Repeater is not connected")
        await command(session, serial_number)

    @property
    def failed_addresses(self) -> frozenset[str]:
        """Repeaters that failed during this round of connection attempts."""
        return frozenset(self._failed_addresses)

    @callback
    def reset_failures(self) -> None:
        """Begin another round once every reachable candidate has been tried."""
        self._failed_addresses.clear()


class RevisionMonitor(_ConnectionMonitor):
    """Connect briefly, only to read revisions, then disconnect.

    Connects to a MeatNet node, which can read its own revisions and those of every probe it
    relays, or directly to a probe. One connection at a time.
    """

    def __init__(self, hass: HomeAssistant, meatnet: MeatNet) -> None:
        """Initialize an idle connection monitor."""
        super().__init__(hass, meatnet)
        self._probe: Probe | None = None
        self._target: str | None = None
        """Address of the device the current or unreleased connection is for."""
        self._abandoned = False
        """Whether the current connection has been asked to give way."""

    @property
    def held_address(self) -> str | None:
        """The device this monitor is connecting, connected, or not yet released from.

        That device can't take another session until this monitor lets it go.
        """
        if self._task is None and self._unreleased is None:
            return None
        return self._target

    @callback
    def async_cancel(self) -> None:
        """Abandon the running connection, releasing it. Its unrun reads count as not tried.

        Repeated calls do nothing more, and cleanup that has started always runs to completion:
        interrupting a slow disconnect would hold the device until a later cleanup retry.
        """
        if self._task is None or self._abandoned:
            return
        self._abandoned = True
        self._unrun_result = None
        if self._session_ended is not None:
            # Connected: end the session, which stops its read, then disconnects.
            self._async_end_session()
        elif self._connecting:
            # Not connected yet, and establishing the connection doesn't watch for an ending.
            self._task.cancel()
        # Otherwise cleanup is already underway.

    @callback
    def async_read(
        self, address: str, probe: Probe | None, reads: dict[str, tuple[Read, ReadDone]]
    ) -> bool:
        """Connect to the node at ``address``, or to ``probe`` directly, and run ``reads``.

        Return ``False`` without doing anything while another connection is running or backing
        off. Otherwise every read reports its result, including when no connection-capable adapter
        can reach ``address``.
        """
        if not self.can_connect:
            return False
        self._probe = probe
        self._target = address
        self._abandoned = False
        # The connection is the attempt, so reads it never runs have failed.
        self._unrun_result = False
        self._reads = dict(reads)
        if not self.async_connect(address):
            self._async_drop_reads(False)
        return True

    @callback
    def async_retry_cleanup(self) -> None:
        """Retry releasing a connection whose disconnect failed, pacing attempts.

        Otherwise a failed disconnect would hold the connection until the next brief read, which
        can be a day away.
        """
        if (
            self._unreleased is None
            or self._task is not None
            or self._closed
            or self.hass.is_stopping
            or not self.cleanup_retry_ready
        ):
            return
        self._task = self.hass.async_create_background_task(
            self._async_cleanup(), "Combustion revisions cleanup", eager_start=False
        )

    async def _async_cleanup(self) -> None:
        try:
            if not await self._async_release_unreleased():
                self._retry_at = monotonic() + RETRY_DELAY
        finally:
            self._task = None

    async def _async_connected(
        self, session: Session, disconnected: asyncio.Event
    ) -> None:
        await self._async_run_reads(session, disconnected)

    def _name(self) -> str:
        return "Combustion revisions"

    def _create_session(
        self, client: BleakClientWithServiceCache, address: str
    ) -> Session:
        if self._probe is not None:
            return ProbeSession(self._meatnet, self._probe.serial_number, client)
        return NodeSession(self._meatnet, address, client)


async def _async_read_information(read: Coroutine[Any, Any, None], name: str) -> bool:
    """Read firmware and hardware revisions. Return whether the read succeeded.

    Revisions are optional, so failures are only logged and never affect the session.
    """
    try:
        async with asyncio.timeout(INFORMATION_TIMEOUT):
            await read
    except _READ_ERRORS as err:
        LOGGER.debug("Reading revisions of %s failed: %s", name, err)
        return False
    return True


async def _async_release(
    client: BleakClientWithServiceCache, session: Session | None
) -> bool:
    """Unsubscribe and disconnect. Return whether the session and the link are both released.

    The disconnect and the release of session ownership run even if unsubscribing is cancelled,
    so cancellation cannot leave the device connected.
    """
    released = session is None
    try:
        if session is not None:
            session.disconnected()
            released = await _async_stop_session(session)
    finally:
        try:
            async with asyncio.timeout(CLEANUP_TIMEOUT):
                await client.disconnect()
        except (*BLEAK_RETRY_EXCEPTIONS, OSError) as err:
            LOGGER.debug("Prediction disconnect failed: %s", err)
        if session is not None and not released and not client.is_connected:
            # A failed unsubscribe retains session ownership. Once disconnected, stop again to
            # release that ownership without further Bluetooth I/O.
            released = await _async_stop_session(session)
    return released and not client.is_connected


async def _async_stop_session(session: Session) -> bool:
    """Stop a session within the cleanup deadline. Return whether it released the device."""
    try:
        async with asyncio.timeout(CLEANUP_TIMEOUT):
            await session.stop()
    except Exception as err:  # Any failure leaves cleanup to be retried.
        LOGGER.debug("Prediction subscription cleanup failed: %s", err)
        return False
    return True
