"""Test initialization."""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.combustion.const import DOMAIN
from tests.utils.bt_utils import (
    create_advertisement,
    create_combustion_bits,
    inject_bt_advertisement,
)


async def _setup_config_entry(hass: HomeAssistant, mock_entry: MockConfigEntry):
    mock_entry.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {}) is True
    await hass.async_block_till_done()

    config_entries = hass.config_entries.async_entries(DOMAIN)
    assert len(config_entries) == 1
    entry = config_entries[0]

    return entry

@pytest.mark.asyncio
async def test_entity_creation(hass: HomeAssistant):
    """Verify that entities are created in response to a BT advertisement."""

    mock_entry = MockConfigEntry(
        unique_id="test_entity_creation",
        domain=DOMAIN,
        version=1,
        data={
        },
        title="Meatnet",
    )

    entry = await _setup_config_entry(hass, mock_entry)

    er = entity_registry.async_get(hass)
    entities = entity_registry.async_entries_for_config_entry(er, entry.entry_id)
    assert len(entities) == 0

    inject_bt_advertisement(hass, create_advertisement(create_combustion_bits()))
    await hass.async_block_till_done()

    entities = entity_registry.async_entries_for_config_entry(er, entry.entry_id)
    sensors = [e for e in entities if e.domain == 'sensor']
    binary_sensors = [e for e in entities if e.domain == 'binary_sensor']

    assert len(entities) == 13
    assert len(sensors) == 12
    assert len(binary_sensors) == 1
