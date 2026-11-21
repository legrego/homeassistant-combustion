"""Base entity for Combustion devices."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from combustion_ble import Gauge, PredictionMode, Probe
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription

from .const import DOMAIN, MANUFACTURER

if TYPE_CHECKING:
    from .coordinator import CombustionCoordinator

type CombustionDevice = Probe | Gauge


def device_identifier(device: CombustionDevice) -> str:
    """Return the device registry identifier for a device, which prefixes its entities' unique IDs.

    Probes use their serial number in lowercase hex without leading zeros, as earlier versions of
    this integration did, so existing entities keep their history.
    """
    if isinstance(device, Probe):
        return f"{device.serial_number:x}"
    return device.serial_number


def prediction_active(probe: Probe) -> bool:
    """Whether the probe has a supported cooking target."""
    return probe.prediction is not None and probe.prediction.mode in (
        PredictionMode.TIME_TO_REMOVAL,
        PredictionMode.REMOVAL_AND_RESTING,
    )


@callback
def async_remove_entities(
    hass: HomeAssistant, config_entry_id: str, domain: str, keys: Iterable[str]
) -> None:
    """Remove a platform's registered entities with these description keys, for every device."""
    registry = er.async_get(hass)
    suffixes = tuple(f"--{key}" for key in keys)
    for entity in er.async_entries_for_config_entry(registry, config_entry_id):
        if entity.domain == domain and entity.unique_id.endswith(suffixes):
            registry.async_remove(entity.entity_id)


class CombustionEntity(Entity):
    """An entity backed by a Combustion probe or gauge."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, device: CombustionDevice, description: EntityDescription
    ) -> None:
        """Initialize."""
        self._device = device
        self.entity_description = description

        identifier = device_identifier(device)
        if isinstance(device, Probe):
            model = "Predictive Thermometer"
            serial_number = device.serial_number_string
        else:
            model = "Giant Grill Gauge"
            serial_number = device.serial_number

        self._attr_unique_id = f"{identifier}--{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            manufacturer=MANUFACTURER,
            model=model,
            name=f"{model} {serial_number}",
            serial_number=serial_number,
        )
        # No sw_version or hw_version: they are read later, through a prediction connection, and
        # the coordinator records them in the device registry, which keeps the last ones read.

    @property
    def available(self) -> bool:
        """Whether the device has been heard recently, directly or through MeatNet."""
        return self._device.available

    async def async_added_to_hass(self) -> None:
        """Follow changes to the device."""
        await super().async_added_to_hass()
        self.async_on_remove(self._device.add_listener(self.async_write_ha_state))


class CombustionProbeControl(CombustionEntity):
    """A control that sends commands to a probe through a prediction connection."""

    _device: Probe

    def __init__(
        self,
        coordinator: CombustionCoordinator,
        probe: Probe,
        description: EntityDescription,
    ) -> None:
        """Initialize."""
        super().__init__(probe, description)
        self._coordinator = coordinator
        self._written_available: bool | None = None

    @property
    def available(self) -> bool:
        """Whether a running connection can reach the probe."""
        return super().available and self._coordinator.can_command(self._device)

    @callback
    def async_write_ha_state(self) -> None:
        """Write the state, remembering the availability written."""
        self._written_available = self.available
        super().async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Also follow connections starting and ending."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._coordinator.async_add_connection_listener(self._async_check_available)
        )

    @callback
    def _async_check_available(self) -> None:
        if self.available != self._written_available:
            self.async_write_ha_state()
