"""Binary sensor platform for combustion."""
from __future__ import annotations

from combustion_ble import DeviceManager
from combustion_ble.ble_data.battery_status_virtual_sensors import BatteryStatus
from combustion_ble.devices import Device, Probe
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityPlatformState
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, LOGGER
from .entity import CombustionEntity

_LOGGER = LOGGER.getChild("binary_sensor")


BATTERY_DESCRIPTION = BinarySensorEntityDescription(
    key="probe_battery_ok", name="Battery", device_class=BinarySensorDeviceClass.BATTERY
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
):
    """Set up the binary_sensor platform."""
    _LOGGER.debug("Starting async_setup_entry %s", hass.is_running)

    def _device_listener(added: list[Device], removed: list[Device]):
        if not hass.is_running or hass.is_stopping:
            return
        for device in added:
            if isinstance(device, Probe):
                # TODO: Create sensors for each discovered Probe
                sensors = [CombustionBatterySensor(device)]
                _LOGGER.error(
                    "Adding binary sensors. Loop running = %s. Home assistant stopping = %s, running = %s",
                    hass.loop.is_running(),
                    hass.is_stopping,
                    hass.is_running,
                )
                async_add_entities(sensors)

    device_manager: DeviceManager = hass.data[DOMAIN].get("device_manager")
    device_manager.add_device_listener(_device_listener)

    return True


class CombustionBatterySensor(CombustionEntity, BinarySensorEntity):
    """combustion binary_sensor class."""

    def __init__(self, probe: Probe) -> None:
        """Initialize."""
        super().__init__(probe.serial_number_string)
        self.probe = probe
        self.device_serial_number = probe.serial_number_string
        self._attr_has_entity_name = True
        self._attr_unique_id = f"{probe.serial_number_string}--battery"
        self.entity_description = BATTERY_DESCRIPTION

        probe.add_battery_status_listener(self.on_update)

    @property
    def name(self):
        """Sensor name."""
        return "Battery"

    @property
    def is_on(self) -> bool | None:
        """Return true if the battery is low."""
        return self.probe.batery_status != BatteryStatus.OK

    @callback
    def on_update(self, next_battery):
        """Process probe updates."""
        _LOGGER.debug("Sensor [%s] has been notified of an update", self.unique_id)
        if self._platform_state == EntityPlatformState.ADDED:
            self.async_schedule_update_ha_state()

    @property
    def should_poll(self) -> bool:
        """Do not poll for updates."""
        return False
