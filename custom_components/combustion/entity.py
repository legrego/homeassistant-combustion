"""CombustionEntity class."""
from __future__ import annotations

from homeassistant.components.bluetooth.passive_update_coordinator import (
    PassiveBluetoothCoordinatorEntity,
)
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, NAME, VERSION
from .coordinator import CombustionDataUpdateCoordinator


class CombustionEntity(PassiveBluetoothCoordinatorEntity):
    """CombustionEntity class."""

    def __init__(self, coordinator: CombustionDataUpdateCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = coordinator.address
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name=coordinator.device.product_name,
            model=VERSION,
            manufacturer=NAME,
        )
