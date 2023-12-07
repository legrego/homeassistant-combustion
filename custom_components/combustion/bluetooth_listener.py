"""Listen for all Bluetooth advertisements from the Combustion, Inc. manufacturer."""
from home_assistant_bluetooth import BluetoothServiceInfoBleak
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.combustion.combustion_ble.combustion_probe_data import (
    CombustionProbeData,
)
from custom_components.combustion.combustion_ble.mode_id import ProbeMode
from custom_components.combustion.const import BT_MANUFACTURER_ID, LOGGER

_LOGGER = LOGGER.getChild('bluetooth-listener')

class BluetoothListener:
    """Listen for all Bluetooth advertisements from the Combustion, Inc. manufacturer."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry):
        """Initialize."""
        self.hass = hass
        self.config_entry = config_entry
        self._listeners = []

    def add_update_listener(self, listener):
        """Add a listener to be notified of new BT data."""
        self._listeners.append(listener)

    def async_init(self):
        """Async initialization."""
        self.config_entry.async_on_unload(
            bluetooth.async_register_callback(
                self.hass,
                self._bt_callback,
                bluetooth.BluetoothCallbackMatcher(manufacturer_id=BT_MANUFACTURER_ID),
                bluetooth.BluetoothScanningMode.ACTIVE
            )
        )
        self.config_entry.async_on_unload(self.async_unload)

    def async_unload(self):
        """Async unload."""
        self._listeners.clear()

    def _bt_callback(self, service_info: BluetoothServiceInfoBleak, change):
        """Handle incoming BT advertisements."""
        _LOGGER.debug("Handling advertisement from [%s]", service_info.address)
        if self.hass.is_stopping:
            _LOGGER.debug("Discarding advertisement; HASS is stopping")
            return

        probe_data = CombustionProbeData.from_advertisement(service_info)
        if not probe_data.valid:
            _LOGGER.debug("Discarding invalid advertisement from [%s]", service_info.address)
            return

        if probe_data.mode == ProbeMode.instantRead:
            _LOGGER.debug("Discarding instant_read data from [%s]", service_info.address)
            return

        for listener in self._listeners:
            listener(probe_data)
