"""Actions for Combustion probes."""

from __future__ import annotations

import voluptuous as vol
from combustion_ble import Probe
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, PROBE_MODEL
from .coordinator import CombustionCoordinator
from .device import ProbeDevice
from .food_safety import SERVINGS, SETTABLE_PRODUCTS, simplified_program

SERVICE_CONFIGURE_SAFECOOK = "configure_safecook"
ATTR_PRODUCT = "product"
ATTR_SERVING = "serving"

CONFIGURE_SAFECOOK_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_PRODUCT): vol.In(SETTABLE_PRODUCTS),
        vol.Optional(ATTR_SERVING, default="served_immediately"): vol.In(SERVINGS),
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's actions."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_CONFIGURE_SAFECOOK,
        _async_configure_safecook,
        schema=CONFIGURE_SAFECOOK_SCHEMA,
    )


async def _async_configure_safecook(call: ServiceCall) -> None:
    """Start a Simplified SafeCook program on a probe."""
    coordinator, probe = _probe_for_device(call.hass, call.data[ATTR_DEVICE_ID])
    program = simplified_program(
        SETTABLE_PRODUCTS[call.data[ATTR_PRODUCT]], SERVINGS[call.data[ATTR_SERVING]]
    )
    await coordinator.async_configure_food_safe(probe, program)


def _probe_for_device(
    hass: HomeAssistant, device_id: str
) -> tuple[CombustionCoordinator, Probe]:
    """Find the probe behind a device, and the coordinator that can command it."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None or device.model != PROBE_MODEL:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="not_a_probe"
        )
    identifiers = {id for domain, id in device.identifiers if domain == DOMAIN}
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        coordinator: CombustionCoordinator = entry.runtime_data
        for known in coordinator.devices.values():
            if isinstance(known, ProbeDevice) and known.identifier in identifiers:
                if not coordinator.enable_predictions:
                    raise ServiceValidationError(
                        translation_domain=DOMAIN,
                        translation_key="predictions_disabled",
                    )
                return coordinator, known.bluetooth
    # A probe not heard since Home Assistant started, or with the integration not loaded.
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="probe_not_heard",
        translation_placeholders={"probe": device.serial_number or device.name or ""},
    )
