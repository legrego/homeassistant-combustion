"""Custom integration to integrate combustion devices with Home Assistant.

For more details about this integration, please refer to
https://github.com/legrego/homeassistant-combustion
"""
from __future__ import annotations

from combustion_ble import BluetoothMode, DeviceManager
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from combustion.bluetooth_listener import BluetoothListener

from .const import DOMAIN, LOGGER

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


# https://developers.home-assistant.io/docs/config_entries_index/#setting-up-an-entry
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up this integration using UI."""
    hass.data.setdefault(DOMAIN, {})

    device_manager = DeviceManager.shared if DeviceManager.shared else DeviceManager()
    device_manager.enable_meatnet()
    callback = await device_manager.init_bluetooth(mode=BluetoothMode.PASSIVE)

    bluetooth_listener = BluetoothListener(hass, entry)
    bluetooth_listener.add_update_listener(callback)

    hass.data[DOMAIN] = {
        "device_manager": device_manager,
        "bluetooth_listener": bluetooth_listener,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    bluetooth_listener.async_init()

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        device_manager: DeviceManager = hass.data[DOMAIN].get("device_manager")
        bluetooth_listener: BluetoothListener = hass.data[DOMAIN].get(
            "bluetooth_listener"
        )

        if bluetooth_listener:
            try:
                bluetooth_listener.async_unload()
            except Exception:
                LOGGER.exception("Error stopping bluetooth listener.")

        if device_manager:
            try:
                await device_manager.async_stop()
            except Exception:
                LOGGER.exception("Error stopping device manager.")
        hass.data[DOMAIN] = {}
    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
