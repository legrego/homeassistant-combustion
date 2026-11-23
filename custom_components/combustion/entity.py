"""Base entity for Combustion devices."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription

from .const import DOMAIN, MANUFACTURER
from .device import CombustionDevice, ProbeDevice

if TYPE_CHECKING:
    from .coordinator import CombustionCoordinator


@callback
def async_remove_entities(
    hass: HomeAssistant, config_entry_id: str, domain: str, keys: Iterable[str]
) -> None:
    """Remove a platform's registered entities with these description keys, for every device."""
    registry = er.async_get(hass)
    suffixes = tuple(f"--{key}" for key in keys)
    for entity in er.async_entries_for_config_entry(registry, config_entry_id):
        if entity.domain == domain and entity.unique_id.endswith(suffixes):
            registry.async_remove(entity.entity_id)


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

        self._attr_unique_id = f"{device.identifier}--{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.identifier)},
            manufacturer=MANUFACTURER,
            model=device.model,
            name=f"{device.model} {device.serial_number}",
            serial_number=device.serial_number,
        )
        # No sw_version or hw_version: they are read later, through a prediction connection, and
        # the coordinator records them in the device registry, which keeps the last ones read.

    @property
    def available(self) -> bool:
        """Whether the device has been heard recently, directly or through MeatNet."""
        return self._device.available

    async def async_added_to_hass(self) -> None:
        """Follow changes to the device."""
        await super().async_added_to_hass()
        self.async_on_remove(self._device.async_add_listener(self.async_write_ha_state))


class CombustionProbeControl(CombustionEntity):
    """A control that sends commands to a probe through a prediction connection."""

    _device: ProbeDevice

    def __init__(
        self,
        coordinator: CombustionCoordinator,
        probe: ProbeDevice,
        description: EntityDescription,
    ) -> None:
        """Initialize."""
        super().__init__(probe, description)
        self._coordinator = coordinator
        self._written_available: bool | None = None

    @property
    def available(self) -> bool:
        """Whether a running connection can reach the probe."""
        return super().available and self._coordinator.can_command(
            self._device.bluetooth
        )

    @callback
    def async_write_ha_state(self) -> None:
        """Write the state, remembering the availability written."""
        self._written_available = self.available
        super().async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Also follow connections starting and ending."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._coordinator.async_add_connection_listener(self._async_check_available)
        )

    @callback
    def _async_check_available(self) -> None:
        if self.available != self._written_available:
            self.async_write_ha_state()
