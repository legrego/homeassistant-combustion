"""CombustionEntity class."""
from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo, Entity

from .const import DEVICE_NAME, DOMAIN, MANUFACTURER


class CombustionEntity(Entity):
    """CombustionEntity class."""

    def __init__(self, serial_number: str) -> None:
        """Initialize."""
        super().__init__()
        self._attr_device_info = DeviceInfo(
            name=f'{DEVICE_NAME} {serial_number}',
            identifiers={(DOMAIN, serial_number)},
            manufacturer=MANUFACTURER,
        )

    async def async_added_to_hass(self) -> None:
        """Register for updates once the entity is actually part of hass.

        Registering here, and unsubscribing via async_on_remove, guarantees a
        listener can never outlive its entity: entities whose platform add
        fails, or which are removed, stop receiving updates. Registering at
        construction time instead leaks a listener every time entity creation
        is retried.
        """
        await super().async_added_to_hass()
        self.async_on_remove(self.probe_manager.add_update_listener(self.on_update))
