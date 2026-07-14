"""Custom integration to integrate combustion devices with Home Assistant.

For more details about this integration, please refer to
https://github.com/legrego/homeassistant-combustion
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from custom_components.combustion.bluetooth_listener import BluetoothListener
from custom_components.combustion.probe_manager import ProbeManager

from .const import DOMAIN

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR
]


# https://developers.home-assistant.io/docs/config_entries_index/#setting-up-an-entry
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up this integration using UI."""
    hass.data.setdefault(DOMAIN, {})

    listener = BluetoothListener(hass, entry)
    probe_manager = ProbeManager(listener)

    hass.data[DOMAIN] = probe_manager

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    probe_manager.async_init()
    listener.async_init()

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN] = {}
    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry.

    This must go through hass.config_entries so the entry's async_on_unload
    callbacks run (bluetooth callback deregistration, update listener
    disposal) and reloads are serialized. Calling async_unload_entry /
    async_setup_entry directly skips both, leaking one bluetooth callback and
    one update listener per reload; the leaked update listeners then multiply
    on every entry update.
    """
    await hass.config_entries.async_reload(entry.entry_id)
