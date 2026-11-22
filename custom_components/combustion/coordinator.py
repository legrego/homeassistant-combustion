"""Feed Home Assistant's Bluetooth advertisements into a combustion_ble MeatNet."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import monotonic

from bleak_retry_connector import BLEAK_RETRY_EXCEPTIONS
from combustion_ble import (
    COMBUSTION_MANUFACTURER_ID,
    ConnectionPlan,
    Device,
    FoodSafe,
    FoodSafeProgram,
    Gauge,
    MeatNet,
    NodeSession,
    Probe,
    ProbeAdvertisement,
    ProbeSession,
    RequestRejectedError,
    parse_advertisement,
    plan_connections,
)
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN, LOGGER, STALE_TIMEOUT
from .entity import device_identifier
from .food_safety import Program, product_option, serving_option
from .prediction import NodeCommand, PredictionMonitor, ProbeCommand, RepeaterMonitor
from .revisions import RevisionScheduler

EXPIRE_INTERVAL = timedelta(seconds=1)

PENDING_FOOD_SAFE_TIMEOUT = 10
"""Seconds a written SafeCook program stands in for the probe's status until the probe reports it.

Probes report status about once a second; this also covers a repeater relaying it late.
"""

type CombustionConfigEntry = ConfigEntry[CombustionCoordinator]


class CombustionCoordinator:
    """Keeps a MeatNet up to date with advertisements from Home Assistant's Bluetooth integration.

    Home Assistant owns all scanning. Temperature readings work with passive adapters and proxies.
    Optional predictions use connectable adapters: one connection to a MeatNet repeater, which
    relays predictions for every probe on its network, and direct connections only to probes the
    repeater is not reporting.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry_id: str,
        *,
        enable_predictions: bool = False,
    ) -> None:
        """Initialize."""
        self.hass = hass
        self.config_entry_id = config_entry_id
        self.enable_predictions = enable_predictions
        self.predictions: dict[int, PredictionMonitor] = {}
        """Direct probe connections, by probe serial number."""
        self._predictions_stopped = False
        self.meatnet = MeatNet(stale_timeout=STALE_TIMEOUT)
        self.repeater = (
            RepeaterMonitor(hass, self.meatnet) if enable_predictions else None
        )
        self.revisions = RevisionScheduler(hass, self.meatnet)
        # Direct connections being released because the repeater reports their probes. A probe
        # gets no new direct connection until its old session has let go of the probe.
        self._retiring: dict[int, PredictionMonitor] = {}
        self._retiring_tasks: dict[int, asyncio.Task] = {}
        self._handled_times: dict[str, float] = {}
        self._connection_listeners: list[CALLBACK_TYPE] = []
        # SafeCook programs written but not yet reported, by probe serial number.
        self._pending_food_safe: dict[int, _PendingProgram] = {}
        self._food_safe_locks: dict[int, asyncio.Lock] = {}
        self.meatnet.add_device_listener(self._async_log_availability)
        self.meatnet.add_device_listener(self._async_track_revisions)

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
    def _async_track_revisions(self, device: Device) -> None:
        """Copy firmware and hardware revisions to the device registry once they are read.

        Revisions are only read through prediction connections. The registry keeps the last ones
        read, so they still show after a restart or with predictions turned off.
        """
        if not isinstance(device, Probe | Gauge):
            return
        identifier = (DOMAIN, device_identifier(device))
        recorded: tuple[str | None, str | None] = (None, None)

        @callback
        def _async_on_change() -> None:
            # Listeners run for every advertisement; revisions change rarely.
            nonlocal recorded
            firmware = device.firmware_version
            revisions = (
                None if firmware is None else firmware.raw,
                device.hardware_revision,
            )
            if revisions == recorded:
                return
            registry = dr.async_get(self.hass)
            entry = registry.async_get_device_by_identifier(
                identifier, self.config_entry_id
            )
            if entry is None:
                return
            recorded = revisions
            changes: dict[str, str] = {}
            if (firmware := device.firmware_version) is not None and (
                str(firmware) != entry.sw_version
            ):
                changes["sw_version"] = str(firmware)
            if (hardware := device.hardware_revision) is not None and (
                hardware != entry.hw_version
            ):
                changes["hw_version"] = hardware
            if changes:
                registry.async_update_device(entry.id, **changes)

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
        if self._predictions_stopped:
            return
        plan = None
        if self.repeater is not None:
            self._async_manage_connections(self.repeater)
            # Connections start and end without any change to the probes, so controls that
            # depend on a connection check it each tick.
            for listener in list(self._connection_listeners):
                listener()
            plan = self._plan()
        self.revisions.async_tick(
            repeater=self.repeater, direct=self.predictions, plan=plan
        )

    @callback
    def async_add_connection_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Call ``listener`` every tick while predictions run, to follow connections."""
        self._connection_listeners.append(listener)
        return lambda: self._connection_listeners.remove(listener)

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
            # A brief revision connection gives way to predictions. Its device can't take a
            # second session, so it is passed over until the brief connection releases it.
            held = self.revisions.held_address
            if held in plan.repeaters:
                self.revisions.async_cancel_brief()
            for address in plan.repeaters:
                if address == held:
                    continue
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
    def can_command(self, probe: Probe) -> bool:
        """Whether a running connection can send the probe cooking target and SafeCook commands."""
        return self._command_route(probe) is not None

    @callback
    def _command_route(
        self, probe: Probe
    ) -> RepeaterMonitor | PredictionMonitor | None:
        """Pick the connection for a probe's commands: the one supplying its predictions."""
        serial_number = probe.serial_number
        repeater = self.repeater
        if (
            repeater is not None
            and repeater.session is not None
            and serial_number in self._plan().release
        ):
            return repeater
        monitor = self.predictions.get(serial_number)
        if monitor is not None and monitor.session is not None:
            return monitor
        return None

    async def async_set_prediction(self, probe: Probe, celsius: float | None) -> None:
        """Set a probe's removal target in Celsius, or cancel it with ``None``.

        Returns once the probe acknowledges the command. Its predictions report the new target
        with their next status update.
        """
        if celsius is None:
            direct: ProbeCommand = ProbeSession.cancel_prediction
            relayed: NodeCommand = NodeSession.cancel_prediction
        else:

            async def direct(session: ProbeSession) -> None:
                await session.set_removal_temperature(celsius)

            async def relayed(session: NodeSession, serial_number: int) -> None:
                await session.set_removal_temperature(serial_number, celsius)

        await self._async_command(
            probe, direct, relayed, "command_rejected", "command_timeout"
        )

    @callback
    def food_safe_program(self, probe: Probe) -> Program | None:
        """Return a probe's SafeCook program, or ``None`` while it is off or not reported yet.

        A program written from Home Assistant stands in for the probe's status until the probe
        reports it, so a change made before then builds on it rather than on the old program.
        """
        if (pending := self._pending_food_safe.get(probe.serial_number)) is not None:
            if not pending.expired:
                return pending.program
            self._async_drop_pending(probe.serial_number)
        reported = probe.food_safe
        return reported if reported is not None and reported.program_set else None

    @callback
    def _async_track_pending(
        self, probe: Probe, program: FoodSafeProgram | None
    ) -> None:
        """Hold a written program until the probe reports it, or for a while if it never does."""
        serial_number = probe.serial_number
        self._async_drop_pending(serial_number)

        @callback
        def _async_on_change() -> None:
            if pending.expired or _reports(probe.food_safe, program):
                self._async_drop_pending(serial_number)

        pending = _PendingProgram(
            program, monotonic(), probe.add_listener(_async_on_change)
        )
        self._pending_food_safe[serial_number] = pending

    @callback
    def _async_drop_pending(self, serial_number: int) -> None:
        if (pending := self._pending_food_safe.pop(serial_number, None)) is not None:
            pending.remove_listener()

    async def async_change_food_safe(
        self, probe: Probe, change: Callable[[Program | None], FoodSafeProgram | None]
    ) -> None:
        """Write the program ``change`` makes from the probe's current one.

        Changes to a probe are written one at a time, each seeing the program the last one
        wrote. ``change`` returns ``None`` to turn SafeCook off, and can raise to write nothing.
        Returns once the probe acknowledges the command. Its status reports the new program.
        """
        serial_number = probe.serial_number
        lock = self._food_safe_locks.setdefault(serial_number, asyncio.Lock())
        async with lock:
            program = change(self.food_safe_program(probe))
            if program is None:
                direct: ProbeCommand = ProbeSession.clear_food_safe
                relayed: NodeCommand = NodeSession.clear_food_safe
            else:

                async def direct(session: ProbeSession) -> None:
                    await session.configure_food_safe(program)

                async def relayed(session: NodeSession, serial_number: int) -> None:
                    await session.configure_food_safe(serial_number, program)

            await self._async_command(
                probe, direct, relayed, "food_safety_rejected", "food_safety_timeout"
            )
            self._async_track_pending(probe, program)

    async def async_configure_food_safe(
        self, probe: Probe, program: FoodSafeProgram | None
    ) -> None:
        """Write a probe's SafeCook program, or turn SafeCook off with ``None``."""
        await self.async_change_food_safe(probe, lambda _current: program)

    async def async_reset_food_safe(self, probe: Probe) -> None:
        """Reset a probe's SafeCook progress, keeping its program."""
        await self._async_command(
            probe,
            ProbeSession.reset_food_safe,
            NodeSession.reset_food_safe,
            "food_safety_rejected",
            "food_safety_timeout",
        )

    async def _async_command(
        self,
        probe: Probe,
        direct: ProbeCommand,
        relayed: NodeCommand,
        rejected_key: str,
        timeout_key: str,
    ) -> None:
        """Send a command through the connection supplying the probe's predictions.

        Raises a translated ``HomeAssistantError`` when it can't be delivered or acknowledged.
        """
        route = self._command_route(probe)
        placeholders = {"probe": probe.serial_number_string}
        try:
            if isinstance(route, RepeaterMonitor):
                await route.async_command(probe.serial_number, relayed)
            elif route is not None:
                await route.async_command(direct)
            else:
                raise ConnectionError("No prediction connection")
        except RequestRejectedError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=rejected_key,
                translation_placeholders=placeholders,
            ) from err
        except TimeoutError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=timeout_key,
                translation_placeholders=placeholders,
            ) from err
        except (*BLEAK_RETRY_EXCEPTIONS, OSError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="not_connected",
                translation_placeholders=placeholders,
            ) from err

    @callback
    def prediction_for(self, probe: Probe) -> PredictionMonitor:
        """Get a probe's prediction monitor."""
        if probe.serial_number not in self.predictions:
            self.predictions[probe.serial_number] = PredictionMonitor(
                self.hass, self.meatnet, probe
            )
        return self.predictions[probe.serial_number]

    async def async_stop_connections(self) -> None:
        """Release all prediction and revision connections during unload.

        Advertisements keep arriving until the entry's unload callbacks stop listening, which
        happens after this returns. Stop starting monitors first, so none is created or connects
        while existing ones are being stopped.
        """
        self._predictions_stopped = True
        await self.revisions.async_stop()
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


@dataclass(frozen=True, slots=True)
class _PendingProgram:
    """A SafeCook program written to a probe that its status hasn't reported yet."""

    program: FoodSafeProgram | None
    """``None`` is SafeCook turned off."""
    written_at: float
    remove_listener: Callable[[], None]

    @property
    def expired(self) -> bool:
        return monotonic() - self.written_at >= PENDING_FOOD_SAFE_TIMEOUT


def _reports(reported: FoodSafe | None, program: FoodSafeProgram | None) -> bool:
    """Whether a probe's status reports a program written to it."""
    if reported is None:
        return False
    if program is None:
        return not reported.program_set
    # Options name the mode along with the product.
    return reported.program_set and (
        product_option(reported),
        serving_option(reported),
    ) == (product_option(program), serving_option(program))
