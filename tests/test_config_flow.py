"""Test the config flow."""

from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.combustion.const import DOMAIN, MEATNET_UNIQUE_ID
from tests.utils.bt_utils import NODE_ADDRESS, node_payload, probe_payload, service_info


async def test_bluetooth_discovery(hass: HomeAssistant) -> None:
    """A discovered probe offers to set up the MeatNet."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info(probe_payload()),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["description_placeholders"] == {"name": "Combustion MeatNet"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Combustion MeatNet"
    assert result["data"] == {}
    assert result["result"].unique_id == MEATNET_UNIQUE_ID


async def test_bluetooth_discovery_of_repeater(hass: HomeAssistant) -> None:
    """A repeater with no probes is enough to offer setup."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info(node_payload(), address=NODE_ADDRESS),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"


async def test_bluetooth_discovery_already_setup(hass: HomeAssistant) -> None:
    """Discovery aborts when the MeatNet is already set up."""
    MockConfigEntry(domain=DOMAIN, unique_id=MEATNET_UNIQUE_ID).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info(probe_payload()),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_bluetooth_discovery_unsupported(hass: HomeAssistant) -> None:
    """Discovery aborts for advertisements the library can't parse."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info(bytes([0x07]) + bytes(22)),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


async def test_user_setup(hass: HomeAssistant) -> None:
    """The MeatNet can be set up by hand."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == MEATNET_UNIQUE_ID


async def test_user_setup_already_setup(hass: HomeAssistant) -> None:
    """Manual setup aborts when the MeatNet is already set up."""
    MockConfigEntry(domain=DOMAIN, unique_id=MEATNET_UNIQUE_ID).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
