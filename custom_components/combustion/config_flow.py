"""Adds config flow for Combustion."""
from __future__ import annotations

from typing import Any

from homeassistant import config_entries
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

from custom_components.combustion.combustion_ble.parser import (
    CombustionBluetoothDeviceData,
)

from .const import CONF_DEVICES, DOMAIN, LOGGER


def format_unique_id(address: str) -> str:
    """Format the unique ID for a device."""
    return address.replace(":", "").lower()

class CombustionFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Combustion."""

    VERSION = 1
    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_adv: CombustionBluetoothDeviceData | None = None
        self._all_discovered_devices: dict[str, CombustionBluetoothDeviceData] = {}

    async def async_step_bluetooth(self, discovery_info: BluetoothServiceInfoBleak) -> config_entries.FlowResult:
        """Bluetooth discovery step."""
        LOGGER.debug("async step bluetooth for device %s", str(discovery_info.as_dict()))

        device = CombustionBluetoothDeviceData()
        if not device.supported(discovery_info):
            return self.async_abort(reason="not_supported")

        self._all_discovered_devices[discovery_info.address] = device

        entries = self._async_current_entries()
        if entries:
            LOGGER.debug("Discovered new device, but we already have an entry created.")
            assert len(entries) == 1
            assert self._add_device_to_entry(entries[0], discovery_info.address, device)
            return self.async_abort(reason="updated_entry")

        # For now, only a single "meatnet" is supported. This prevents each device from showing as an independent integration.
        # Instead we ask to configure once, and create devices for each of the entities on the meatnet.
        await self.async_set_unique_id("combustion_meatnet")
        self._abort_if_unique_id_configured()

        self._discovered_adv = device

        self.context["title_placeholders"] = {
            "name": device.title,
            "address": discovery_info.address,
        }

        # Display advertisement
        # {'name': 'FB-57-A0-67-14-9A', 'address': 'FB:57:A0:67:14:9A', 'rssi': -36, 'manufacturer_data': {2503: b'\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'}, 'service_data': {}, 'service_uuids': [], 'source': 'D8:3A:DD:63:C3:81', 'advertisement': AdvertisementData(manufacturer_data={2503: b'\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'}, rssi=-36), 'device': BLEDevice(FB:57:A0:67:14:9A, FB-57-A0-67-14-9A), 'connectable': True, 'time': 2650511.905738745}

        # Probe advertisement
        # {'name': 'C2-71-04-30-7E-40', 'address': 'C2:71:04:30:7E:40', 'rssi': -64, 'manufacturer_data': {2503: b'\x01t\x1b\x00\x10?\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01\xc0\x00\x00'}, 'service_data': {}, 'service_uuids': [], 'source': 'D8:3A:DD:63:C3:81', 'advertisement': AdvertisementData(manufacturer_data={2503: b'\x01t\x1b\x00\x10?\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01\xc0\x00\x00'}, rssi=-64), 'device': BLEDevice(C2:71:04:30:7E:40, C2-71-04-30-7E-40), 'connectable': True, 'time': 2650787.586090339}
        return await self.async_step_confirm()


    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Confirm a single device."""
        assert self._discovered_adv is not None
        if user_input is not None:
            return await self._async_create_entry_from_discovery(user_input)

        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "name": self._discovered_adv.title
            },
        )

    async def _async_create_entry_from_discovery(
        self, user_input: dict[str, Any]
    ) -> config_entries.FlowResult:
        """Create an entry from a discovery."""
        assert self._discovered_adv is not None

        devices = []
        for (addr, device) in self._all_discovered_devices.items():
            devices.append({
                "name": device.title,
                "address": addr,
                "product_type": 2 # hardcode meatnet probe
            })

        return self.async_create_entry(
            title="Combustion Meatnet",
            data={
                **user_input,
                CONF_DEVICES: devices,
            },
        )

    def _add_device_to_entry(self, entry: config_entries.ConfigEntry, address: str, device: CombustionBluetoothDeviceData) -> bool:
        """Add a Combustion device to an existing entry."""
        LOGGER.debug(f"Adding device [{device.title}] to existing entry")
        devices = entry.data.get(CONF_DEVICES, []).copy()
        devices.append({
            "name": device.title,
            "address": address,
            "product_type": 2 # hardcode meatnet probe
        })

        return self.hass.config_entries.async_update_entry(entry, data={
            **entry.data,
            CONF_DEVICES: devices
        })
