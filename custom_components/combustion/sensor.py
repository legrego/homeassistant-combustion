"""Sensors for Combustion probes and Giant Grill Gauges."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    DOMAIN as SENSOR_DOMAIN,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .coordinator import CombustionConfigEntry
from .device import (
    FOOD_SAFETY_STATUSES,
    PREDICTION_STATUSES,
    CombustionDevice,
    ProbeDevice,
    ProbeFoodSafety,
    ProbePrediction,
)
from .entity import CombustionEntity, async_remove_entities

# Entities only update from pushed advertisements.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class CombustionSensorEntityDescription(SensorEntityDescription):
    """Describes a Combustion sensor."""

    value_fn: Callable[[Any], StateType]
    thermistor_fn: Callable[[ProbeDevice], int] | None = None
    """For virtual sensors: which thermistor (1-8) the reading comes from."""


def _temperature(
    key: str, value_fn: Callable[[Any], StateType], **kwargs: Any
) -> CombustionSensorEntityDescription:
    return CombustionSensorEntityDescription(
        **{
            "key": key,
            "device_class": SensorDeviceClass.TEMPERATURE,
            "native_unit_of_measurement": UnitOfTemperature.CELSIUS,
            "state_class": SensorStateClass.MEASUREMENT,
            "suggested_display_precision": 1,
            "value_fn": value_fn,
            **kwargs,
        }
    )


def _thermistor_reading(number: int) -> Callable[[ProbeDevice], float | None]:
    def value(probe: ProbeDevice) -> float | None:
        return probe.readings.thermistor(number)

    return value


_RSSI = CombustionSensorEntityDescription(
    key="rssi",
    translation_key="rssi",
    device_class=SensorDeviceClass.SIGNAL_STRENGTH,
    native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    state_class=SensorStateClass.MEASUREMENT,
    entity_category=EntityCategory.DIAGNOSTIC,
    entity_registry_enabled_default=False,
    value_fn=lambda device: device.rssi,
)

PROBE_SENSORS: tuple[CombustionSensorEntityDescription, ...] = (
    _temperature(
        "sensor--core",
        lambda probe: probe.readings.core,
        translation_key="core_temperature",
        thermistor_fn=lambda probe: probe.readings.core_thermistor,
    ),
    _temperature(
        "sensor--surface",
        lambda probe: probe.readings.surface,
        translation_key="surface_temperature",
        thermistor_fn=lambda probe: probe.readings.surface_thermistor,
    ),
    _temperature(
        "sensor--ambient",
        lambda probe: probe.readings.ambient,
        translation_key="ambient_temperature",
        thermistor_fn=lambda probe: probe.readings.ambient_thermistor,
    ),
    *(
        _temperature(
            f"thermistor--{number}",
            _thermistor_reading(number),
            translation_key="thermistor_temperature",
            translation_placeholders={"number": str(number)},
            entity_registry_enabled_default=False,
        )
        for number in range(1, 9)
    ),
    # Smoothed the same way Combustion's apps show it. Unknown unless the probe is in instant read
    # mode.
    _temperature(
        "instant_read",
        lambda probe: probe.instant_read,
        translation_key="instant_read_temperature",
        suggested_display_precision=0,
    ),
    # Signal strength of the probe's own advertisements. Unknown when only heard through MeatNet.
    _RSSI,
)

GAUGE_SENSORS: tuple[CombustionSensorEntityDescription, ...] = (
    # Named after its device class. Unknown when no sensor is plugged into the gauge.
    _temperature("temperature", lambda gauge: gauge.temperature),
    _RSSI,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CombustionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add sensors for each probe and gauge as it is discovered."""
    # The cooking target became a number entity, and the SafeCook product and serving became
    # selects, which can also set them.
    removed = {"prediction_target", "food_safety_product", "food_safety_serving"}
    if not entry.runtime_data.enable_predictions:
        removed.update(description.key for description in CONNECTED_SENSORS)
    async_remove_entities(hass, entry.entry_id, SENSOR_DOMAIN, removed)

    @callback
    def _async_add_device(device: CombustionDevice) -> None:
        if isinstance(device, ProbeDevice):
            descriptions = PROBE_SENSORS
            if entry.runtime_data.enable_predictions:
                descriptions += CONNECTED_SENSORS
        else:
            descriptions = GAUGE_SENSORS
        async_add_entities(
            CombustionSensor(device, description) for description in descriptions
        )

    entry.async_on_unload(
        entry.runtime_data.async_add_device_listener(_async_add_device)
    )


class CombustionSensor(CombustionEntity, SensorEntity):
    """A Combustion sensor."""

    entity_description: CombustionSensorEntityDescription

    @property
    def native_value(self) -> StateType:
        """Return the sensor's value."""
        return self.entity_description.value_fn(self._device)

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return which thermistor a virtual sensor reads from."""
        if (thermistor_fn := self.entity_description.thermistor_fn) is None:
            return None
        return {"thermistor_id": thermistor_fn(self._device)}


def _prediction_value(
    fn: Callable[[ProbePrediction], StateType],
) -> Callable[[ProbeDevice], StateType]:
    """Read from the probe's prediction. Unknown while nothing reports it."""

    def value(probe: ProbeDevice) -> StateType:
        prediction = probe.prediction
        return None if prediction is None else fn(prediction)

    return value


PREDICTION_SENSORS = (
    # The cooking target is a number entity.
    CombustionSensorEntityDescription(
        key="prediction_remaining",
        translation_key="prediction_remaining",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_display_precision=0,
        value_fn=_prediction_value(lambda prediction: prediction.seconds_remaining),
    ),
    CombustionSensorEntityDescription(
        key="prediction_status",
        translation_key="prediction_status",
        device_class=SensorDeviceClass.ENUM,
        options=PREDICTION_STATUSES,
        value_fn=_prediction_value(lambda prediction: prediction.status),
    ),
)


def _food_safety_value(
    fn: Callable[[ProbeFoodSafety], StateType],
) -> Callable[[ProbeDevice], StateType]:
    """Read from the probe's SafeCook progress. Unknown while nothing reports it."""

    def value(probe: ProbeDevice) -> StateType:
        food_safety = probe.food_safety
        return None if food_safety is None else fn(food_safety)

    return value


FOOD_SAFETY_SENSORS = (
    CombustionSensorEntityDescription(
        key="food_safety",
        translation_key="food_safety",
        device_class=SensorDeviceClass.ENUM,
        options=FOOD_SAFETY_STATUSES,
        value_fn=_food_safety_value(lambda food_safety: food_safety.status),
    ),
    _temperature(
        "food_safety_threshold",
        _food_safety_value(lambda food_safety: food_safety.threshold_temperature),
        translation_key="food_safety_threshold",
        # Part of the program, not a measurement.
        state_class=None,
    ),
    CombustionSensorEntityDescription(
        key="food_safety_time_above_threshold",
        translation_key="food_safety_time_above_threshold",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_display_precision=0,
        value_fn=_food_safety_value(
            lambda food_safety: food_safety.seconds_above_threshold
        ),
    ),
    CombustionSensorEntityDescription(
        key="food_safety_log_reduction",
        translation_key="food_safety_log_reduction",
        suggested_display_precision=1,
        value_fn=_food_safety_value(lambda food_safety: food_safety.log_reduction),
    ),
    CombustionSensorEntityDescription(
        key="food_safety_target_log_reduction",
        translation_key="food_safety_target_log_reduction",
        suggested_display_precision=1,
        value_fn=_food_safety_value(
            lambda food_safety: food_safety.target_log_reduction
        ),
    ),
)

CONNECTED_SENSORS = PREDICTION_SENSORS + FOOD_SAFETY_SENSORS
"""Sensors that need a connection, enabled by the predictions option."""
