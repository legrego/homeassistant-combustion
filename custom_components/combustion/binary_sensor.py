"""Binary sensors for Combustion probes and Giant Grill Gauges."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CombustionConfigEntry
from .device import CombustionDevice
from .entity import CombustionEntity

# Entities only update from pushed data.
PARALLEL_UPDATES = 0


# Named after its device class.
BATTERY = BinarySensorEntityDescription(
    key="battery",
    device_class=BinarySensorDeviceClass.BATTERY,
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CombustionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add binary sensors for each probe and gauge as it is discovered."""

    @callback
    def _async_add_device(device: CombustionDevice) -> None:
        async_add_entities([CombustionBatterySensor(device, BATTERY)])

    entry.async_on_unload(
        entry.runtime_data.async_add_device_listener(_async_add_device)
    )


class CombustionBatterySensor(CombustionEntity, BinarySensorEntity):
    """Whether a device's battery is low."""

    @property
    def is_on(self) -> bool | None:
        """Return whether the battery is low."""
        return self._device.low_battery
