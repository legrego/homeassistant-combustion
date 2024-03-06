"""Listen for all Bluetooth advertisements from the Combustion, Inc. manufacturer."""
import asyncio

from home_assistant_bluetooth import BluetoothServiceInfoBleak
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from custom_components.combustion.const import BT_MANUFACTURER_ID, LOGGER

_LOGGER = LOGGER.getChild("bluetooth-listener")


class BluetoothListener:
    """Listen for all Bluetooth advertisements from the Combustion, Inc. manufacturer."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry):
        """Initialize."""
        self.hass = hass
        self.config_entry = config_entry
        self._listeners = []

    def add_update_listener(self, listener):
        """Add a listener to be notified of new BT data."""

        @callback
        def wrapped_listener(device, advertisement):
            return listener(device, advertisement)

        self._listeners.append(wrapped_listener)

    def async_init(self):
        """Async initialization."""
        self.config_entry.async_on_unload(
            bluetooth.async_register_callback(
                self.hass,
                self._bt_callback,
                bluetooth.BluetoothCallbackMatcher(manufacturer_id=BT_MANUFACTURER_ID),
                bluetooth.BluetoothScanningMode.ACTIVE,
            )
        )
        self.config_entry.async_on_unload(self.async_unload)

    def async_unload(self):
        """Async unload."""
        self._listeners.clear()

    def _task_cb(self, future: asyncio.Future):
        if not future.cancelled():
            if ex := future.exception():
                _LOGGER.error("Error during bt_callback %r: %s", future, ex)

    def _bt_callback(self, service_info: BluetoothServiceInfoBleak, change):
        """Handle incoming BT advertisements."""
        _LOGGER.debug("Handling advertisement from [%s]", service_info.address)
        if self.hass.is_stopping:
            _LOGGER.debug("Discarding advertisement; HASS is stopping")
            return

        device = service_info.device
        advetrisement = service_info.advertisement
        for listener in self._listeners:
            self.hass.async_add_job(listener, device, advetrisement)
