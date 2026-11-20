"""Feed Home Assistant's Bluetooth advertisements into a combustion_ble MeatNet."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from combustion_ble import (
    COMBUSTION_MANUFACTURER_ID,
    Device,
    MeatNet,
    Probe,
    ProbeAdvertisement,
    parse_advertisement,
)
from combustion_ble.meatnet import DEFAULT_STALE_TIMEOUT
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

from .const import LOGGER
from .prediction import PredictionMonitor

EXPIRE_INTERVAL = timedelta(seconds=1)

type CombustionConfigEntry = ConfigEntry[CombustionCoordinator]


class CombustionCoordinator:
    """Keeps a MeatNet up to date with advertisements from Home Assistant's Bluetooth integration.

    Home Assistant owns all scanning. Temperature readings work with passive adapters and proxies.
    Optional prediction monitors connect directly to probes through connectable adapters.
    """

    def __init__(
        self, hass: HomeAssistant, *, enable_predictions: bool = False
    ) -> None:
        """Initialize."""
        self.hass = hass
        self.enable_predictions = enable_predictions
        self.predictions: dict[int, PredictionMonitor] = {}
        self.meatnet = MeatNet()
        self._handled_times: dict[str, float] = {}
        self.meatnet.add_device_listener(self._async_log_availability)

    @callback
    def async_start(self) -> CALLBACK_TYPE:
        """Start listening for advertisements. Returns a callback that stops listening."""
        cancel_callback = bluetooth.async_register_callback(
            self.hass,
            self._async_handle_service_info,
            # connectable=False matches every advertisement, not only connectable ones. Probes stop
            # advertising as connectable once they have 3 connections, and passive proxies never
            # report connectable advertisements.
            bluetooth.BluetoothCallbackMatcher(
                manufacturer_id=COMBUSTION_MANUFACTURER_ID, connectable=False
            ),
            bluetooth.BluetoothScanningMode.PASSIVE,
        )
        cancel_timer = async_track_time_interval(
            self.hass,
            self._async_tick,
            EXPIRE_INTERVAL,
            name="Combustion MeatNet expiry",
            cancel_on_shutdown=True,
        )

        @callback
        def _async_stop() -> None:
            cancel_callback()
            cancel_timer()

        return _async_stop

    @callback
    def async_add_device_listener(
        self, listener: Callable[[Device], None]
    ) -> CALLBACK_TYPE:
        """Call ``listener`` for every known device, then for each device discovered later."""
        for device in self.meatnet.devices:
            listener(device)
        return self.meatnet.add_device_listener(listener)

    @callback
    def _async_log_availability(self, device: Device) -> None:
        """Log once when a device stops being heard, and once when it is heard again."""
        was_available = device.available

        @callback
        def _async_on_change() -> None:
            nonlocal was_available
            if device.available == was_available:
                return
            was_available = device.available
            if was_available:
                LOGGER.info("%s is available again", device)
            else:
                LOGGER.info(
                    "%s is unavailable: not heard directly or through MeatNet for %d seconds",
                    device,
                    DEFAULT_STALE_TIMEOUT,
                )

        device.add_listener(_async_on_change)

    @callback
    def _async_handle_service_info(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange = bluetooth.BluetoothChange.ADVERTISEMENT,
    ) -> None:
        self._handled_times[service_info.address] = service_info.time
        self.meatnet.handle_service_info(service_info)
        if self.enable_predictions:
            payload = service_info.manufacturer_data.get(COMBUSTION_MANUFACTURER_ID)
            advertisement = (
                parse_advertisement(payload) if payload is not None else None
            )
            if (
                isinstance(advertisement, ProbeAdvertisement)
                and advertisement.has_probe
                and not advertisement.is_repeated
            ):
                self.prediction_for(
                    self.meatnet.probes[advertisement.serial_number]
                ).async_connect(service_info.address)

    @callback
    def _async_tick(self, _now: datetime) -> None:
        # Home Assistant skips callbacks for an advertisement that is identical to the previous
        # one from the same address, so a probe whose readings hold steady would look silent and
        # go unavailable. Its Bluetooth history still records every advertisement, so catch up
        # from there.
        for address, handled_time in list(self._handled_times.items()):
            service_info = bluetooth.async_last_service_info(
                self.hass, address, connectable=False
            )
            if service_info is not None and service_info.time > handled_time:
                self._async_handle_service_info(service_info)

        self.meatnet.expire()

    @callback
    def prediction_for(self, probe: Probe) -> PredictionMonitor:
        """Get a probe's prediction monitor."""
        if probe.serial_number not in self.predictions:
            self.predictions[probe.serial_number] = PredictionMonitor(
                self.hass, self.meatnet, probe
            )
        return self.predictions[probe.serial_number]

    async def async_stop_predictions(self) -> None:
        """Release all direct probe connections during unload."""
        for monitor in self.predictions.values():
            await monitor.async_stop()
