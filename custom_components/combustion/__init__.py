"""Custom integration to integrate combustion devices with Home Assistant.

For more details about this integration, please refer to
https://github.com/legrego/homeassistant-combustion
"""
from __future__ import annotations

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry, ConfigEntryNotReady
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from custom_components.combustion.combustion_ble.parser import (
    CombustionBluetoothDeviceData,
)

from .const import CONF_DEVICES, DOMAIN
from .coordinator import CombustionDataUpdateCoordinator

PLATFORMS: list[Platform] = [
    Platform.SENSOR
]


# https://developers.home-assistant.io/docs/config_entries_index/#setting-up-an-entry
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up this integration using UI."""
    hass.data.setdefault(DOMAIN, {})

    for device_conf in entry.data.get(CONF_DEVICES):
        name = device_conf.get("name")
        address = device_conf.get("address")
        device_conf.get("product_type")

        # Not sure if this pattern makes sense, but I'm loosely basing it off of
        # https://github.com/home-assistant/core/blob/419dc8adb1026c57a3fe5f004bcd2c6d76710f9f/homeassistant/components/thermopro/__init__.py
        # Seems strange to have an unused varable dangling here.
        data = CombustionBluetoothDeviceData()

        ble_device = bluetooth.async_ble_device_from_address(
            hass, address.upper(), True
        )

        if not ble_device:
            raise ConfigEntryNotReady(
                f"Could not find Combustion device [{name}] with address [{address}]"
            )

        hass.data[DOMAIN][address] = coordinator = CombustionDataUpdateCoordinator(
            hass=hass,
            ble_device=ble_device,
            update_method=data.update
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    entry.async_on_unload(
        coordinator.async_start()
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN] = {}
    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
