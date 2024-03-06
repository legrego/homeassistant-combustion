"""Sensor platform for combustion."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from combustion_ble import DeviceManager
from combustion_ble.ble_data.probe_temperatures import ProbeTemperatures
from combustion_ble.devices import Device, Probe
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SIGNAL_STRENGTH_DECIBELS_MILLIWATT, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityPlatformState
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from sensor_state_data import Units

from custom_components.combustion.entity import CombustionEntity

from .const import DOMAIN, LOGGER

_LOGGER = LOGGER.getChild("sensor")

VIRTUAL_TEMPERATURE_SENSOR_DESCRIPTION = SensorEntityDescription(
    key=f"{SensorDeviceClass.TEMPERATURE}_{Units.TEMP_CELSIUS}",
    device_class=SensorDeviceClass.TEMPERATURE,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=1,
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
    # TODO: prediction sensors...
    (SensorDeviceClass.ENUM.value, None): SensorEntityDescription(
        key=f"{SensorDeviceClass.ENUM}_mode",
        device_class=SensorDeviceClass.ENUM,
        options=["normal", "instant_read", "error", "reserved", "unknown"],
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


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
):
    """Set up the sensor platform."""
    _LOGGER.error("Starting async_setup_entry")

    def _device_listener(added: list[Device], removed: list[Device]):
        _LOGGER.error("device-listener called with %s", len(added))
        if not hass.is_running or hass.is_stopping:
            return
        for device in added:
            if isinstance(device, Probe):
                # TODO: Create sensors for each discovered Probe
                sensors = [
                    CombustionVirtualCoreSensor(device),
                    CombustionVirtualSurfaceSensor(device),
                    CombustionVirtualAmbientSensor(device),
                    CombustionRSSISensor(device),
                ]

                sensors.extend(
                    [
                        CombustionTemperatureSensor(device, thermistor_id)
                        for thermistor_id in range(0, 8)
                    ]
                )

                _LOGGER.error("Creating sensors!")
                async_add_entities(sensors)

    device_manager: DeviceManager = hass.data[DOMAIN].get("device_manager")
    device_manager.add_device_listener(_device_listener)

    return True


class CombustionRSSISensor(CombustionEntity, SensorEntity):
    """RSSI diagnostic sensor."""

    def __init__(self, probe: Probe) -> None:
        """Initialize."""
        super().__init__(probe.serial_number_string)
        self.device_serial_number = probe.serial_number_string
        self._attr_has_entity_name = True
        self._attr_unique_id = f"{probe.serial_number_string}--rssi"
        self.entity_description = RSSI_SENSOR_DESCRIPTION
        self._current_value = probe.rssi

        def update_listener(next_rssi):
            self._current_value = next_rssi
            if self._platform_state == EntityPlatformState.ADDED:
                self.async_schedule_update_ha_state()

        probe.add_rssi_listener(update_listener)

    @property
    def should_poll(self) -> bool:
        """Do not poll for updates."""
        return False

    @property
    def name(self):
        """Sensor name."""
        return "RSSI"

    @property
    def native_value(self) -> str:
        """Return the native value of the sensor."""
        return self._current_value


class CombustionTemperatureSensor(CombustionEntity, SensorEntity):
    """Combustion Temperature Sensor class."""

    def __init__(
        self,
        probe: Probe,
        thermistor_id: int,
    ) -> None:
        """Initialize."""
        super().__init__(probe)
        self.probe = probe
        self.thermistor_id = thermistor_id
        self._attr_unique_id = (
            f"{probe.serial_number_string}--thermistor--{thermistor_id}"
        )
        self.entity_description = TEMPERATURE_SENSOR_DESCRIPTION

        self._current_value: float | None = None

        def update_listener(next_value: ProbeTemperatures | None):
            if next_value is None:
                return
            self._current_value = next_value.values[self.thermistor_id - 1]
            if self._platform_state == EntityPlatformState.ADDED:
                self.async_schedule_update_ha_state()

        self.probe.add_current_temperatures_listener(update_listener)

    @property
    def name(self):
        """Sensor name."""
        return f"Temperature {self.thermistor_id}"

    @property
    def native_value(self) -> str:
        """Return the native value of the sensor."""
        return self._current_value

    @property
    def should_poll(self) -> bool:
        """Do not poll for updates."""
        return False

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """State attributes."""
        return {}


class CombustionVirtualSensor(CombustionEntity, SensorEntity):
    """Base class for virtual temperature sensors."""

    def __init__(self, probe: Probe) -> None:
        """Initialize."""
        super().__init__(probe.serial_number_string)
        self.entity_description = VIRTUAL_TEMPERATURE_SENSOR_DESCRIPTION

        self.probe = probe
        self.device_serial_number = probe.serial_number_string
        self._attr_has_entity_name = True
        self._current_value = probe.virtual_temperatures

        def update_listener(next_value):
            self._current_value = next_value
            if self._platform_state == EntityPlatformState.ADDED:
                self.async_schedule_update_ha_state()

        self.probe.add_virtual_temperatures_listener(update_listener)

    @property
    def should_poll(self) -> bool:
        """Do not poll for updates."""
        return False


class CombustionVirtualCoreSensor(CombustionVirtualSensor):
    """Combustion virtual core sensor class."""

    def __init__(self, probe: Probe) -> None:
        """Initialize."""
        super().__init__(probe)
        self._attr_unique_id = f"{probe.serial_number_string}--sensor--core"

    @property
    def name(self):
        """Sensor name."""
        return "Core Temperature"

    @property
    def native_value(self) -> str:
        """Return the native value of the sensor."""
        return self._current_value.core_temperature

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """State attributes."""
        return {}


class CombustionVirtualAmbientSensor(CombustionVirtualSensor):
    """Combustion virtual ambient sensor class."""

    def __init__(self, probe: Probe) -> None:
        """Initialize."""
        super().__init__(probe)
        self._attr_unique_id = f"{probe.serial_number_string}--sensor--ambient"

    @property
    def name(self):
        """Sensor name."""
        return "Ambient Temperature"

    @property
    def native_value(self) -> str:
        """Return the native value of the sensor."""
        return self._current_value.ambient_temperature


class CombustionVirtualSurfaceSensor(CombustionVirtualSensor):
    """Combustion virtual surface sensor class."""

    def __init__(self, probe: Probe) -> None:
        """Initialize."""
        super().__init__(probe)
        self._attr_unique_id = f"{probe.serial_number_string}--sensor--surface"

    @property
    def name(self):
        """Sensor name."""
        return "Surface Temperature"

    @property
    def native_value(self) -> str:
        """Return the native value of the sensor."""
        return self._current_value.surface_temperature
