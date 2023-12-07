"""DataUpdateCoordinator for combustion."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.passive_update_processor import (
    PassiveBluetoothProcessorCoordinator,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.combustion.combustion_ble._parser import (
    CombustionBluetoothDeviceData,
)

from .const import LOGGER

if TYPE_CHECKING:
    from bleak.backends.device import BLEDevice


# https://developers.home-assistant.io/docs/integration_fetching_data#coordinated-single-api-poll-for-data-for-all-entities
class CombustionDataUpdateCoordinator(PassiveBluetoothProcessorCoordinator[CombustionBluetoothDeviceData]):
    """Class to manage fetching data from the API."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        ble_device: BLEDevice,
        update_method: Any
    ) -> None:
        """Initialize."""
        super().__init__(
            hass=hass,
            logger=LOGGER,
            address=ble_device.address,
            mode=bluetooth.BluetoothScanningMode.ACTIVE,
            update_method=update_method
        )

