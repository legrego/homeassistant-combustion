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
