"""The Combustion integration.

Creates entities for Combustion Predictive Thermometers and Giant Grill Gauges from their Bluetooth
advertisements, heard directly or relayed by MeatNet repeaters (Display, Booster, Engine, or Giant
Grill Gauge). Probes linked to an optional MeatNet Cloud account can be out of Bluetooth range.

For more details about this integration, please refer to
https://github.com/legrego/homeassistant-combustion
"""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.typing import ConfigType

from .cloud import CloudManager
from .const import CONF_ACCOUNT_ID, CONF_CLOUD, CONF_ENABLE_PREDICTIONS, DOMAIN
from .coordinator import CombustionConfigEntry, CombustionCoordinator
from .services import async_setup_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
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
        cloud=CloudManager(hass, entry) if CONF_CLOUD in entry.data else None,
    )
    entry.runtime_data = coordinator

    # Platforms subscribe to new devices first, so nothing heard at startup is missed.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(coordinator.async_start())
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    if coordinator.cloud is not None:
        coordinator.cloud.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CombustionConfigEntry) -> bool:
    """Unload a config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.async_stop_connections()
    return True


async def _async_entry_updated(
    hass: HomeAssistant, entry: CombustionConfigEntry
) -> None:
    """Reload when predictions are turned on or off, or a cloud account is linked or unlinked.

    Other entry updates must not reload, which would drop every prediction connection: Bluetooth
    discovery of each new Combustion address updates the entry's discovery keys, and the cloud
    session saves its renewed token.
    """
    coordinator = entry.runtime_data
    cloud = coordinator.cloud
    account_id = entry.data.get(CONF_CLOUD, {}).get(CONF_ACCOUNT_ID)
    predictions = entry.options.get(CONF_ENABLE_PREDICTIONS, False)
    if (predictions, account_id) != (
        coordinator.enable_predictions,
        None if cloud is None else cloud.account_id,
    ):
        await hass.config_entries.async_reload(entry.entry_id)
    elif cloud is not None:
        cloud.async_entry_updated()


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: CombustionConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Let users remove a device not heard since Home Assistant started, nor linked to the cloud.

    Others would come back, or not get their entities back until the next restart.
    """
    return all(
        entry.runtime_data.async_forget_device(identifier)
        for domain, identifier in device_entry.identifiers
        if domain == DOMAIN
    )
