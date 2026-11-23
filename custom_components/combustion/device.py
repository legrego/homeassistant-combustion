"""Combustion devices as entities see them.

Entities read a device's values here, rather than from the library that reported them, in terms
that don't depend on how they were reported.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

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
from homeassistant.core import CALLBACK_TYPE, callback

from .const import GAUGE_MODEL, PROBE_MODEL

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


@dataclass(frozen=True, slots=True)
class ProbeReadings:
    """A probe's temperatures in Celsius, and which thermistors its virtual sensors read from."""

    temperatures: tuple[float | None, ...] | None
    """Readings for T1-T8, or ``None`` when none have been reported."""
    core_thermistor: int
    surface_thermistor: int
    ambient_thermistor: int

    def thermistor(self, number: int) -> float | None:
        """Return the reading of thermistor ``number`` (1-8)."""
        return None if self.temperatures is None else self.temperatures[number - 1]

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


class CombustionDevice(ABC):
    """A Combustion device with entities."""

    model: str

    def __init__(self, identifier: str, serial_number: str) -> None:
        """Initialize."""
        self.identifier = identifier
        """Device registry identifier, which prefixes the device's entities' unique IDs."""
        self.serial_number = serial_number
        """Serial number as printed on the device."""

    @property
    @abstractmethod
    def bluetooth(self) -> Probe | Gauge:
        """The device as heard over Bluetooth."""

    @property
    def available(self) -> bool:
        """Whether the device has been heard recently, directly or through MeatNet."""
        return self.bluetooth.available

    @property
    def rssi(self) -> int | None:
        """Signal strength of the device's own advertisements."""
        return self.bluetooth.rssi

    @property
    @abstractmethod
    def low_battery(self) -> bool:
        """Whether the device's battery is low."""

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Call ``listener`` whenever the device's state changes."""
        return self.bluetooth.add_listener(listener)


class ProbeDevice(CombustionDevice):
    """A Predictive Thermometer."""

    model = PROBE_MODEL

    def __init__(self, bluetooth: Probe) -> None:
        """Initialize."""
        super().__init__(device_identifier(bluetooth), bluetooth.serial_number_string)
        self._bluetooth = bluetooth

    @property
    def bluetooth(self) -> Probe:
        """The probe as heard over Bluetooth, which is what commands are sent to."""
        return self._bluetooth

    @property
    def low_battery(self) -> bool:
        """Whether the probe's battery is low."""
        return self._bluetooth.battery_status == BatteryStatus.LOW

    @property
    def readings(self) -> ProbeReadings:
        """The probe's temperatures."""
        probe = self._bluetooth
        return ProbeReadings(
            probe.temperatures,
            # Members are named after their thermistor, e.g. VirtualSurfaceSensor.T6.
            int(probe.core_sensor.name[1:]),
            int(probe.surface_sensor.name[1:]),
            int(probe.ambient_sensor.name[1:]),
        )

    @property
    def instant_read(self) -> float | None:
        """Instant read temperature in Celsius, while the probe is in instant read mode."""
        return self._bluetooth.instant_read_celsius

    @property
    def prediction(self) -> ProbePrediction | None:
        """The probe's prediction, or ``None`` while nothing reports it."""
        prediction = self._bluetooth.prediction
        return None if prediction is None else _bluetooth_prediction(prediction)

    @property
    def food_safety(self) -> ProbeFoodSafety | None:
        """The probe's SafeCook progress, or ``None`` while nothing reports it."""
        food_safe = self._bluetooth.food_safe
        return None if food_safe is None else _bluetooth_food_safety(food_safe)


class GaugeDevice(CombustionDevice):
    """A Giant Grill Gauge."""

    model = GAUGE_MODEL

    def __init__(self, bluetooth: Gauge) -> None:
        """Initialize."""
        super().__init__(device_identifier(bluetooth), bluetooth.serial_number)
        self._bluetooth = bluetooth

    @property
    def bluetooth(self) -> Gauge:
        """The gauge as heard over Bluetooth."""
        return self._bluetooth

    @property
    def low_battery(self) -> bool:
        """Whether the gauge's battery is low."""
        return self._bluetooth.low_battery

    @property
    def temperature(self) -> float | None:
        """Gauge temperature in Celsius, unknown when no sensor is plugged into the gauge."""
        return self._bluetooth.temperature


def device_identifier(device: Probe | Gauge) -> str:
    """Return the device registry identifier for a device heard over Bluetooth.

    Probes use their serial number in lowercase hex without leading zeros, as earlier versions of
    this integration did, so existing entities keep their history.
    """
    if isinstance(device, Probe):
        return f"{device.serial_number:x}"
    return device.serial_number


def _bluetooth_prediction(prediction: CookingPrediction) -> ProbePrediction:
    active = prediction.mode in (
        PredictionMode.TIME_TO_REMOVAL,
        PredictionMode.REMOVAL_AND_RESTING,
    )
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
