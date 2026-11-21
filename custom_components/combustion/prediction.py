"""Home Assistant connection lifecycle for library-owned probe sessions."""

from __future__ import annotations

import asyncio
from time import monotonic

from bleak.backends.device import BLEDevice
from bleak_retry_connector import (
    BLEAK_RETRY_EXCEPTIONS,
    BleakClientWithServiceCache,
    establish_connection,
)
from combustion_ble import MeatNet, Probe, ProbeSession
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant, callback

from .const import LOGGER

RETRY_DELAY = 60
"""Seconds to wait after a failed connection or cleanup before trying again."""

RECONNECT_DELAY = 5
"""Seconds to wait after a working session drops, so a flapping link cannot hammer the probe."""

CONNECT_TIMEOUT = 20
SUBSCRIBE_TIMEOUT = 30
CLEANUP_TIMEOUT = 10


class PredictionMonitor:
    """Connect a direct probe while the library owns subscriptions and prediction state."""

    def __init__(self, hass: HomeAssistant, meatnet: MeatNet, probe: Probe) -> None:
        """Initialize an idle connection monitor."""
        self.hass = hass
        self._meatnet = meatnet
        self._probe = probe
        self._retry_at = 0.0
        self._task: asyncio.Task | None = None
        self._closed = False
        # A client and session whose cleanup has not finished. The session keeps ownership of the
        # probe until it is released, so it must be cleaned up before a replacement can start.
        self._unreleased: (
            tuple[BleakClientWithServiceCache, ProbeSession | None] | None
        ) = None

    @callback
    def async_connect(self, address: str) -> None:
        """Attempt a connection through HA's current adapter, backing off on failure."""
        if (
            self._closed
            or self.hass.is_stopping
            or self._task is not None
            or monotonic() < self._retry_at
        ):
            return
        device = bluetooth.async_ble_device_from_address(
            self.hass, address, connectable=True
        )
        if device is None:
            return
        self._task = self.hass.async_create_background_task(
            self._async_run(device),
            f"Combustion prediction {address}",
            eager_start=False,
        )

    async def _async_run(self, device: BLEDevice) -> None:
        subscribed = False
        try:
            if await self._async_release_unreleased():
                subscribed = await self._async_session(device)
        finally:
            self._retry_at = monotonic() + (
                RECONNECT_DELAY if subscribed else RETRY_DELAY
            )
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
                self._probe.serial_number_string,
                disconnected_callback=on_disconnect,
                timeout=CONNECT_TIMEOUT,
            )
            # Failed attempts inside establish_connection can report disconnects before a retry
            # succeeds. Only disconnects of the connected client end the session; one that was
            # lost before this point is still caught, because start() checks is_connected.
            disconnected.clear()
            session = ProbeSession(self._meatnet, self._probe.serial_number, client)
            async with asyncio.timeout(SUBSCRIBE_TIMEOUT):
                await session.start()
            subscribed = True
            await disconnected.wait()
        except (*BLEAK_RETRY_EXCEPTIONS, OSError) as err:
            LOGGER.debug("Prediction connection to %s failed: %s", device.address, err)
        finally:
            if client is not None:
                # Recorded before cleanup, so cleanup that fails or is cancelled is retried
                # before the next connection and when the monitor stops.
                self._unreleased = (client, session)
                await self._async_release_unreleased()
        return subscribed

    async def _async_release_unreleased(self) -> bool:
        """Finish any outstanding cleanup. Return whether the probe is free for a new session."""
        if self._unreleased is None:
            return True
        if not await _async_release(*self._unreleased):
            return False
        self._unreleased = None
        return True

    async def async_stop(self) -> None:
        """Cancel outstanding work and release the probe connection."""
        self._closed = True
        if (task := self._task) is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                # Only the monitor task's cancellation is expected; propagate the caller's own.
                if (current := asyncio.current_task()) and current.cancelling():
                    raise
        await self._async_release_unreleased()


async def _async_release(
    client: BleakClientWithServiceCache, session: ProbeSession | None
) -> bool:
    """Unsubscribe and disconnect. Return whether the session and the link are both released.

    The disconnect and the release of session ownership run even if unsubscribing is cancelled,
    so cancellation cannot leave the probe connected.
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


async def _async_stop_session(session: ProbeSession) -> bool:
    """Stop a session within the cleanup deadline. Return whether it released the probe."""
    try:
        async with asyncio.timeout(CLEANUP_TIMEOUT):
            await session.stop()
    except Exception as err:  # Any failure leaves cleanup to be retried.
        LOGGER.debug("Prediction subscription cleanup failed: %s", err)
        return False
    return True
