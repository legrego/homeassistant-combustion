"""Config flow for Combustion."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from combustion_ble import COMBUSTION_MANUFACTURER_ID, parse_advertisement
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback

from .const import CONF_ENABLE_PREDICTIONS, DOMAIN, MEATNET_UNIQUE_ID, NAME


class CombustionFlowHandler(ConfigFlow, domain=DOMAIN):
    """Config flow for Combustion.

    A single entry covers every Combustion device in range. A probe can be heard through any MeatNet
    repeater, so devices are not configured individually.
    """

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> CombustionOptionsFlow:
        """Offer optional direct prediction connections."""
        return CombustionOptionsFlow()

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a Combustion device discovered over Bluetooth."""
        await self.async_set_unique_id(MEATNET_UNIQUE_ID)
        self._abort_if_unique_id_configured()

        payload = discovery_info.manufacturer_data.get(COMBUSTION_MANUFACTURER_ID)
        if payload is None or parse_advertisement(payload) is None:
            return self.async_abort(reason="not_supported")

        self.context["title_placeholders"] = {"name": NAME}
        return await self.async_step_confirm()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle setup started by the user."""
        await self.async_set_unique_id(MEATNET_UNIQUE_ID)
        self._abort_if_unique_id_configured()
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm setup."""
        if user_input is not None:
            return self.async_create_entry(title=NAME, data={})

        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm", description_placeholders={"name": NAME}
        )


class CombustionOptionsFlow(OptionsFlow):
    """Configure read-only cooking predictions."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enable or disable direct probe connections."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_ENABLE_PREDICTIONS,
                        default=self.config_entry.options.get(
                            CONF_ENABLE_PREDICTIONS, False
                        ),
                    ): bool,
                }
            ),
        )
