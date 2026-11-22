"""SafeCook product and serving for Combustion probes, set through a prediction connection."""

from __future__ import annotations

from combustion_ble import Device, FoodSafeProgram, Probe, Serving
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import CombustionConfigEntry, CombustionCoordinator
from .entity import CombustionProbeControl, async_remove_entities
from .food_safety import (
    PRODUCTS,
    SERVINGS,
    SETTABLE_PRODUCTS,
    Program,
    current_product,
    current_serving,
    product_option,
    serving_option,
    simplified_program,
)

# The library matches each command to its response, so commands for several probes can run at once.
PARALLEL_UPDATES = 0

# Both replace read-only sensors with these keys, keeping their unique ID suffixes.
PRODUCT = SelectEntityDescription(
    key="food_safety_product",
    translation_key="food_safety_product",
    # Every product a probe can report, so the current program always shows. Choosing one that
    # can't be written from Home Assistant explains why.
    options=PRODUCTS,
)
SERVING = SelectEntityDescription(
    key="food_safety_serving",
    translation_key="food_safety_serving",
    options=list(SERVINGS),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CombustionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add SafeCook selects for each probe when predictions are enabled."""
    coordinator = entry.runtime_data
    if not coordinator.enable_predictions:
        async_remove_entities(
            hass, entry.entry_id, SELECT_DOMAIN, [PRODUCT.key, SERVING.key]
        )
        return

    @callback
    def _async_add_device(device: Device) -> None:
        if isinstance(device, Probe):
            async_add_entities(
                [
                    CombustionProductSelect(coordinator, device),
                    CombustionServingSelect(coordinator, device),
                ]
            )

    entry.async_on_unload(coordinator.async_add_device_listener(_async_add_device))


class CombustionProductSelect(CombustionProbeControl, SelectEntity):
    """A probe's SafeCook product. Choosing one starts a Simplified program."""

    def __init__(self, coordinator: CombustionCoordinator, probe: Probe) -> None:
        """Initialize."""
        super().__init__(coordinator, probe, PRODUCT)

    @property
    def current_option(self) -> str | None:
        """Return the product the probe reports. Unknown while SafeCook is off."""
        return current_product(self._device)

    async def async_select_option(self, option: str) -> None:
        """Start a program for the product, keeping the current serving.

        This replaces the program and restarts its progress. The probe reports the new program
        with its next status, which updates this entity.
        """
        if (product := SETTABLE_PRODUCTS.get(option)) is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="food_safety_product_unsupported",
            )

        def change(current: Program | None) -> FoodSafeProgram:
            serving = None if current is None else serving_option(current)
            return simplified_program(
                product, SERVINGS.get(serving or "", Serving.SERVED_IMMEDIATELY)
            )

        await self._coordinator.async_change_food_safe(self._device, change)


class CombustionServingSelect(CombustionProbeControl, SelectEntity):
    """How the food in a probe's SafeCook program will be served."""

    def __init__(self, coordinator: CombustionCoordinator, probe: Probe) -> None:
        """Initialize."""
        super().__init__(coordinator, probe, SERVING)

    @property
    def current_option(self) -> str | None:
        """Return the serving the probe reports. Unknown while SafeCook is off."""
        return current_serving(self._device)

    async def async_select_option(self, option: str) -> None:
        """Restart the current Simplified program with this serving.

        This replaces the program and restarts its progress.
        """

        def change(current: Program | None) -> FoodSafeProgram:
            if current is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="food_safety_no_program",
                )
            # Only Simplified products are settable, so this also rules out Integrated programs.
            product = SETTABLE_PRODUCTS.get(product_option(current) or "")
            if product is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="food_safety_serving_unsupported",
                )
            return simplified_program(product, SERVINGS[option])

        await self._coordinator.async_change_food_safe(self._device, change)
