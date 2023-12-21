"""Sensor platform for combustion."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SIGNAL_STRENGTH_DECIBELS_MILLIWATT, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityPlatformState
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from sensor_state_data import Units

from custom_components.combustion.combustion_ble.combustion_probe_data import (
    CombustionProbeData,
)
from custom_components.combustion.entity import CombustionEntity
from custom_components.combustion.probe_manager import ProbeManager

from .const import DOMAIN, LOGGER

_LOGGER = LOGGER.getChild('sensor')

VIRTUAL_TEMPERATURE_SENSOR_DESCRIPTION = SensorEntityDescription(
    key=f"{SensorDeviceClass.TEMPERATURE}_{Units.TEMP_CELSIUS}",
    device_class=SensorDeviceClass.TEMPERATURE,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=1
)

TEMPERATURE_SENSOR_DESCRIPTION = SensorEntityDescription(
    key=f"{SensorDeviceClass.TEMPERATURE}_{Units.TEMP_CELSIUS}",
    device_class=SensorDeviceClass.TEMPERATURE,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=1,
    entity_registry_enabled_default=False,
)

RSSI_SENSOR_DESCRIPTION = SensorEntityDescription(
    key=f"{SensorDeviceClass.SIGNAL_STRENGTH}_{Units.SIGNAL_STRENGTH_DECIBELS_MILLIWATT}",
    device_class=SensorDeviceClass.SIGNAL_STRENGTH,
    native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    state_class=SensorStateClass.MEASUREMENT,
    entity_registry_enabled_default=False,
)

PREDICTION_MODE_SENSOR_DESCRIPTION =  SensorEntityDescription(
    key=f"{SensorDeviceClass.ENUM}_prediction_mode",
    device_class=SensorDeviceClass.ENUM,
    options=['None', 'Time to Removal', 'Remove and Resting', 'Reserved', 'unknown']
)

PREDICTION_STATE_SENSOR_DESCRIPTION =  SensorEntityDescription(
    key=f"{SensorDeviceClass.ENUM}_prediction_state",
    device_class=SensorDeviceClass.ENUM,
    options=['Probe Not Inserted', 'Probe Inserted', 'Cooking', 'Predicting', 'Removal Prediction Done', 'Unknown']
)

PREDICTION_TYPE_SENSOR_DESCRIPTION =  SensorEntityDescription(
    key=f"{SensorDeviceClass.ENUM}_prediction_type",
    device_class=SensorDeviceClass.ENUM,
    options=['None', 'Removal', 'Resting', 'Reserved', "Unknown"]
)

SENSOR_DESCRIPTIONS = {
    (
        SensorDeviceClass.TEMPERATURE,
        Units.TEMP_CELSIUS,
    ): SensorEntityDescription(
        key=f"{SensorDeviceClass.TEMPERATURE}_{Units.TEMP_CELSIUS}",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    (
        SensorDeviceClass.ENUM.value,
        None
    ): SensorEntityDescription(
        key=f"{SensorDeviceClass.ENUM}_mode",
        device_class=SensorDeviceClass.ENUM,
        options=['normal', 'instant_read', 'error', 'reserved', 'unknown']
    ),
    (
        SensorDeviceClass.SIGNAL_STRENGTH,
        Units.SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    ): SensorEntityDescription(
        key=f"{SensorDeviceClass.SIGNAL_STRENGTH}_{Units.SIGNAL_STRENGTH_DECIBELS_MILLIWATT}",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
}

def _create_temperature_sensors(probe_manager: ProbeManager, probe_data: CombustionProbeData):
    sensors: list[BaseCombustionTemperatureSensor] = [
        CombustionVirtualCoreSensor(probe_manager, probe_data),
        CombustionVirtualSurfaceSensor(probe_manager, probe_data),
        CombustionVirtualAmbientSensor(probe_manager, probe_data)
    ]
    for i in range(len(probe_data.temperature_data)):
        sensors.append(CombustionTemperatureSensor(probe_manager, probe_data, i + 1))

    for sensor in sensors:
        sensor.async_init()

    return sensors

def _create_prediction_sensors(probe_manager: ProbeManager, probe_data: CombustionProbeData):
    sensors: list[BaseCombustionPredictionSensor] = [
        CombustionPredictionModeSensor(probe_manager, probe_data),
        CombustionPredictionStateSensor(probe_manager, probe_data),
        CombustionPredictionTypeSensor(probe_manager, probe_data),
    ]

    for sensor in sensors:
        sensor.async_init()

    return sensors

def _create_diagnostic_sensors(probe_manager: ProbeManager, probe_data: CombustionProbeData):
    sensors: list[CombustionEntity] = [
        CombustionRSSISensor(probe_manager, probe_data)
    ]

    for sensor in sensors:
        sensor.async_init()

    return sensors

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    """Set up the sensor platform."""
    _LOGGER.debug("Starting async_setup_entry")

    def _create_sensors_callback(pm: ProbeManager, probe_data: CombustionProbeData):
        sensors = _create_temperature_sensors(pm, probe_data)
        sensors.extend(_create_prediction_sensors(pm, probe_data))
        sensors.extend(_create_diagnostic_sensors(pm, probe_data))
        async_add_entities(sensors)

    probe_manager: ProbeManager = hass.data[DOMAIN]
    probe_manager.init_sensor_platform(_create_sensors_callback)

class CombustionRSSISensor(CombustionEntity, SensorEntity):
    """RSSI diagnostic sensor."""

    def __init__(self, probe_manager: ProbeManager, probe_data: CombustionProbeData) -> None:
        """Initialize."""
        super().__init__(probe_data.serial_number)
        self.device_serial_number = probe_data.serial_number
        self.probe_manager = probe_manager
        self._attr_has_entity_name = True
        self._attr_unique_id = f'{probe_data.serial_number}--rssi'
        self.entity_description = RSSI_SENSOR_DESCRIPTION

    def async_init(self):
        """Async initialization."""
        self.probe_manager.add_update_listener(self.on_update)

    @property
    def should_poll(self) -> bool:
        """Do not poll for updates."""
        return False

    @property
    def name(self):
        """Sensor name."""
        return 'RSSI'

    @callback
    def on_update(self):
        """Process probe updates."""
        _LOGGER.debug("Sensor [%s] with state [%s] has been notified of an update", self.unique_id, self._platform_state)
        if self._platform_state == EntityPlatformState.ADDED:
            self.async_schedule_update_ha_state()

    @property
    def native_value(self) -> str:
        """Return the native value of the sensor."""
        return self.probe_manager.probe_data(self.device_serial_number).rssi

class BaseCombustionTemperatureSensor(CombustionEntity, SensorEntity):
    """Base class for temperature sensors."""

    def __init__(self, probe_manager: ProbeManager, probe_data: CombustionProbeData) -> None:
        """Initialize."""
        super().__init__(probe_data.serial_number)
        self.device_serial_number = probe_data.serial_number
        self.probe_manager = probe_manager
        self._attr_has_entity_name = True

    def async_init(self):
        """Async initialization."""
        self.probe_manager.add_update_listener(self.on_update)

    @callback
    def on_update(self):
        """Process probe updates."""
        _LOGGER.debug("Sensor [%s] has been notified of an update", self.unique_id)
        if self._platform_state == EntityPlatformState.ADDED:
            self.async_schedule_update_ha_state()

    @property
    def should_poll(self) -> bool:
        """Do not poll for updates."""
        return False

class BaseCombustionPredictionSensor(CombustionEntity, SensorEntity):
    """Base class for prediction sensors."""

    def __init__(self, probe_manager: ProbeManager, probe_data: CombustionProbeData) -> None:
        """Initialize."""
        super().__init__(probe_data.serial_number)
        self.device_serial_number = probe_data.serial_number
        self.probe_manager = probe_manager
        self._attr_has_entity_name = True

    def async_init(self):
        """Async initialization."""
        self.probe_manager.add_update_listener(self.on_update)

    @callback
    def on_update(self):
        """Process probe updates."""
        _LOGGER.debug("Sensor [%s] has been notified of an update", self.unique_id)
        if self._platform_state == EntityPlatformState.ADDED:
            self.async_schedule_update_ha_state()

    @property
    def should_poll(self) -> bool:
        """Do not poll for updates."""
        return False

class CombustionPredictionModeSensor(BaseCombustionPredictionSensor):
    """Combustion Prediction Mode class."""

    def __init__(self, probe_manager: ProbeManager, probe_data: CombustionProbeData) -> None:
        """Initialize."""
        super().__init__(probe_manager, probe_data)
        self._attr_unique_id = f'{probe_data.serial_number}--prediction-mode'
        self.entity_description = PREDICTION_MODE_SENSOR_DESCRIPTION

    @property
    def name(self):
        """Sensor name."""
        return 'Prediction Mode'

    @property
    def native_value(self) -> str:
        """Return the native value of the sensor."""
        return self.probe_manager.probe_data(self.device_serial_number).prediction_mode

class CombustionPredictionStateSensor(BaseCombustionPredictionSensor):
    """Combustion Prediction State class."""

    def __init__(self, probe_manager: ProbeManager, probe_data: CombustionProbeData) -> None:
        """Initialize."""
        super().__init__(probe_manager, probe_data)
        self._attr_unique_id = f'{probe_data.serial_number}--prediction-state'
        self.entity_description = PREDICTION_STATE_SENSOR_DESCRIPTION

    @property
    def name(self):
        """Sensor name."""
        return 'Prediction State'

    @property
    def native_value(self) -> str:
        """Return the native value of the sensor."""
        return self.probe_manager.probe_data(self.device_serial_number).prediction_state

class CombustionPredictionTypeSensor(BaseCombustionPredictionSensor):
    """Combustion Prediction Type class."""

    def __init__(self, probe_manager: ProbeManager, probe_data: CombustionProbeData) -> None:
        """Initialize."""
        super().__init__(probe_manager, probe_data)
        self._attr_unique_id = f'{probe_data.serial_number}--prediction-type'
        self.entity_description = PREDICTION_TYPE_SENSOR_DESCRIPTION

    @property
    def name(self):
        """Sensor name."""
        return 'Prediction Type'

    @property
    def native_value(self) -> str:
        """Return the native value of the sensor."""
        return self.probe_manager.probe_data(self.device_serial_number).prediction_type

class CombustionTemperatureSensor(BaseCombustionTemperatureSensor):
    """Combustion Temperature Sensor class."""

    def __init__(self, probe_manager: ProbeManager, probe_data: CombustionProbeData, thermistor_id: int) -> None:
        """Initialize."""
        super().__init__(probe_manager, probe_data)
        self.thermistor_id = thermistor_id
        self._attr_unique_id = f'{probe_data.serial_number}--thermistor--{thermistor_id}'
        self.entity_description = TEMPERATURE_SENSOR_DESCRIPTION

    @property
    def name(self):
        """Sensor name."""
        return f'Temperature {self.thermistor_id}'

    @property
    def native_value(self) -> str:
        """Return the native value of the sensor."""
        return self.probe_manager.probe_data(self.device_serial_number).temperature_data[self.thermistor_id - 1]

class CombustionVirtualCoreSensor(BaseCombustionTemperatureSensor):
    """Combustion virtual core sensor class."""

    def __init__(self, probe_manager: ProbeManager, probe_data: CombustionProbeData) -> None:
        """Initialize."""
        super().__init__(probe_manager, probe_data)
        self._attr_unique_id = f'{probe_data.serial_number}--sensor--core'
        self.entity_description = VIRTUAL_TEMPERATURE_SENSOR_DESCRIPTION

    @property
    def name(self):
        """Sensor name."""
        return 'Core Temperature'

    @property
    def native_value(self) -> str:
        """Return the native value of the sensor."""
        (id, temp) = self.probe_manager.probe_data(self.device_serial_number).core_sensor
        return temp

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """State attributes."""
        (id, temp) = self.probe_manager.probe_data(self.device_serial_number).core_sensor
        return {
            "thermistor_id": id
        }

class CombustionVirtualAmbientSensor(BaseCombustionTemperatureSensor):
    """Combustion virtual ambient sensor class."""

    def __init__(self, probe_manager: ProbeManager, probe_data: CombustionProbeData) -> None:
        """Initialize."""
        super().__init__(probe_manager, probe_data)
        self._attr_unique_id = f'{probe_data.serial_number}--sensor--ambient'
        self.entity_description = VIRTUAL_TEMPERATURE_SENSOR_DESCRIPTION

    @property
    def name(self):
        """Sensor name."""
        return 'Ambient Temperature'

    @property
    def native_value(self) -> str:
        """Return the native value of the sensor."""
        (id, temp) = self.probe_manager.probe_data(self.device_serial_number).ambient_sensor
        return temp

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """State attributes."""
        (id, temp) = self.probe_manager.probe_data(self.device_serial_number).ambient_sensor
        return {
            "thermistor_id": id
        }

class CombustionVirtualSurfaceSensor(BaseCombustionTemperatureSensor):
    """Combustion virtual surface sensor class."""

    def __init__(self, probe_manager: ProbeManager, probe_data: CombustionProbeData) -> None:
        """Initialize."""
        super().__init__(probe_manager, probe_data)
        self._attr_unique_id = f'{probe_data.serial_number}--sensor--surface'
        self.entity_description = VIRTUAL_TEMPERATURE_SENSOR_DESCRIPTION

    @property
    def name(self):
        """Sensor name."""
        return 'Surface Temperature'

    @property
    def native_value(self) -> str:
        """Return the native value of the sensor."""
        (id, temp) = self.probe_manager.probe_data(self.device_serial_number).surface_sensor
        return temp

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """State attributes."""
        (id, temp) = self.probe_manager.probe_data(self.device_serial_number).surface_sensor
        return {
            "thermistor_id": id
        }
