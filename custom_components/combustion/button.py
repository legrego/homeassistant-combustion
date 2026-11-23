"""Cancel Combustion probes' cooking targets and control SafeCook, through a prediction connection."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from combustion_ble import Probe
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CombustionConfigEntry, CombustionCoordinator
from .device import CombustionDevice, ProbeDevice
from .entity import CombustionProbeControl, async_remove_entities

# The library matches each command to its response, so commands for several probes can run at once.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class CombustionButtonEntityDescription(ButtonEntityDescription):
    """Describes a command sent to a probe when the button is pressed."""

    press_fn: Callable[[CombustionCoordinator, Probe], Coroutine[Any, Any, None]]


BUTTONS = (
    CombustionButtonEntityDescription(
        key="prediction_cancel",
        translation_key="prediction_cancel",
        press_fn=lambda coordinator, probe: coordinator.async_set_prediction(
            probe, None
        ),
    ),
    CombustionButtonEntityDescription(
        key="food_safety_reset",
        translation_key="food_safety_reset",
        press_fn=lambda coordinator, probe: coordinator.async_reset_food_safe(probe),
    ),
    CombustionButtonEntityDescription(
        key="food_safety_off",
        translation_key="food_safety_off",
        press_fn=lambda coordinator, probe: coordinator.async_configure_food_safe(
            probe, None
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CombustionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add each probe's buttons when predictions are enabled."""
    coordinator = entry.runtime_data
    if not coordinator.enable_predictions:
        async_remove_entities(
            hass, entry.entry_id, BUTTON_DOMAIN, [button.key for button in BUTTONS]
        )
        return

    @callback
    def _async_add_device(device: CombustionDevice) -> None:
        if isinstance(device, ProbeDevice):
            async_add_entities(
                CombustionProbeButton(coordinator, device, description)
                for description in BUTTONS
            )

    entry.async_on_unload(coordinator.async_add_device_listener(_async_add_device))


class CombustionProbeButton(CombustionProbeControl, ButtonEntity):
    """Send a command to a probe. Its status reports the result."""

    entity_description: CombustionButtonEntityDescription

    def __init__(
        self,
        coordinator: CombustionCoordinator,
        probe: ProbeDevice,
        description: CombustionButtonEntityDescription,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, probe, description)

    async def async_press(self) -> None:
        """Send the command and wait for the probe to acknowledge it."""
        await self.entity_description.press_fn(
            self._coordinator, self._device.bluetooth
        )
