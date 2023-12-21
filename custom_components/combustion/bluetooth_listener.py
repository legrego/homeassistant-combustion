"""Listen for all Bluetooth advertisements from the Combustion, Inc. manufacturer."""
import asyncio

from home_assistant_bluetooth import BluetoothServiceInfoBleak
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.combustion.combustion_ble.advertising_data import (
    CombustionProductType,
)
from custom_components.combustion.combustion_ble.ble_probe import CombustionBLEProbe
from custom_components.combustion.combustion_ble.combustion_probe_data import (
    CombustionProbeData,
)
from custom_components.combustion.combustion_ble.mode_id import ProbeMode
from custom_components.combustion.combustion_ble.probe_status import ProbeStatus
from custom_components.combustion.const import BT_MANUFACTURER_ID, LOGGER

_LOGGER = LOGGER.getChild('bluetooth-listener')

class BluetoothListener:
    """Listen for all Bluetooth advertisements from the Combustion, Inc. manufacturer."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry):
        """Initialize."""
        self.hass = hass
        self.config_entry = config_entry
        self._devices: dict[str, CombustionBLEProbe] = {}
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
        for device in self._devices.values():
            device._disconnect()

    def _create_probe_status_callback(self, device: CombustionBLEProbe):

        def _probe_status_callback(probe_status: ProbeStatus):
            if self.hass.is_stopping:
                _LOGGER.debug("Discarding probe status; HASS is stopping")
                return

            probe_data = CombustionProbeData.from_probe_status(device=device, probe_status=probe_status)
            if not probe_data.valid:
                _LOGGER.debug("Discarding invalid probe data")
                return

            if probe_data.mode == ProbeMode.instantRead:
                _LOGGER.debug("Discarding instant_read data from [%s]", device._service_info.address)
                return

            for listener in self._listeners:
                listener(probe_data)

        return _probe_status_callback

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

        # if probe_data.mode == ProbeMode.instantRead:
        #     _LOGGER.debug("Discarding instant_read data from [%s]", service_info.address)
        #     return

        if probe_data.device_type == CombustionProductType.PROBE.name:
            if service_info.address in self._devices:
                probe = self._devices[service_info.address]
                probe.set_service_info(service_info)
                if not probe._client or not probe._client.is_connected:
                    asyncio.ensure_future(probe.update(), loop=self.hass.loop)
            else:
                device = bluetooth.async_ble_device_from_address(self.hass, service_info.address, True)
                if device is None:
                    _LOGGER.debug("Unable to reach device, not continuing with CombustionBLEProbe")
                else:
                    _LOGGER.debug("Constructing CombustionBLEProbe")
                    probe = CombustionBLEProbe(device, service_info)
                    probe.register_callback(self._create_probe_status_callback(probe))
                    asyncio.ensure_future(probe.update(), loop=self.hass.loop)
                    self._devices[service_info.address] = probe


        # for listener in self._listeners:
        #     listener(probe_data)
