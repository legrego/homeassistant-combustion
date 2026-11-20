"""Binary sensors for Combustion probes and Giant Grill Gauges."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from combustion_ble import BatteryStatus, Device, Gauge, Probe
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CombustionConfigEntry
from .entity import CombustionEntity

# Entities only update from pushed advertisements.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class CombustionBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes a Combustion binary sensor."""

    is_on_fn: Callable[[Any], bool | None]


# Battery sensors are named after their device class.
PROBE_BINARY_SENSORS: tuple[CombustionBinarySensorEntityDescription, ...] = (
    CombustionBinarySensorEntityDescription(
        key="battery",
        device_class=BinarySensorDeviceClass.BATTERY,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda probe: probe.battery_status == BatteryStatus.LOW,
    ),
)

GAUGE_BINARY_SENSORS: tuple[CombustionBinarySensorEntityDescription, ...] = (
    CombustionBinarySensorEntityDescription(
        key="battery",
        device_class=BinarySensorDeviceClass.BATTERY,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda gauge: gauge.low_battery,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CombustionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add binary sensors for each probe and gauge as it is discovered."""

    @callback
    def _async_add_device(device: Device) -> None:
        if isinstance(device, Probe):
            descriptions = PROBE_BINARY_SENSORS
        elif isinstance(device, Gauge):
            descriptions = GAUGE_BINARY_SENSORS
        else:
            return
        async_add_entities(
            CombustionBinarySensor(device, description) for description in descriptions
        )

    entry.async_on_unload(
        entry.runtime_data.async_add_device_listener(_async_add_device)
    )


class CombustionBinarySensor(CombustionEntity, BinarySensorEntity):
    """A Combustion binary sensor."""

    entity_description: CombustionBinarySensorEntityDescription

    @property
    def is_on(self) -> bool | None:
        """Return the sensor's state."""
        return self.entity_description.is_on_fn(self._device)
