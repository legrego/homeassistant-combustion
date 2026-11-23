"""Linking, unlinking, and signing in again to a MeatNet Cloud account."""

from contextlib import suppress

import pytest
from combustion_cloud import (
    ApiError,
    AuthenticationError,
    CloudConnectionError,
    PermissionDeniedError,
    ProtocolError,
    RateLimitError,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, UnknownFlow

from custom_components.combustion.const import (
    CONF_CLOUD,
    CONF_ENABLE_PREDICTIONS,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)
from tests.test_init import setup_entry
from tests.utils.cloud_utils import (
    CLOUD_DATA,
    EMAIL,
    PASSWORD,
    FakeCloud,
    settle_hass,
    tokens,
)

SIGN_IN = {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}

ERRORS = [
    ("login", AuthenticationError("Rejected"), "invalid_auth"),
    ("login", RateLimitError(60), "rate_limited"),
    ("login", CloudConnectionError("Interrupted"), "cannot_connect"),
    ("login", ApiError(400, "API_KEY_INVALID"), "service_error"),
    ("login", RuntimeError("Bug"), "unknown"),
    # The account must be readable too, before it is linked.
    ("get_account", PermissionDeniedError(403), "service_error"),
    ("get_account", ProtocolError("Unreadable"), "service_error"),
]


async def open_options(hass: HomeAssistant, entry, step: str) -> dict:
    """Open one step of the options."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["predictions", step]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == step
    return result


async def open_reauth(hass: HomeAssistant, cloud: FakeCloud):
    """Set up a linked account whose session is rejected."""
    cloud.failures["resume"] = [AuthenticationError("Rejected")]
    entry = await setup_entry(hass, data=CLOUD_DATA)
    await settle_hass(hass)
    (flow,) = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert flow["context"]["source"] == SOURCE_REAUTH
    assert flow["step_id"] == "reauth_confirm"
    return entry, flow["flow_id"]


async def test_link(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """Linking stores the session, but not the password, and connects."""
    # Entries from earlier versions hold data this version doesn't use.
    entry = await setup_entry(hass, data={"devices": []})
    hass.config_entries.async_update_entry(
        entry, options={CONF_ENABLE_PREDICTIONS: True}
    )
    await hass.async_block_till_done()
    result = await open_options(hass, entry, "link_cloud")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**SIGN_IN, CONF_EMAIL: f" {EMAIL} "}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cloud_linked"
    await settle_hass(hass)

    assert cloud.logins == [(EMAIL, PASSWORD)]
    assert entry.data == {"devices": [], **CLOUD_DATA}
    assert entry.options == {CONF_ENABLE_PREDICTIONS: True}
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.enable_predictions
    await cloud.connect()
    assert entry.runtime_data.cloud.connected
    assert cloud.clients[0].closed  # The client that signed in.


@pytest.mark.parametrize(("method", "failure", "error"), ERRORS)
async def test_link_errors(
    hass: HomeAssistant,
    cloud: FakeCloud,
    caplog: pytest.LogCaptureFixture,
    method: str,
    failure: Exception,
    error: str,
) -> None:
    """Failures are explained, link nothing, and can be retried."""
    entry = await setup_entry(hass)
    result = await open_options(hass, entry, "link_cloud")
    cloud.failures[method] = [failure]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], SIGN_IN
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    assert entry.data == {}
    assert cloud.clients[0].closed
    assert PASSWORD not in caplog.text

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], SIGN_IN
    )
    assert result["reason"] == "cloud_linked"
    await settle_hass(hass)
    assert entry.data == CLOUD_DATA


@pytest.mark.parametrize("blank", [{CONF_EMAIL: " "}, {CONF_PASSWORD: ""}])
async def test_link_blank(
    hass: HomeAssistant, cloud: FakeCloud, blank: dict[str, str]
) -> None:
    """Blank credentials are rejected without asking the service."""
    entry = await setup_entry(hass)
    result = await open_options(hass, entry, "link_cloud")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], SIGN_IN | blank
    )
    assert result["errors"] == {"base": "invalid_auth"}
    assert not cloud.clients


async def test_unlink(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """Unlinking forgets the session and disconnects."""
    entry = await setup_entry(hass, data={"devices": [], **CLOUD_DATA})
    await cloud.connect()
    result = await open_options(hass, entry, "unlink_cloud")
    assert result["description_placeholders"] == {"email": EMAIL}
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cloud_unlinked"
    await settle_hass(hass)
    assert entry.data == {"devices": []}
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.cloud is None
    assert cloud.clients[0].closed


async def test_unlink_after_rejection(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """Unlinking also withdraws a request to sign in again."""
    entry, _ = await open_reauth(hass, cloud)
    result = await open_options(hass, entry, "unlink_cloud")
    await hass.config_entries.options.async_configure(result["flow_id"], {})
    await settle_hass(hass)
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert entry.data == {}
    assert entry.runtime_data.cloud is None


async def test_unlink_twice(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """A second dialog for unlinking has nothing left to do."""
    entry = await setup_entry(hass, data=CLOUD_DATA)
    first = await open_options(hass, entry, "unlink_cloud")
    second = await open_options(hass, entry, "unlink_cloud")
    for result in (first, second):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {}
        )
        assert result["reason"] == "cloud_unlinked"
    await settle_hass(hass)
    assert entry.data == {}


@pytest.mark.parametrize(("method", "failure", "error"), ERRORS)
async def test_reauth_errors(
    hass: HomeAssistant, cloud: FakeCloud, method: str, failure: Exception, error: str
) -> None:
    """Signing in again explains failures, and can be retried."""
    entry, flow_id = await open_reauth(hass, cloud)
    cloud.failures[method] = [failure]
    result = await hass.config_entries.flow.async_configure(flow_id, SIGN_IN)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    assert entry.data == CLOUD_DATA

    cloud.login_tokens = tokens("second-token")
    result = await hass.config_entries.flow.async_configure(flow_id, SIGN_IN)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    await settle_hass(hass)
    assert entry.data[CONF_CLOUD][CONF_REFRESH_TOKEN] == "second-token"
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_reauth_wrong_account(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """Signing in again can't switch to a different account."""
    entry, flow_id = await open_reauth(hass, cloud)
    cloud.login_tokens = tokens("second-token", account_id="another-account")
    result = await hass.config_entries.flow.async_configure(flow_id, SIGN_IN)
    assert result["errors"] == {"base": "wrong_account"}
    assert entry.data == CLOUD_DATA


async def test_unlink_during_reauth(hass: HomeAssistant, cloud: FakeCloud) -> None:
    """An account unlinked while signing in again stays unlinked."""
    entry, flow_id = await open_reauth(hass, cloud)
    cloud.on_login = lambda: hass.config_entries.async_update_entry(entry, data={})
    # Home Assistant may already have dropped the flow, as unlinking reloads the entry.
    with suppress(UnknownFlow):
        result = await hass.config_entries.flow.async_configure(flow_id, SIGN_IN)
        assert result["reason"] == "cloud_unlinked"
    await settle_hass(hass)
    assert cloud.logins
    assert entry.data == {}
    assert entry.runtime_data.cloud is None
