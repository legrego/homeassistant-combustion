"""Base entity for Combustion devices."""

from __future__ import annotations

from combustion_ble import Gauge, Probe
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription

from .const import DOMAIN, MANUFACTURER

type CombustionDevice = Probe | Gauge


def device_identifier(device: CombustionDevice) -> str:
    """Return the device registry identifier for a device, which prefixes its entities' unique IDs.

    Probes use their serial number in lowercase hex without leading zeros, as earlier versions of
    this integration did, so existing entities keep their history.
    """
    if isinstance(device, Probe):
        return f"{device.serial_number:x}"
    return device.serial_number


class CombustionEntity(Entity):
    """An entity backed by a Combustion probe or gauge."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, device: CombustionDevice, description: EntityDescription
    ) -> None:
        """Initialize."""
        self._device = device
        self.entity_description = description

        identifier = device_identifier(device)
        if isinstance(device, Probe):
            model = "Predictive Thermometer"
            serial_number = device.serial_number_string
        else:
            model = "Giant Grill Gauge"
            serial_number = device.serial_number

        self._attr_unique_id = f"{identifier}--{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            manufacturer=MANUFACTURER,
            model=model,
            name=f"{model} {serial_number}",
            serial_number=serial_number,
        )

    @property
    def available(self) -> bool:
        """Whether the device has been heard recently, directly or through MeatNet."""
        return self._device.available

    async def async_added_to_hass(self) -> None:
        """Follow changes to the device."""
        await super().async_added_to_hass()
        self.async_on_remove(self._device.add_listener(self.async_write_ha_state))
