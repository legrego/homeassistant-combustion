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
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.typing import ConfigType

from .const import CONF_ENABLE_PREDICTIONS, DOMAIN
from .coordinator import CombustionConfigEntry, CombustionCoordinator
from .entity import device_identifier
from .services import async_setup_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SENSOR,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions, which work once a probe has a prediction connection."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: CombustionConfigEntry) -> bool:
    """Set up Combustion from a config entry."""
    coordinator = CombustionCoordinator(
        hass,
        entry.entry_id,
        enable_predictions=entry.options.get(CONF_ENABLE_PREDICTIONS, False),
    )
    entry.runtime_data = coordinator

    # Platforms subscribe to new devices first, so nothing heard at startup is missed.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(coordinator.async_start())
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CombustionConfigEntry) -> bool:
    """Unload a config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.async_stop_connections()
    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: CombustionConfigEntry
) -> None:
    """Reload when prediction connections are enabled or disabled.

    Other entry updates must not reload: Bluetooth discovery of each new Combustion address
    updates the entry's discovery keys, and a reload would drop every prediction connection.
    """
    enabled = entry.options.get(CONF_ENABLE_PREDICTIONS, False)
    if enabled != entry.runtime_data.enable_predictions:
        await hass.config_entries.async_reload(entry.entry_id)


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
