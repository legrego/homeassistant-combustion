"""Feed Home Assistant's Bluetooth advertisements into a combustion_ble MeatNet."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta

from combustion_ble import (
    COMBUSTION_MANUFACTURER_ID,
    ConnectionPlan,
    Device,
    MeatNet,
    Probe,
    ProbeAdvertisement,
    parse_advertisement,
    plan_connections,
)
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

from .const import LOGGER, STALE_TIMEOUT
from .prediction import PredictionMonitor, RepeaterMonitor

EXPIRE_INTERVAL = timedelta(seconds=1)

type CombustionConfigEntry = ConfigEntry[CombustionCoordinator]


class CombustionCoordinator:
    """Keeps a MeatNet up to date with advertisements from Home Assistant's Bluetooth integration.

    Home Assistant owns all scanning. Temperature readings work with passive adapters and proxies.
    Optional predictions use connectable adapters: one connection to a MeatNet repeater, which
    relays predictions for every probe on its network, and direct connections only to probes the
    repeater is not reporting.
    """

    def __init__(
        self, hass: HomeAssistant, *, enable_predictions: bool = False
    ) -> None:
        """Initialize."""
        self.hass = hass
        self.enable_predictions = enable_predictions
        self.predictions: dict[int, PredictionMonitor] = {}
        """Direct probe connections, by probe serial number."""
        self._predictions_stopped = False
        self.meatnet = MeatNet(stale_timeout=STALE_TIMEOUT)
        self.repeater = (
            RepeaterMonitor(hass, self.meatnet) if enable_predictions else None
        )
        # Direct connections being released because the repeater reports their probes. A probe
        # gets no new direct connection until its old session has let go of the probe.
        self._retiring: dict[int, PredictionMonitor] = {}
        self._retiring_tasks: dict[int, asyncio.Task] = {}
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
                    STALE_TIMEOUT,
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
        if self.enable_predictions and not self._predictions_stopped:
            payload = service_info.manufacturer_data.get(COMBUSTION_MANUFACTURER_ID)
            advertisement = (
                parse_advertisement(payload) if payload is not None else None
            )
            if (
                isinstance(advertisement, ProbeAdvertisement)
                and advertisement.has_probe
                and not advertisement.is_repeated
            ):
                serial_number = advertisement.serial_number
                if (
                    serial_number in self._plan().direct
                    and serial_number not in self._retiring
                ):
                    self.prediction_for(
                        self.meatnet.probes[serial_number]
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
        if self.repeater is not None and not self._predictions_stopped:
            self._async_manage_connections(self.repeater)

    def _plan(self) -> ConnectionPlan:
        """Ask the library which prediction connections to hold."""
        repeater = self.repeater
        return plan_connections(
            self.meatnet,
            repeater=repeater.session if repeater is not None else None,
            failed=repeater.failed_addresses if repeater is not None else (),
        )

    @callback
    def _async_manage_connections(self, repeater: RepeaterMonitor) -> None:
        """Keep a repeater connected, and release direct connections it makes redundant."""
        plan = self._plan()
        if repeater.can_connect:
            for address in plan.repeaters:
                if address in repeater.failed_addresses:
                    # All untried candidates were unreachable. Start a fresh round so failed
                    # repeaters can recover without starving lower-ranked candidates.
                    repeater.reset_failures()
                if repeater.async_connect(address):
                    break
        for serial_number, monitor in list(self._retiring.items()):
            if (
                serial_number not in self._retiring_tasks
                and monitor.cleanup_retry_ready
            ):
                self._async_release_direct(serial_number, monitor)
        for serial_number in plan.release & self.predictions.keys():
            monitor = self.predictions.pop(serial_number)
            probe = self.meatnet.probes[serial_number]
            LOGGER.debug(
                "Releasing direct connection to %s: a MeatNet repeater reports it",
                probe,
            )
            self._retiring[serial_number] = monitor
            self._async_release_direct(serial_number, monitor)

    @callback
    def _async_release_direct(
        self, serial_number: int, monitor: PredictionMonitor
    ) -> None:
        """Release a redundant connection, retaining failed cleanup for retry and unload."""

        async def release() -> None:
            try:
                if await monitor.async_stop():
                    self._retiring.pop(serial_number)
            finally:
                self._retiring_tasks.pop(serial_number)

        # Bounded by the monitor's cleanup deadlines, so it is safe to track.
        self._retiring_tasks[serial_number] = self.hass.async_create_task(
            release(),
            f"Combustion release {serial_number:08X}",
            eager_start=False,
        )

    @callback
    def prediction_for(self, probe: Probe) -> PredictionMonitor:
        """Get a probe's prediction monitor."""
        if probe.serial_number not in self.predictions:
            self.predictions[probe.serial_number] = PredictionMonitor(
                self.hass, self.meatnet, probe
            )
        return self.predictions[probe.serial_number]

    async def async_stop_predictions(self) -> None:
        """Release all prediction connections during unload.

        Advertisements keep arriving until the entry's unload callbacks stop listening, which
        happens after this returns. Stop starting monitors first, so none is created or connects
        while existing ones are being stopped.
        """
        self._predictions_stopped = True
        # Stopped in turn, in this task, so a connection that has not started yet is cancelled
        # before it can connect.
        for monitor in list(self.predictions.values()):
            await monitor.async_stop()
        if self.repeater is not None:
            await self.repeater.async_stop()
        if self._retiring_tasks:
            await asyncio.gather(*self._retiring_tasks.values(), return_exceptions=True)
        for serial_number, monitor in list(self._retiring.items()):
            if await monitor.async_stop():
                self._retiring.pop(serial_number)
