"""Config flow for Combustion."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from combustion_ble import COMBUSTION_MANUFACTURER_ID, parse_advertisement
from combustion_cloud import (
    AuthenticationError,
    CloudConnectionError,
    CombustionCloudError,
    RateLimitError,
)
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .cloud import async_sign_in
from .const import (
    CONF_ACCOUNT_ID,
    CONF_CLOUD,
    CONF_ENABLE_PREDICTIONS,
    DOMAIN,
    LOGGER,
    MEATNET_UNIQUE_ID,
    NAME,
)


def _sign_in_schema(email: str = "") -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_EMAIL, default=email): TextSelector(
                TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
            ),
            vol.Required(CONF_PASSWORD): TextSelector(
                TextSelectorConfig(
                    type=TextSelectorType.PASSWORD, autocomplete="current-password"
                )
            ),
        }
    )


async def _async_try_sign_in(
    hass: HomeAssistant, user_input: Mapping[str, str]
) -> tuple[dict[str, str] | None, str | None]:
    """Sign in to MeatNet Cloud. Return what to store for the account, or an error key."""
    email = user_input[CONF_EMAIL].strip()
    password = user_input[CONF_PASSWORD]
    if not email or not password:
        return None, "invalid_auth"
    try:
        return await async_sign_in(hass, email, password), None
    except AuthenticationError:
        return None, "invalid_auth"
    except RateLimitError:
        return None, "rate_limited"
    except CloudConnectionError:
        return None, "cannot_connect"
    except CombustionCloudError as err:
        LOGGER.warning("MeatNet Cloud sign-in failed: %s: %s", type(err).__name__, err)
        return None, "service_error"
    except Exception:
        LOGGER.exception("Unexpected error signing in to MeatNet Cloud")
        return None, "unknown"


class CombustionFlowHandler(ConfigFlow, domain=DOMAIN):
    """Config flow for Combustion.

    A single entry covers every Combustion device in range. A probe can be heard through any MeatNet
    repeater, so devices are not configured individually.
    """

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> CombustionOptionsFlow:
        """Offer optional prediction connections, and a MeatNet Cloud account."""
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

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle a MeatNet Cloud session that is no longer accepted."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sign in to the linked account again."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            account, error = await _async_try_sign_in(self.hass, user_input)
            # Signing in took a while, so the account may have been unlinked since.
            if (linked := entry.data.get(CONF_CLOUD)) is None:
                return self.async_abort(reason="cloud_unlinked")
            if account is None:
                errors["base"] = error or "unknown"
            elif account[CONF_ACCOUNT_ID] != linked[CONF_ACCOUNT_ID]:
                errors["base"] = "wrong_account"
            else:
                # The update listener reconnects without reloading, which would drop Bluetooth
                # connections.
                return self.async_update_and_abort(
                    entry, data_updates={CONF_CLOUD: account}
                )
        email = (user_input or entry.data.get(CONF_CLOUD, {})).get(CONF_EMAIL, "")
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_sign_in_schema(email),
            errors=errors,
        )


class CombustionOptionsFlow(OptionsFlow):
    """Configure cooking predictions and a MeatNet Cloud account, both optional."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose what to configure."""
        linked = CONF_CLOUD in self.config_entry.data
        return self.async_show_menu(
            step_id="init",
            menu_options=["predictions", "unlink_cloud" if linked else "link_cloud"],
        )

    async def async_step_predictions(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enable or disable prediction connections."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="predictions",
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

    async def async_step_link_cloud(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sign in to a MeatNet Cloud account. Only its session is stored."""
        errors: dict[str, str] = {}
        if user_input is not None:
            account, error = await _async_try_sign_in(self.hass, user_input)
            if account is not None:
                self._async_update_data({**self.config_entry.data, CONF_CLOUD: account})
                return self.async_abort(reason="cloud_linked")
            errors["base"] = error or "unknown"
        return self.async_show_form(
            step_id="link_cloud",
            data_schema=_sign_in_schema((user_input or {}).get(CONF_EMAIL, "")),
            errors=errors,
        )

    async def async_step_unlink_cloud(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm unlinking the account."""
        entry = self.config_entry
        if (linked := entry.data.get(CONF_CLOUD)) is None:  # By another dialog.
            return self.async_abort(reason="cloud_unlinked")
        if user_input is None:
            return self.async_show_form(
                step_id="unlink_cloud",
                description_placeholders={"email": linked[CONF_EMAIL]},
            )
        self._async_update_data(
            {key: value for key, value in entry.data.items() if key != CONF_CLOUD}
        )
        return self.async_abort(reason="cloud_unlinked")

    @callback
    def _async_update_data(self, data: Mapping[str, Any]) -> None:
        """Change the linked account.

        The update listener reloads the entry, which also withdraws any request to sign in again.
        """
        self.hass.config_entries.async_update_entry(self.config_entry, data=data)
