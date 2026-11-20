"""Sensors for Combustion probes and Giant Grill Gauges."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from combustion_ble import Device, Gauge, PredictionState, Probe
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
from .entity import CombustionEntity
from .prediction import PredictionMonitor

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

    @callback
    def _async_add_device(device: Device) -> None:
        if isinstance(device, Probe):
            descriptions = PROBE_SENSORS
        elif isinstance(device, Gauge):
            descriptions = GAUGE_SENSORS
        else:
            return
        if isinstance(device, Probe) and entry.runtime_data.enable_predictions:
            monitor = entry.runtime_data.prediction_for(device)
            async_add_entities(
                CombustionPredictionSensor(device, monitor, description)
                for description in PREDICTION_SENSORS
            )
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
    _temperature(
        "prediction_target",
        lambda monitor: (
            monitor.status.prediction_set_point_temperature if monitor.active else None
        ),
        translation_key="prediction_target",
    ),
    CombustionSensorEntityDescription(
        key="prediction_remaining",
        translation_key="prediction_remaining",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_display_precision=0,
        value_fn=lambda monitor: monitor.seconds_remaining,
    ),
    CombustionSensorEntityDescription(
        key="prediction_status",
        translation_key="prediction_status",
        device_class=SensorDeviceClass.ENUM,
        options=["idle", *PREDICTION_STATES.values()],
        value_fn=lambda monitor: (
            (
                PREDICTION_STATES.get(monitor.status.prediction_state)
                if monitor.active
                else "idle"
            )
            if monitor.status is not None
            else None
        ),
    ),
)


class CombustionPredictionSensor(CombustionSensor):
    """A prediction entity that also follows connected status notifications."""

    def __init__(
        self,
        probe: Probe,
        monitor: PredictionMonitor,
        description: CombustionSensorEntityDescription,
    ) -> None:
        """Attach prediction data to the existing probe device."""
        super().__init__(probe, description)
        self._monitor = monitor

    @property
    def native_value(self) -> StateType:
        """Return connected prediction data, or unknown when disconnected."""
        return self.entity_description.value_fn(self._monitor)

    async def async_added_to_hass(self) -> None:
        """Listen for predictions as well as passive availability changes."""
        await super().async_added_to_hass()
        self.async_on_remove(self._monitor.add_listener(self.async_write_ha_state))
