"""Read-only prediction connections, using Home Assistant's Bluetooth adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from time import monotonic
from typing import Any

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak_retry_connector import BLEAK_RETRY_EXCEPTIONS, establish_connection
from combustion_ble import (
    PredictionMode,
    PredictionState,
    PredictionStatus,
    PredictionType,
)
from combustion_ble.ble_data.probe_status import ProbeStatus
from combustion_ble.const import DEVICE_STATUS_CHARACTERISTIC
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant, callback

from .const import LOGGER

STALE_TIMEOUT = 15
RETRY_DELAY = 60


class PredictionMonitor:
    """Keep one direct probe connection without changing its cook or passive readings."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize an idle monitor."""
        self.hass = hass
        self.status: PredictionStatus | None = None
        self._updated = 0.0
        self._retry_at = 0.0
        self._task: asyncio.Task | None = None
        self._listeners: set[Callable[[], None]] = set()
        self._closed = False
        self._receiving = False

    @callback
    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Subscribe to prediction changes."""
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    @callback
    def _publish(self) -> None:
        for listener in tuple(self._listeners):
            listener()

    @property
    def active(self) -> bool:
        """Whether a supported prediction mode is enabled on the probe."""
        return self.status is not None and self.status.prediction_mode in (
            PredictionMode.TIME_TO_REMOVAL,
            PredictionMode.REMOVAL_AND_RESTING,
        )

    @property
    def seconds_remaining(self) -> int | None:
        """Count down a usable prediction; unknown while warming or disconnected."""
        status = self.status
        if not self.active or status.prediction_type not in (
            PredictionType.REMOVAL,
            PredictionType.RESTING,
        ):
            return None
        if status.prediction_state == PredictionState.REMOVAL_PREDICTION_DONE:
            return 0
        if status.prediction_state != PredictionState.PREDICTING:
            return None
        return max(
            0, int(status.prediction_value_seconds) - int(monotonic() - self._updated)
        )

    @callback
    def async_tick(self) -> None:
        """Expire stale statuses and refresh countdown entities."""
        if self.status is not None:
            if monotonic() - self._updated > STALE_TIMEOUT:
                self.status = None
            self._publish()

    @callback
    def async_connect(self, address: str) -> None:
        """Attempt a connection when a direct probe is reachable, backing off on failure."""
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
        disconnected = asyncio.Event()

        def on_disconnect(_client: BleakClient) -> None:
            self._receiving = False
            self.status = None
            self._publish()
            disconnected.set()

        try:
            client = await establish_connection(
                BleakClient, device, device.address, disconnected_callback=on_disconnect
            )
            self._receiving = True
            async with asyncio.timeout(30):
                await client.start_notify(
                    DEVICE_STATUS_CHARACTERISTIC, self._handle_status
                )
            await disconnected.wait()
        except BLEAK_RETRY_EXCEPTIONS as err:
            LOGGER.debug("Prediction connection to %s failed: %s", device.address, err)
        finally:
            self._receiving = False
            self.status = None
            self._publish()
            if client is not None:
                with suppress(*BLEAK_RETRY_EXCEPTIONS):
                    async with asyncio.timeout(10):
                        await client.disconnect()
            self._retry_at = monotonic() + RETRY_DELAY
            self._task = None

    @callback
    def _handle_status(self, _sender: Any, data: bytearray) -> None:
        if not self._receiving:
            return
        try:
            status = ProbeStatus.from_data(bytes(data))
        except ValueError, IndexError:
            LOGGER.debug("Ignoring malformed prediction status")
            return
        if status is not None:
            self.status = status.prediction_status
            self._updated = monotonic()
            self._publish()

    async def async_stop(self) -> None:
        """Cancel outstanding work and release the probe connection."""
        self._closed = True
        if self._task is not None:
            task = self._task
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
