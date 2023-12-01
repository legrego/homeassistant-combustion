"""Sensor platform for combustion."""
from __future__ import annotations

from homeassistant.components.bluetooth.passive_update_processor import (
    PassiveBluetoothDataProcessor,
    PassiveBluetoothDataUpdate,
    PassiveBluetoothEntityKey,
    PassiveBluetoothProcessorEntity,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SIGNAL_STRENGTH_DECIBELS_MILLIWATT, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.sensor import sensor_device_info_to_hass_device_info
from sensor_state_data import DeviceKey, SensorUpdate, Units

from .const import DOMAIN, LOGGER
from .coordinator import CombustionDataUpdateCoordinator

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

def _device_key_to_bluetooth_entity_key(
    device_key: DeviceKey,
) -> PassiveBluetoothEntityKey:
    """Convert a device key to an entity key."""
    return PassiveBluetoothEntityKey(device_key.key, device_key.device_id)

def sensor_update_to_bluetooth_data_update(sensor_update: SensorUpdate):
    """Convert a sensor update to a Bluetooth data update."""
    # This function must convert the parsed_data
    # from your library's update_method to a `PassiveBluetoothDataUpdate`
    # See the structure above

    LOGGER.debug("Inside sensor_update_to_bluetooth_data_update")

    return PassiveBluetoothDataUpdate(
        devices={
            device_id: sensor_device_info_to_hass_device_info(device_info)
            for device_id, device_info in sensor_update.devices.items()
        },
        entity_descriptions={
             _device_key_to_bluetooth_entity_key(device_key): SENSOR_DESCRIPTIONS[
                (description.device_class, description.native_unit_of_measurement)
            ]
            for device_key, description in sensor_update.entity_descriptions.items()
            # if description.device_class and description.native_unit_of_measurement
        },
        entity_data={
            _device_key_to_bluetooth_entity_key(device_key): sensor_values.native_value
            for device_key, sensor_values in sensor_update.entity_values.items()
        },
        entity_names={
            _device_key_to_bluetooth_entity_key(device_key): sensor_values.name
            for device_key, sensor_values in sensor_update.entity_values.items()
        },
    )

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_devices):
    """Set up the sensor platform."""
    LOGGER.debug("Inside async_setup_entry")
    domain_data: dict[str, CombustionDataUpdateCoordinator] = hass.data[DOMAIN]
    coordinators = domain_data.items()
    LOGGER.debug("We have %s coordinators", len(coordinators))
    for (_cid, coordinator) in coordinators:
        LOGGER.debug("Starting setup for %s", coordinator.address)
        processor = PassiveBluetoothDataProcessor(sensor_update_to_bluetooth_data_update)
        entry.async_on_unload(processor.async_add_entities_listener(CombustionBluetoothSensor, async_add_devices))
        entry.async_on_unload(coordinator.async_register_processor(processor))
        LOGGER.debug("Finished setup for %s", coordinator.address)

class CombustionBluetoothSensor(PassiveBluetoothProcessorEntity[PassiveBluetoothDataProcessor[float | int | None]], SensorEntity):
    """Combustion Bluetooth Sensor class."""

    @property
    def native_value(self) -> str:
        """Return the native value of the sensor."""
        return self.processor.entity_data.get(self.entity_key)
