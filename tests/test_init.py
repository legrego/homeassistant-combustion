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
    disabled_sensors = [e for e in sensors if e.disabled is True]
    binary_sensors = [e for e in entities if e.domain == 'binary_sensor']

    assert len(entities) == 13
    assert len(sensors) == 12
    # 9 disabled by default: 8 temperature sensors, and 1 RSSI sensor
    assert len(disabled_sensors) == 9
    assert len(binary_sensors) == 1


@pytest.mark.asyncio
async def test_listeners_removed_with_entities(hass: HomeAssistant):
    """Entity update listeners must not outlive their entities.

    Regression test for the event storm in #68: listeners registered at
    entity construction time were never removed, so every retried entity
    creation or reload grew the listener list until a single advertisement
    fanned out into an event storm ("more than 10000 events were queued").
    """

    mock_entry = MockConfigEntry(
        unique_id="test_listener_cleanup",
        domain=DOMAIN,
        version=1,
        data={},
        title="Meatnet",
    )

    await _setup_config_entry(hass, mock_entry)

    # The first advertisement for a new address records it on the config
    # entry, which reloads the integration once (fresh ProbeManager). Inject
    # again afterwards so entities exist under the current manager.
    inject_bt_advertisement(hass, create_advertisement(create_combustion_bits()))
    await hass.async_block_till_done()
    probe_manager = hass.data[DOMAIN]
    inject_bt_advertisement(hass, create_advertisement(create_combustion_bits()))
    await hass.async_block_till_done()

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    # A known address must not reload the entry again (each leaked reload
    # used to add another update listener, multiplying future reloads).
    assert hass.data[DOMAIN] is probe_manager
    assert len(entry.update_listeners) == 1
    assert len(probe_manager._listeners) > 0

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert len(probe_manager._listeners) == 0


@pytest.mark.asyncio
async def test_unknown_product_type_ignored(hass: HomeAssistant):
    """Advertisements from unknown Combustion products must be ignored.

    The Booster (product type 5) shares the Combustion manufacturer id, so
    its advertisements reach the bluetooth callback; parsing them used to
    raise ValueError into the bluetooth manager on every advertisement.
    """

    mock_entry = MockConfigEntry(
        unique_id="test_unknown_product_type",
        domain=DOMAIN,
        version=1,
        data={},
        title="Meatnet",
    )

    entry = await _setup_config_entry(hass, mock_entry)

    # Real Booster advertisement payload (product type 0x05).
    booster_bits = bytes.fromhex('0543523130303034304138' + '00' * 11)
    inject_bt_advertisement(hass, create_advertisement(booster_bits))
    await hass.async_block_till_done()

    er = entity_registry.async_get(hass)
    entities = entity_registry.async_entries_for_config_entry(er, entry.entry_id)
    assert len(entities) == 0
