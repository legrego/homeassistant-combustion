"""Cancel Combustion probes' cooking targets through a prediction connection."""

from __future__ import annotations

from combustion_ble import Device, Probe
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CombustionConfigEntry, CombustionCoordinator
from .entity import CombustionProbeControl, async_remove_entities

# The library matches each command to its response, so commands for several probes can run at once.
PARALLEL_UPDATES = 0

CANCEL_TARGET = ButtonEntityDescription(
    key="prediction_cancel",
    translation_key="prediction_cancel",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CombustionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a cancel button for each probe when predictions are enabled."""
    coordinator = entry.runtime_data
    if not coordinator.enable_predictions:
        async_remove_entities(hass, entry.entry_id, BUTTON_DOMAIN, [CANCEL_TARGET.key])
        return

    @callback
    def _async_add_device(device: Device) -> None:
        if isinstance(device, Probe):
            async_add_entities([CombustionCancelTargetButton(coordinator, device)])

    entry.async_on_unload(coordinator.async_add_device_listener(_async_add_device))


class CombustionCancelTargetButton(CombustionProbeControl, ButtonEntity):
    """Turn off a probe's cooking target and its prediction."""

    def __init__(self, coordinator: CombustionCoordinator, probe: Probe) -> None:
        """Initialize."""
        super().__init__(coordinator, probe, CANCEL_TARGET)

    async def async_press(self) -> None:
        """Cancel the probe's cooking target."""
        await self._coordinator.async_set_prediction(self._device, None)
