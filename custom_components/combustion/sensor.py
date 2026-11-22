"""Sensors for Combustion probes and Giant Grill Gauges."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from combustion_ble import (
    Device,
    FoodSafe,
    FoodSafeMode,
    FoodSafeState,
    Gauge,
    IntegratedProduct,
    PredictionState,
    Probe,
    Serving,
    SimplifiedProduct,
)
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
from .entity import CombustionEntity, async_remove_entities, prediction_active

# Entities only update from pushed advertisements.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class CombustionSensorEntityDescription(SensorEntityDescription):
    """Describes a Combustion sensor."""

    value_fn: Callable[[Any], StateType]
    thermistor_fn: Callable[[Probe], int] | None = None
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


def _thermistor_reading(number: int) -> Callable[[Probe], float | None]:
    def value(probe: Probe) -> float | None:
        if probe.temperatures is None:
            return None
        return probe.temperatures[number - 1]

    return value


def _thermistor_number(sensor: Any) -> int:
    # Virtual sensor enum members are named after their thermistor, e.g. VirtualSurfaceSensor.T6.
    return int(sensor.name[1:])


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
        lambda probe: probe.core_temperature,
        translation_key="core_temperature",
        thermistor_fn=lambda probe: _thermistor_number(probe.core_sensor),
    ),
    _temperature(
        "sensor--surface",
        lambda probe: probe.surface_temperature,
        translation_key="surface_temperature",
        thermistor_fn=lambda probe: _thermistor_number(probe.surface_sensor),
    ),
    _temperature(
        "sensor--ambient",
        lambda probe: probe.ambient_temperature,
        translation_key="ambient_temperature",
        thermistor_fn=lambda probe: _thermistor_number(probe.ambient_sensor),
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
        lambda probe: probe.instant_read_celsius,
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
    # The cooking target became a number entity, which can also set it.
    removed = {"prediction_target"}
    if not entry.runtime_data.enable_predictions:
        removed.update(description.key for description in CONNECTED_SENSORS)
    async_remove_entities(hass, entry.entry_id, SENSOR_DOMAIN, removed)

    @callback
    def _async_add_device(device: Device) -> None:
        if isinstance(device, Probe):
            descriptions = PROBE_SENSORS
        elif isinstance(device, Gauge):
            descriptions = GAUGE_SENSORS
        else:
            return
        if isinstance(device, Probe) and entry.runtime_data.enable_predictions:
            descriptions += CONNECTED_SENSORS
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


PREDICTION_STATES = {
    PredictionState.PROBE_NOT_INSERTED: "probe_not_inserted",
    PredictionState.PROBE_INSERTED: "probe_inserted",
    PredictionState.COOKING: "warming",
    PredictionState.PREDICTING: "predicting",
    PredictionState.REMOVAL_PREDICTION_DONE: "ready",
}

PREDICTION_SENSORS = (
    # The cooking target is a number entity.
    CombustionSensorEntityDescription(
        key="prediction_remaining",
        translation_key="prediction_remaining",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_display_precision=0,
        value_fn=lambda probe: (
            probe.prediction.seconds_remaining if probe.prediction is not None else None
        ),
    ),
    CombustionSensorEntityDescription(
        key="prediction_status",
        translation_key="prediction_status",
        device_class=SensorDeviceClass.ENUM,
        options=["idle", *PREDICTION_STATES.values()],
        value_fn=lambda probe: (
            (
                PREDICTION_STATES.get(probe.prediction.state)
                if prediction_active(probe)
                else "idle"
            )
            if probe.prediction is not None
            else None
        ),
    ),
)


# SafeCook comes from the same connected status as predictions, so it shares their option.


def _food_safe_value(
    fn: Callable[[FoodSafe], StateType], *, integrated_only: bool = False
) -> Callable[[Probe], StateType]:
    """Read from the probe's SafeCook program.

    Unknown until a connection reports SafeCook data, and while SafeCook is off.
    """

    def value(probe: Probe) -> StateType:
        if (food_safe := probe.food_safe) is None or not food_safe.program_set:
            return None
        if integrated_only and food_safe.mode is not FoodSafeMode.INTEGRATED:
            # Simplified mode always reports 0, which would read as no progress.
            return None
        return fn(food_safe)

    return value


FOOD_SAFETY_STATES = {
    FoodSafeState.NOT_SAFE: "not_safe",
    FoodSafeState.SAFE: "safe",
    FoodSafeState.SAFETY_IMPOSSIBLE: "safety_impossible",
}

_PRODUCT_PREFIXES = {SimplifiedProduct: "simplified", IntegratedProduct: "integrated"}


def _product_option(product: SimplifiedProduct | IntegratedProduct) -> str:
    # Both modes number their products from 0, so options name the mode too.
    return f"{_PRODUCT_PREFIXES[type(product)]}_{product.name.lower()}"


FOOD_SAFETY_PRODUCTS = [
    _product_option(product)
    for product_type in _PRODUCT_PREFIXES
    for product in product_type
]

FOOD_SAFETY_SERVINGS = {
    Serving.SERVED_IMMEDIATELY: "served_immediately",
    Serving.COOKED_AND_CHILLED: "cooked_and_chilled",
}


def _food_safety_state(probe: Probe) -> str | None:
    if (food_safe := probe.food_safe) is None:
        return None
    if not food_safe.program_set:
        return "off"
    if isinstance(food_safe.state, FoodSafeState):
        return FOOD_SAFETY_STATES[food_safe.state]
    return None


def _food_safety_product(food_safe: FoodSafe) -> str | None:
    product = food_safe.product
    # Unknown products are kept as plain integers.
    if isinstance(product, SimplifiedProduct | IntegratedProduct):
        return _product_option(product)
    return None


FOOD_SAFETY_SENSORS = (
    CombustionSensorEntityDescription(
        key="food_safety",
        translation_key="food_safety",
        device_class=SensorDeviceClass.ENUM,
        options=["off", *FOOD_SAFETY_STATES.values()],
        value_fn=_food_safety_state,
    ),
    _temperature(
        "food_safety_threshold",
        # Simplified programs written without a threshold, as Combustion's Android library and
        # this integration's action do, leave the threshold to the firmware's product rules.
        _food_safe_value(lambda food_safe: food_safe.threshold_temperature or None),
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
        value_fn=_food_safe_value(lambda food_safe: food_safe.seconds_above_threshold),
    ),
    CombustionSensorEntityDescription(
        key="food_safety_log_reduction",
        translation_key="food_safety_log_reduction",
        suggested_display_precision=1,
        value_fn=_food_safe_value(
            lambda food_safe: food_safe.log_reduction, integrated_only=True
        ),
    ),
    CombustionSensorEntityDescription(
        key="food_safety_target_log_reduction",
        translation_key="food_safety_target_log_reduction",
        suggested_display_precision=1,
        value_fn=_food_safe_value(
            lambda food_safe: food_safe.target_log_reduction, integrated_only=True
        ),
    ),
    CombustionSensorEntityDescription(
        key="food_safety_product",
        translation_key="food_safety_product",
        device_class=SensorDeviceClass.ENUM,
        options=FOOD_SAFETY_PRODUCTS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_food_safe_value(_food_safety_product),
    ),
    CombustionSensorEntityDescription(
        key="food_safety_serving",
        translation_key="food_safety_serving",
        device_class=SensorDeviceClass.ENUM,
        options=list(FOOD_SAFETY_SERVINGS.values()),
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_food_safe_value(
            lambda food_safe: (
                FOOD_SAFETY_SERVINGS[food_safe.serving]
                if isinstance(food_safe.serving, Serving)
                else None
            )
        ),
    ),
)

CONNECTED_SENSORS = PREDICTION_SENSORS + FOOD_SAFETY_SENSORS
"""Sensors that need a connection, enabled by the predictions option."""
