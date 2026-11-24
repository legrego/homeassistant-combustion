"""Combustion devices as entities see them.

Entities read a device's values here, rather than from the library that reported them, in terms
that don't depend on how they were reported. A probe can be reported over Bluetooth, by MeatNet
Cloud, or both.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Hashable
from dataclasses import dataclass
from time import monotonic

from combustion_ble import (
    BatteryStatus,
    CookingPrediction,
    FoodSafe,
    FoodSafeMode,
    FoodSafeState,
    Gauge,
    PredictionMode,
    PredictionState,
    Probe,
)
from combustion_cloud import DeviceIdentity, DeviceStatus, Prediction
from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.util.dt import utcnow

from .const import (
    BLUETOOTH_PREFERENCE,
    CLOUD_CLOCK_SKEW,
    CLOUD_PREDICTION_TIMEOUT,
    GAUGE_MODEL,
    LOGGER,
    PROBE_MODEL,
    SOURCE_BLUETOOTH,
    SOURCE_CLOUD,
    STALE_TIMEOUT,
)

PREDICTION_STATES = {
    PredictionState.PROBE_NOT_INSERTED: "probe_not_inserted",
    PredictionState.PROBE_INSERTED: "probe_inserted",
    PredictionState.COOKING: "warming",
    PredictionState.PREDICTING: "predicting",
    PredictionState.REMOVAL_PREDICTION_DONE: "ready",
}
PREDICTION_STATUSES = ["idle", *PREDICTION_STATES.values()]

FOOD_SAFETY_STATES = {
    FoodSafeState.NOT_SAFE: "not_safe",
    FoodSafeState.SAFE: "safe",
    FoodSafeState.SAFETY_IMPOSSIBLE: "safety_impossible",
}
FOOD_SAFETY_STATUSES = ["off", *FOOD_SAFETY_STATES.values()]

ACTIVE_PREDICTION_MODES = (
    PredictionMode.TIME_TO_REMOVAL,
    PredictionMode.REMOVAL_AND_RESTING,
)

# The cloud uses the same names as the library's enums.
CLOUD_PREDICTION_STATES = {
    state.name: status for state, status in PREDICTION_STATES.items()
}
CLOUD_PREDICTION_MODES = {mode.name for mode in ACTIVE_PREDICTION_MODES}
CLOUD_FOOD_SAFETY_STATES = {
    state.name: status for state, status in FOOD_SAFETY_STATES.items()
}
CLOUD_LOW_BATTERY = {"OK": False, "LOW_BATTERY": True}
CLOUD_THERMISTORS = {f"T{number}": number for number in range(1, 9)}


@dataclass(frozen=True, slots=True)
class ProbeReadings:
    """A probe's temperatures in Celsius, and which thermistors its virtual sensors read from."""

    temperatures: tuple[float | None, ...] | None = None
    """Readings for T1-T8, or ``None`` when none have been reported."""
    core_thermistor: int | None = None
    surface_thermistor: int | None = None
    ambient_thermistor: int | None = None

    def thermistor(self, number: int | None) -> float | None:
        """Return the reading of thermistor ``number`` (1-8)."""
        if self.temperatures is None or number is None:
            return None
        # A cloud sample could hold fewer readings.
        return (
            self.temperatures[number - 1] if number <= len(self.temperatures) else None
        )

    @property
    def core(self) -> float | None:
        """Virtual core temperature."""
        return self.thermistor(self.core_thermistor)

    @property
    def surface(self) -> float | None:
        """Virtual surface temperature."""
        return self.thermistor(self.surface_thermistor)

    @property
    def ambient(self) -> float | None:
        """Virtual ambient temperature."""
        return self.thermistor(self.ambient_thermistor)


@dataclass(frozen=True, slots=True)
class ProbePrediction:
    """A probe's cooking prediction."""

    status: str | None
    """One of :data:`PREDICTION_STATUSES`, or ``None`` for a state this integration doesn't know."""
    target: float | None
    """Cooking target in Celsius, or ``None`` when no supported target is set."""
    seconds_remaining: int | None


@dataclass(frozen=True, slots=True)
class ProbeFoodSafety:
    """A probe's SafeCook progress. Everything but the status is unknown while SafeCook is off."""

    status: str | None
    """One of :data:`FOOD_SAFETY_STATUSES`, or ``None`` for a state this integration doesn't know."""
    threshold_temperature: float | None = None
    """``None`` when the program leaves the threshold to the firmware's product rules."""
    seconds_above_threshold: int | None = None
    log_reduction: float | None = None
    """Integrated mode only, as is the target."""
    target_log_reduction: float | None = None


type Revisions = tuple[str | None, str | None]
"""Firmware and hardware revisions."""


class CombustionDevice(ABC):
    """A Combustion device with entities."""

    model: str

    def __init__(self, identifier: str, serial_number: str) -> None:
        """Initialize."""
        self.identifier = identifier
        """Device registry identifier, which prefixes the device's entities' unique IDs."""
        self.serial_number = serial_number
        """Serial number as printed on the device."""
        self._listeners: list[CALLBACK_TYPE] = []

    @property
    @abstractmethod
    def bluetooth(self) -> Probe | Gauge | None:
        """The device as heard over Bluetooth, if it has been since Home Assistant started."""

    @property
    @abstractmethod
    def available(self) -> bool:
        """Whether the device has been reported recently."""

    @property
    def rssi(self) -> int | None:
        """Signal strength of the device's own advertisements, while they are heard."""
        heard = self.bluetooth
        return heard.rssi if heard is not None and heard.available else None

    @property
    def revisions(self) -> dict[str, Revisions]:
        """The device's firmware and hardware revisions, from each source that knows them."""
        if (heard := self.bluetooth) is None:
            return {}
        firmware = heard.firmware_version
        if firmware is None and heard.hardware_revision is None:
            return {}
        return {
            SOURCE_BLUETOOTH: (
                None if firmware is None else firmware.raw,
                heard.hardware_revision,
            )
        }

    @property
    @abstractmethod
    def low_battery(self) -> bool | None:
        """Whether the device's battery is low."""

    @callback
    def async_add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Call ``listener`` whenever the device's state changes."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def _async_notify(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:  # One entity's error must not keep others from updating.
                LOGGER.exception("Error updating %s", self)


class ProbeDevice(CombustionDevice):
    """A Predictive Thermometer, reported over Bluetooth, by MeatNet Cloud, or both.

    Readings come from one source at a time, so temperatures never mix with the other source's
    choice of virtual sensors. Predictions and SafeCook progress choose separately, as Bluetooth
    only reports them through a connection.
    """

    model = PROBE_MODEL

    def __init__(self, serial_number: int) -> None:
        """Initialize a probe that nothing has reported yet."""
        super().__init__(probe_identifier(serial_number), f"{serial_number:08X}")
        self.identity = DeviceIdentity.probe(serial_number)
        """The probe's identity in MeatNet Cloud."""
        self._bluetooth: Probe | None = None
        self._cloud: DeviceStatus | None = None
        self._notified = self._state()

    def __repr__(self) -> str:
        """Describe the probe in logs."""
        return f"Probe({self.serial_number})"

    @property
    def bluetooth(self) -> Probe | None:
        """The probe as heard over Bluetooth, which is what commands are sent to."""
        return self._bluetooth

    @callback
    def async_set_bluetooth(self, probe: Probe) -> None:
        """Follow the probe's Bluetooth state, once it has been heard."""
        self._bluetooth = probe
        probe.add_listener(self._async_notify)
        self._async_notify()

    @property
    def cloud(self) -> DeviceStatus | None:
        """The newest sample from MeatNet Cloud, however old, while the probe is linked."""
        return self._cloud

    @callback
    def async_set_cloud(self, status: DeviceStatus | None) -> None:
        """Use a sample from MeatNet Cloud, or stop using any."""
        self._cloud = status
        self.async_refresh()

    @callback
    def async_refresh(self) -> None:
        """Notify listeners if the passing of time, or a new sample, has changed anything."""
        if self._state() != self._notified:
            self._async_notify()

    @callback
    def _async_notify(self) -> None:
        self._notified = self._state()
        super()._async_notify()

    def _state(self) -> Hashable:
        """Return what entities show, other than what only Bluetooth reports."""
        return (
            self.source,
            self.low_battery,
            self.readings,
            self.prediction,
            self.food_safety,
        )

    @property
    def bluetooth_age(self) -> float | None:
        """Seconds since the probe was heard over Bluetooth, or ``None`` if not recently."""
        probe = self._bluetooth
        if probe is None or not probe.available:
            return None
        return monotonic() - probe.last_seen

    @property
    def cloud_age(self) -> float | None:
        """Age of the cloud sample in seconds, or ``None`` if there is no usable one."""
        status = self._cloud
        if status is None or (sampled_at := status.sampled_at) is None:
            return None
        if (sampled_at - status.received_at).total_seconds() > CLOUD_CLOCK_SKEW:
            return None
        # A sample was published, and then received, some time after it was taken. An uploader
        # whose clock is ahead must not make it seem newer than that.
        at = min(sampled_at, status.received_at, status.published_at or sampled_at)
        return max((utcnow() - at).total_seconds(), 0)

    @property
    def source(self) -> str | None:
        """Where readings come from, or ``None`` when neither source has recent ones."""
        bluetooth_age = self.bluetooth_age
        cloud_age = self.cloud_age
        if cloud_age is None or cloud_age > STALE_TIMEOUT:
            return None if bluetooth_age is None else SOURCE_BLUETOOTH
        if bluetooth_age is None:
            return SOURCE_CLOUD
        if bluetooth_age <= BLUETOOTH_PREFERENCE or bluetooth_age <= cloud_age:
            return SOURCE_BLUETOOTH
        return SOURCE_CLOUD

    @property
    def available(self) -> bool:
        """Whether the probe has been heard recently, directly, through MeatNet, or by the cloud."""
        return self.source is not None

    @property
    def revisions(self) -> dict[str, Revisions]:
        """The probe's firmware and hardware revisions, from each source that knows them.

        An old cloud sample could be from before an update.
        """
        result = super().revisions
        status, age = self._cloud, self.cloud_age
        if status is not None and age is not None and age <= STALE_TIMEOUT:
            revisions = (
                status.firmware_version or None,
                status.hardware_revision or None,
            )
            if any(revisions):
                result[SOURCE_CLOUD] = revisions
        return result

    @property
    def low_battery(self) -> bool | None:
        """Whether the probe's battery is low."""
        if self.source == SOURCE_CLOUD and self._cloud is not None:
            return CLOUD_LOW_BATTERY.get(self._cloud.battery_status or "")
        if self._bluetooth is None:
            return None
        return self._bluetooth.battery_status == BatteryStatus.LOW

    @property
    def readings(self) -> ProbeReadings:
        """The probe's temperatures."""
        if self.source == SOURCE_CLOUD and self._cloud is not None:
            return _cloud_readings(self._cloud)
        probe = self._bluetooth
        if probe is None:
            return ProbeReadings()
        return ProbeReadings(
            probe.temperatures,
            # Members are named after their thermistor, e.g. VirtualSurfaceSensor.T6.
            int(probe.core_sensor.name[1:]),
            int(probe.surface_sensor.name[1:]),
            int(probe.ambient_sensor.name[1:]),
        )

    @property
    def instant_read(self) -> float | None:
        """Instant read temperature in Celsius, while the probe is in instant read mode.

        The cloud doesn't say whether a probe is.
        """
        return None if self._bluetooth is None else self._bluetooth.instant_read_celsius

    @property
    def prediction(self) -> ProbePrediction | None:
        """The probe's prediction, or ``None`` while nothing reports it."""
        if self._bluetooth is not None and self._bluetooth.prediction is not None:
            return _bluetooth_prediction(self._bluetooth.prediction)
        if (recent := self._recent_cloud) is None:
            return None
        status, age = recent
        return _cloud_prediction(status.prediction, age)

    @property
    def food_safety(self) -> ProbeFoodSafety | None:
        """The probe's SafeCook progress, or ``None`` while nothing reports it."""
        if self._bluetooth is not None and self._bluetooth.food_safe is not None:
            return _bluetooth_food_safety(self._bluetooth.food_safe)
        return (
            None
            if (recent := self._recent_cloud) is None
            else _cloud_food_safety(recent[0])
        )

    @property
    def _recent_cloud(self) -> tuple[DeviceStatus, float] | None:
        """The cloud sample and its age, if it is recent enough for predictions."""
        status, age = self._cloud, self.cloud_age
        if status is None or age is None or age >= CLOUD_PREDICTION_TIMEOUT:
            return None
        return status, age


class GaugeDevice(CombustionDevice):
    """A Giant Grill Gauge."""

    model = GAUGE_MODEL

    def __init__(self, bluetooth: Gauge) -> None:
        """Initialize."""
        super().__init__(bluetooth.serial_number, bluetooth.serial_number)
        self._bluetooth = bluetooth
        bluetooth.add_listener(self._async_notify)

    def __repr__(self) -> str:
        """Describe the gauge in logs."""
        return repr(self._bluetooth)

    @property
    def bluetooth(self) -> Gauge:
        """The gauge as heard over Bluetooth."""
        return self._bluetooth

    @property
    def available(self) -> bool:
        """Whether the gauge has been heard recently, directly or through MeatNet."""
        return self._bluetooth.available

    @property
    def low_battery(self) -> bool:
        """Whether the gauge's battery is low."""
        return self._bluetooth.low_battery

    @property
    def temperature(self) -> float | None:
        """Gauge temperature in Celsius, unknown when no sensor is plugged into the gauge."""
        return self._bluetooth.temperature


def probe_identifier(serial_number: int) -> str:
    """Return a probe's device registry identifier: lowercase hex without leading zeros.

    Earlier versions of this integration used the same, so existing entities keep their history.
    """
    return f"{serial_number:x}"


def _bluetooth_prediction(prediction: CookingPrediction) -> ProbePrediction:
    active = prediction.mode in ACTIVE_PREDICTION_MODES
    return ProbePrediction(
        status=PREDICTION_STATES.get(prediction.state) if active else "idle",
        target=prediction.set_point_temperature if active else None,
        seconds_remaining=prediction.seconds_remaining,
    )


def _bluetooth_food_safety(food_safe: FoodSafe) -> ProbeFoodSafety:
    if not food_safe.program_set:
        return ProbeFoodSafety("off")
    # Simplified mode always reports 0 log reductions, which would read as no progress.
    integrated = food_safe.mode is FoodSafeMode.INTEGRATED
    return ProbeFoodSafety(
        # Unknown states are kept as plain integers.
        status=(
            FOOD_SAFETY_STATES[food_safe.state]
            if isinstance(food_safe.state, FoodSafeState)
            else None
        ),
        # Simplified programs written without a threshold, as Combustion's Android library and
        # this integration's action do, leave the threshold to the firmware's product rules.
        threshold_temperature=food_safe.threshold_temperature or None,
        seconds_above_threshold=food_safe.seconds_above_threshold,
        log_reduction=food_safe.log_reduction if integrated else None,
        target_log_reduction=food_safe.target_log_reduction if integrated else None,
    )


def _cloud_readings(status: DeviceStatus) -> ProbeReadings:
    def thermistor(sensor: str) -> int | None:
        return CLOUD_THERMISTORS.get(str(status.virtual_sensors.get(sensor)))

    return ProbeReadings(
        status.temperatures or None,
        thermistor("virtual_core"),
        thermistor("virtual_surface"),
        thermistor("virtual_ambient"),
    )


def _cloud_prediction(prediction: Prediction, age: float) -> ProbePrediction | None:
    if prediction.mode is None:
        return None
    active = prediction.mode in CLOUD_PREDICTION_MODES
    remaining = prediction.remaining_seconds
    return ProbePrediction(
        status=(
            CLOUD_PREDICTION_STATES.get(prediction.state or "") if active else "idle"
        ),
        target=prediction.setpoint if active else None,
        # Time has passed since the sample was taken.
        seconds_remaining=None if remaining is None else max(0, remaining - int(age)),
    )


def _cloud_food_safety(status: DeviceStatus) -> ProbeFoodSafety | None:
    program, progress = status.food_safe_data, status.food_safe_status
    if program is None:
        return None
    if not program.program_set:
        return ProbeFoodSafety("off")
    if progress is None:
        return None
    criteria = program.completion_criteria
    integrated = program.mode == FoodSafeMode.INTEGRATED.name
    return ProbeFoodSafety(
        status=CLOUD_FOOD_SAFETY_STATES.get(progress.state),
        threshold_temperature=(
            None if criteria is None else criteria.threshold_temperature or None
        ),
        seconds_above_threshold=progress.seconds_above_threshold,
        log_reduction=progress.log_reduction if integrated else None,
        target_log_reduction=(
            criteria.target_log_reduction
            if integrated and criteria is not None
            else None
        ),
    )
