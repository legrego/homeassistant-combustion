"""Cooking targets for Combustion probes, set through a prediction connection."""

from __future__ import annotations

from homeassistant.components.number import (
    DOMAIN as NUMBER_DOMAIN,
)
from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CombustionConfigEntry, CombustionCoordinator
from .device import CombustionDevice, ProbeDevice
from .entity import CombustionProbeControl, async_remove_entities

# The library matches each command to its response, so commands for several probes can run at once.
PARALLEL_UPDATES = 0

MIN_TARGET = 0.0
MAX_TARGET = 102.3
"""The highest removal target a probe accepts, in Celsius."""

TARGET = NumberEntityDescription(
    # Replaces the read-only sensor with this key, keeping its unique ID suffix.
    key="prediction_target",
    translation_key="prediction_target",
    device_class=NumberDeviceClass.TEMPERATURE,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    native_min_value=MIN_TARGET,
    native_max_value=MAX_TARGET,
    native_step=0.1,
    mode=NumberMode.BOX,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CombustionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a cooking target for each probe when predictions are enabled."""
    coordinator = entry.runtime_data
    if not coordinator.enable_predictions:
        async_remove_entities(hass, entry.entry_id, NUMBER_DOMAIN, [TARGET.key])
        return

    @callback
    def _async_add_device(device: CombustionDevice) -> None:
        if isinstance(device, ProbeDevice):
            async_add_entities([CombustionTargetNumber(coordinator, device)])

    entry.async_on_unload(coordinator.async_add_device_listener(_async_add_device))


class CombustionTargetNumber(CombustionProbeControl, NumberEntity):
    """A probe's cooking target. Setting it starts a removal prediction."""

    def __init__(self, coordinator: CombustionCoordinator, probe: ProbeDevice) -> None:
        """Initialize."""
        super().__init__(coordinator, probe, TARGET)

    @property
    def native_value(self) -> float | None:
        """Return the target the probe reports. Unknown when no target is set."""
        prediction = self._device.prediction
        return None if prediction is None else prediction.target

    async def async_set_native_value(self, value: float) -> None:
        """Set the probe's removal target.

        The probe reports the new target with its next status, which updates this entity.
        """
        # Converting from Fahrenheit can land just outside the probe's range.
        celsius = min(max(round(value, 1), MIN_TARGET), MAX_TARGET)
        await self._coordinator.async_set_prediction(self._probe, celsius)
