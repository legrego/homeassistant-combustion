"""Test manual setup using cached Bluetooth advertisements."""
from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.combustion.const import CONF_DEVICES, DOMAIN
from tests.utils.bt_utils import create_advertisement, create_combustion_bits

CACHE = "custom_components.combustion.config_flow.bluetooth.async_discovered_service_info"


async def test_manual_setup(hass: HomeAssistant) -> None:
    """Confirm cached probes in one entry without requiring connectability."""
    first = create_advertisement(create_combustion_bits())
    first.connectable = False
    second = create_advertisement(create_combustion_bits(serial_number="10001ccd"))
    second.address = "dd:dd:dd:dd:dd:dd"
    unrelated = create_advertisement(create_combustion_bits())
    unrelated.manufacturer_data = {1234: b"unrelated"}
    unsupported = create_advertisement(b"\x05" + b"\x01" * 21)
    with patch(CACHE, return_value=[unrelated, unsupported, first, second]) as cache:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    cache.assert_called_once_with(hass, connectable=False)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert not hass.config_entries.async_entries(DOMAIN)

    with patch("custom_components.combustion.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={}
        )
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Combustion Meatnet"
    assert result["result"].unique_id == "combustion_meatnet"
    assert {device["address"] for device in result["data"][CONF_DEVICES]} == {
        first.address, second.address
    }


@pytest.mark.parametrize("payload", [None, b"", b"\x05" + b"\x01" * 21,
                                    create_combustion_bits(serial_number="00000000")])
async def test_manual_no_supported_probes(hass: HomeAssistant, payload) -> None:
    """Explain missing probes for empty, malformed, unsupported or no-probe data."""
    devices = [] if payload is None else [create_advertisement(payload)]
    with patch(CACHE, return_value=devices):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_manual_already_configured(hass: HomeAssistant) -> None:
    """Keep a single entry without scanning when Combustion is configured."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id="combustion_meatnet")
    entry.add_to_hass(hass)
    with patch(CACHE) as cache:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    cache.assert_not_called()
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_manual_discovery_in_progress(hass: HomeAssistant) -> None:
    """Direct users to an existing Bluetooth confirmation without duplicating it."""
    discovery = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=create_advertisement(create_combustion_bits()),
    )
    assert discovery["step_id"] == "confirm"
    with patch(CACHE) as cache:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    cache.assert_not_called()
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_in_progress"
    assert len(hass.config_entries.flow.async_progress()) == 1
