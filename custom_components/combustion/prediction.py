"""Home Assistant connection lifecycle for library-owned probe sessions."""

from __future__ import annotations

import asyncio
from contextlib import suppress
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
        client = None
        session = None
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
                timeout=20,
            )
            session = ProbeSession(self._meatnet, self._probe.serial_number, client)
            async with asyncio.timeout(30):
                await session.start()
            await disconnected.wait()
        except (*BLEAK_RETRY_EXCEPTIONS, OSError) as err:
            LOGGER.debug("Prediction connection to %s failed: %s", device.address, err)
        finally:
            try:
                if session is not None:
                    session.disconnected()
                    try:
                        async with asyncio.timeout(10):
                            await session.stop()
                    except Exception as err:
                        LOGGER.debug("Prediction subscription cleanup failed: %s", err)
                if client is not None:
                    try:
                        async with asyncio.timeout(10):
                            await client.disconnect()
                    except (*BLEAK_RETRY_EXCEPTIONS, OSError) as err:
                        LOGGER.debug("Prediction disconnect failed: %s", err)
                    finally:
                        # A failed unsubscribe retains session ownership. Once disconnected,
                        # stop again to release that ownership without further Bluetooth I/O.
                        if session is not None and not client.is_connected:
                            await session.stop()
            finally:
                self._retry_at = monotonic() + RETRY_DELAY
                self._task = None

    async def async_stop(self) -> None:
        """Cancel outstanding work and release the probe connection."""
        self._closed = True
        if self._task is not None:
            task = self._task
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
