"""Home Assistant connection lifecycle for library-owned prediction sessions.

A repeater monitor connects to one MeatNet repeater, which relays predictions for every probe on
its network. Probe monitors connect directly to probes the repeater is not reporting.
"""

from __future__ import annotations

import asyncio
from time import monotonic

from bleak.backends.device import BLEDevice
from bleak_retry_connector import (
    BLEAK_RETRY_EXCEPTIONS,
    BleakClientWithServiceCache,
    establish_connection,
)
from combustion_ble import MeatNet, NodeSession, Probe, ProbeSession
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


type Session = ProbeSession | NodeSession


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
            self._session = session
            await disconnected.wait()
        except (*BLEAK_RETRY_EXCEPTIONS, OSError) as err:
            LOGGER.debug("Prediction connection to %s failed: %s", device.address, err)
        finally:
            self._session = None
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

    @property
    def failed_addresses(self) -> frozenset[str]:
        """Repeaters that failed during this round of connection attempts."""
        return frozenset(self._failed_addresses)

    @callback
    def reset_failures(self) -> None:
        """Begin another round once every reachable candidate has been tried."""
        self._failed_addresses.clear()


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
