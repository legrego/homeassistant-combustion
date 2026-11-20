"""The Combustion integration.

Creates entities for Combustion Predictive Thermometers and Giant Grill Gauges from their Bluetooth
advertisements, heard directly or relayed by MeatNet repeaters (Display, Booster, Engine, or Giant
Grill Gauge).

For more details about this integration, please refer to
https://github.com/legrego/homeassistant-combustion
"""

from __future__ import annotations

from combustion_ble import Gauge, Probe
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .const import DOMAIN
from .coordinator import CombustionConfigEntry, CombustionCoordinator
from .entity import device_identifier

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: CombustionConfigEntry) -> bool:
    """Set up Combustion from a config entry."""
    coordinator = CombustionCoordinator(hass)
    entry.runtime_data = coordinator

    # Platforms subscribe to new devices first, so nothing heard at startup is missed.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(coordinator.async_start())
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CombustionConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: CombustionConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Let users remove a device that hasn't been heard since Home Assistant started.

    A device heard since then would not get its entities back until the next restart.
    """
    heard = {
        (DOMAIN, device_identifier(device))
        for device in entry.runtime_data.meatnet.devices
        if isinstance(device, Probe | Gauge)
    }
    return heard.isdisjoint(device_entry.identifiers)
