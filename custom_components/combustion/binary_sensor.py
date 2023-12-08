"""Binary sensor platform for combustion."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from custom_components.combustion.combustion_ble.combustion_probe_data import (
    CombustionProbeData,
)
from custom_components.combustion.probe_manager import ProbeManager

from .const import DOMAIN, LOGGER
from .entity import CombustionEntity

_LOGGER = LOGGER.getChild('binary_sensor')


BATTERY_DESCRIPTION = BinarySensorEntityDescription(
    key="probe_battery_ok",
    name="Battery",
    device_class=BinarySensorDeviceClass.BATTERY
)

def _create_binary_sensors(probe_manager: ProbeManager, probe_data: CombustionProbeData):
    sensors: list[CombustionEntity] = [
        CombustionBatterySensor(probe_manager, probe_data)
    ]

    return sensors


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    """Set up the binary_sensor platform."""
    _LOGGER.debug("Starting async_setup_entry")

    def _create_sensors_callback(pm: ProbeManager, probe_data: CombustionProbeData):
        sensors = _create_binary_sensors(pm, probe_data)
        async_add_entities(sensors)

    probe_manager: ProbeManager = hass.data[DOMAIN]
    probe_manager.init_binary_sensor_platform(_create_sensors_callback)

class CombustionBatterySensor(CombustionEntity, BinarySensorEntity):
    """combustion binary_sensor class."""

    def __init__(self, probe_manager: ProbeManager, probe_data: CombustionProbeData) -> None:
        """Initialize."""
        super().__init__(probe_data.serial_number)
        self.device_serial_number = probe_data.serial_number
        self.probe_manager = probe_manager
        self._attr_has_entity_name = True
        self._attr_unique_id = f'{probe_data.serial_number}--battery'
        self.entity_description = BATTERY_DESCRIPTION

    def async_init(self):
        """Async initialization."""
        self.probe_manager.add_update_listener(self.on_update)

    @property
    def name(self):
        """Sensor name."""
        return 'Battery'

    @property
    def is_on(self) -> bool | None:
        """Return true if the battery is low."""
        return not self.probe_manager.probe_data(self.device_serial_number).battery_ok

    @callback
    def on_update(self):
        """Process probe updates."""
        _LOGGER.debug("Sensor [%s] has been notified of an update", self.unique_id)
        self.async_schedule_update_ha_state()

    @property
    def should_poll(self) -> bool:
        """Do not poll for updates."""
        return False


