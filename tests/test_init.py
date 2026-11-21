"""Test setup, entities, and availability."""

import time
from datetime import timedelta
from unittest.mock import PropertyMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.combustion import async_remove_config_entry_device
from custom_components.combustion.const import DOMAIN, MEATNET_UNIQUE_ID, STALE_TIMEOUT
from tests.conftest import FakeClock
from tests.utils.bt_utils import (
    GAUGE_ADDRESS,
    NODE_ADDRESS,
    gauge_payload,
    inject_bt_advertisement,
    node_payload,
    probe_payload,
    service_info,
)

# Unique ID prefix earlier versions used for probe 0x10001CCC: lowercase hex, no leading zeros.
PROBE = "10001ccc"
GAUGE = "GG00000001"


async def setup_entry(hass: HomeAssistant, data: dict | None = None) -> MockConfigEntry:
    """Set up the integration with a config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=MEATNET_UNIQUE_ID,
        title="Combustion MeatNet",
        data=data or {},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def get_state(hass: HomeAssistant, platform: str, unique_id: str) -> State:
    """State of the entity with ``unique_id``."""
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    assert entity_id is not None, f"no {platform} entity with unique ID {unique_id}"
    state = hass.states.get(entity_id)
    assert state is not None, f"{entity_id} has no state"
    return state


async def advance(hass: HomeAssistant, clock: FakeClock, seconds: float) -> None:
    """Advance MeatNet's clock and run the integration's once-per-second tick."""
    clock.advance(seconds)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1.1))
    await hass.async_block_till_done()


async def test_probe_entities_keep_legacy_unique_ids(hass: HomeAssistant) -> None:
    """Upgrading keeps existing entities, and their history, by reusing unique IDs."""
    # Entries created by earlier versions stored a list of addresses, which is now unused.
    entry = await setup_entry(
        hass,
        data={
            "devices": [
                {
                    "name": "Combustion Meatnet",
                    "address": "CC:CC:CC:CC:CC:01",
                    "product_type": 2,
                }
            ]
        },
    )
    entity_registry = er.async_get(hass)
    assert er.async_entries_for_config_entry(entity_registry, entry.entry_id) == []

    inject_bt_advertisement(hass, service_info(probe_payload()))
    await hass.async_block_till_done()

    entities = er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    thermistors = {f"{PROBE}--thermistor--{n}" for n in range(1, 9)}
    assert {(entity.domain, entity.unique_id) for entity in entities} == {
        ("sensor", f"{PROBE}--sensor--core"),
        ("sensor", f"{PROBE}--sensor--surface"),
        ("sensor", f"{PROBE}--sensor--ambient"),
        *(("sensor", unique_id) for unique_id in thermistors),
        ("sensor", f"{PROBE}--instant_read"),
        ("sensor", f"{PROBE}--rssi"),
        ("binary_sensor", f"{PROBE}--battery"),
    }
    assert {entity.unique_id for entity in entities if entity.disabled} == {
        *thermistors,
        f"{PROBE}--rssi",
    }

    assert {
        entity.unique_id
        for entity in entities
        if entity.entity_category is EntityCategory.DIAGNOSTIC
    } == {f"{PROBE}--battery", f"{PROBE}--rssi"}

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, PROBE), entry.entry_id
    )
    assert device is not None
    assert device.name == "Predictive Thermometer 10001CCC"
    assert device.serial_number == "10001CCC"
    assert device.manufacturer == "Combustion, Inc."


async def test_entity_names(hass: HomeAssistant) -> None:
    """Entity names come from translations or device classes."""
    entry = await setup_entry(hass)
    inject_bt_advertisement(hass, service_info(probe_payload()))
    inject_bt_advertisement(hass, service_info(gauge_payload(), address=GAUGE_ADDRESS))
    await hass.async_block_till_done()

    probe_name = "Predictive Thermometer 10001CCC"
    gauge_name = "Giant Grill Gauge GG00000001"
    expected = {
        ("sensor", f"{PROBE}--sensor--core"): f"{probe_name} Core temperature",
        ("sensor", f"{PROBE}--sensor--surface"): f"{probe_name} Surface temperature",
        ("sensor", f"{PROBE}--sensor--ambient"): f"{probe_name} Ambient temperature",
        ("sensor", f"{PROBE}--instant_read"): f"{probe_name} Instant read temperature",
        ("binary_sensor", f"{PROBE}--battery"): f"{probe_name} Battery",
        ("sensor", f"{GAUGE}--temperature"): f"{gauge_name} Temperature",
        ("binary_sensor", f"{GAUGE}--battery"): f"{gauge_name} Battery",
    }
    for (platform, unique_id), name in expected.items():
        assert get_state(hass, platform, unique_id).attributes["friendly_name"] == name

    # Disabled by default, so they have no state. Check the names they were registered with.
    original_names = {
        entity.unique_id: entity.original_name
        for entity in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
    }
    assert original_names[f"{PROBE}--thermistor--3"] == "Temperature 3"
    assert original_names[f"{PROBE}--rssi"] == "RSSI"


async def test_probe_states(hass: HomeAssistant) -> None:
    """Virtual sensors report the thermistor they read from."""
    await setup_entry(hass)
    inject_bt_advertisement(
        hass, service_info(probe_payload(core=2, surface=6, ambient=8))
    )
    await hass.async_block_till_done()

    core = get_state(hass, "sensor", f"{PROBE}--sensor--core")
    assert core.state == "21.1"
    assert core.attributes["thermistor_id"] == 2
    assert core.attributes["unit_of_measurement"] == "°C"
    surface = get_state(hass, "sensor", f"{PROBE}--sensor--surface")
    assert surface.state == "25.5"
    assert surface.attributes["thermistor_id"] == 6
    ambient = get_state(hass, "sensor", f"{PROBE}--sensor--ambient")
    assert ambient.state == "27.7"
    assert ambient.attributes["thermistor_id"] == 8

    assert get_state(hass, "sensor", f"{PROBE}--instant_read").state == STATE_UNKNOWN
    assert get_state(hass, "binary_sensor", f"{PROBE}--battery").state == STATE_OFF

    inject_bt_advertisement(hass, service_info(probe_payload(low_battery=True)))
    await hass.async_block_till_done()
    assert get_state(hass, "binary_sensor", f"{PROBE}--battery").state == STATE_ON


async def test_thermistor_and_rssi_sensors(hass: HomeAssistant) -> None:
    """Sensors that are disabled by default work once enabled."""
    with patch(
        "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
        new_callable=PropertyMock,
        return_value=True,
    ):
        await setup_entry(hass)
        # A probe heard only in instant read mode has no thermistor readings yet.
        inject_bt_advertisement(
            hass, service_info(probe_payload(instant_read=60.0), rssi=-70)
        )
        await hass.async_block_till_done()

    assert get_state(hass, "sensor", f"{PROBE}--thermistor--1").state == STATE_UNKNOWN
    assert get_state(hass, "sensor", f"{PROBE}--rssi").state == "-70"

    inject_bt_advertisement(hass, service_info(probe_payload()))
    await hass.async_block_till_done()
    for number, expected in enumerate(
        ("20.0", "21.1", "22.2", "23.3", "24.4", "25.5", "26.6", "27.7"), start=1
    ):
        state = get_state(hass, "sensor", f"{PROBE}--thermistor--{number}")
        assert state.state == expected


async def test_probe_heard_only_through_meatnet(hass: HomeAssistant) -> None:
    """A probe relayed by a repeater gets the same entities as one heard directly."""
    await setup_entry(hass)
    inject_bt_advertisement(
        hass,
        service_info(probe_payload(hop=0), address=NODE_ADDRESS, connectable=False),
    )
    await hass.async_block_till_done()

    assert get_state(hass, "sensor", f"{PROBE}--sensor--core").state == "20.0"


async def test_instant_read(hass: HomeAssistant) -> None:
    """Instant read survives the normal mode advertisements probes interleave with it."""
    await setup_entry(hass)
    inject_bt_advertisement(hass, service_info(probe_payload(instant_read=60.2)))
    await hass.async_block_till_done()
    assert get_state(hass, "sensor", f"{PROBE}--instant_read").state == "60.0"

    inject_bt_advertisement(hass, service_info(probe_payload()))
    await hass.async_block_till_done()
    assert get_state(hass, "sensor", f"{PROBE}--instant_read").state == "60.0"
    assert get_state(hass, "sensor", f"{PROBE}--sensor--core").state == "20.0"


async def test_probe_becomes_unavailable_then_recovers(
    hass: HomeAssistant, clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    """Entities go unavailable when a probe stops advertising, and recover when it returns."""
    await setup_entry(hass)
    inject_bt_advertisement(hass, service_info(probe_payload(), time=time.monotonic()))
    await hass.async_block_till_done()

    # Home Assistant can go a minute or more without passing along an advertisement from a weak
    # scanner, so a probe stays available well past the library's 15 second default.
    await advance(hass, clock, STALE_TIMEOUT - 1)
    assert get_state(hass, "sensor", f"{PROBE}--sensor--core").state == "20.0"

    await advance(hass, clock, 2)
    await advance(hass, clock, 1)
    assert (
        get_state(hass, "sensor", f"{PROBE}--sensor--core").state == STATE_UNAVAILABLE
    )
    assert (
        get_state(hass, "binary_sensor", f"{PROBE}--battery").state == STATE_UNAVAILABLE
    )

    inject_bt_advertisement(
        hass, service_info(probe_payload(low_battery=True), time=time.monotonic())
    )
    await hass.async_block_till_done()
    inject_bt_advertisement(hass, service_info(probe_payload(), time=time.monotonic()))
    await hass.async_block_till_done()
    assert get_state(hass, "sensor", f"{PROBE}--sensor--core").state == "20.0"

    # Logged once each way, however many updates follow.
    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("custom_components.combustion")
    ]
    assert messages == [
        f"Probe(10001CCC) is unavailable: not heard directly or through MeatNet for {STALE_TIMEOUT} seconds",
        "Probe(10001CCC) is available again",
    ]


async def test_identical_advertisements_keep_probe_available(
    hass: HomeAssistant, clock: FakeClock
) -> None:
    """A probe whose readings hold steady stays available.

    Home Assistant doesn't call back for an advertisement identical to the previous one, so the
    integration catches up from Home Assistant's Bluetooth history.
    """
    await setup_entry(hass)
    start = time.monotonic()
    inject_bt_advertisement(hass, service_info(probe_payload(), time=start))
    await hass.async_block_till_done()

    for step in range(1, 5):
        clock.advance(9)
        inject_bt_advertisement(hass, service_info(probe_payload(), time=start + step))
        await advance(hass, clock, 1)
        assert get_state(hass, "sensor", f"{PROBE}--sensor--core").state == "20.0"


async def test_gauge(hass: HomeAssistant) -> None:
    """Giant Grill Gauges get a temperature sensor and a battery sensor."""
    entry = await setup_entry(hass)
    inject_bt_advertisement(hass, service_info(gauge_payload(), address=GAUGE_ADDRESS))
    await hass.async_block_till_done()

    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert {(entity.domain, entity.unique_id) for entity in entities} == {
        ("sensor", f"{GAUGE}--temperature"),
        ("sensor", f"{GAUGE}--rssi"),
        ("binary_sensor", f"{GAUGE}--battery"),
    }
    assert get_state(hass, "sensor", f"{GAUGE}--temperature").state == "150.0"
    assert get_state(hass, "binary_sensor", f"{GAUGE}--battery").state == STATE_OFF

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, GAUGE), entry.entry_id
    )
    assert device is not None
    assert device.name == "Giant Grill Gauge GG00000001"

    inject_bt_advertisement(
        hass,
        service_info(
            gauge_payload(temperature=None, low_battery=True), address=GAUGE_ADDRESS
        ),
    )
    await hass.async_block_till_done()
    assert get_state(hass, "sensor", f"{GAUGE}--temperature").state == STATE_UNKNOWN
    assert get_state(hass, "binary_sensor", f"{GAUGE}--battery").state == STATE_ON


async def test_repeater_alone_creates_no_entities(hass: HomeAssistant) -> None:
    """Repeaters themselves don't get entities, and neither does an empty probe slot."""
    entry = await setup_entry(hass)
    inject_bt_advertisement(hass, service_info(node_payload(), address=NODE_ADDRESS))
    inject_bt_advertisement(
        hass, service_info(probe_payload(serial_number=0, hop=0), address=NODE_ADDRESS)
    )
    await hass.async_block_till_done()

    assert er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id) == []


async def test_remove_device(hass: HomeAssistant) -> None:
    """Only devices that haven't been heard since startup can be removed."""
    entry = await setup_entry(hass)
    inject_bt_advertisement(hass, service_info(probe_payload()))
    await hass.async_block_till_done()

    device_registry = dr.async_get(hass)
    heard = device_registry.async_get_device_by_identifier(
        (DOMAIN, PROBE), entry.entry_id
    )
    assert heard is not None
    stale = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "10009999")}
    )

    assert not await async_remove_config_entry_device(hass, entry, heard)
    assert await async_remove_config_entry_device(hass, entry, stale)


async def test_unload(hass: HomeAssistant) -> None:
    """Unloading stops listening for advertisements."""
    entry = await setup_entry(hass)
    inject_bt_advertisement(hass, service_info(probe_payload()))
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED

    entity_registry = er.async_get(hass)
    entities_before = er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    inject_bt_advertisement(hass, service_info(probe_payload(serial_number=0x10002DDD)))
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done()
    assert (
        er.async_entries_for_config_entry(entity_registry, entry.entry_id)
        == entities_before
    )


async def test_reload(hass: HomeAssistant, clock: FakeClock) -> None:
    """Reloading preserves entities, resumes updates, and discovers new devices."""
    entry = await setup_entry(hass)
    inject_bt_advertisement(hass, service_info(probe_payload()))
    await hass.async_block_till_done()
    entity_registry = er.async_get(hass)
    core_id = get_state(hass, "sensor", f"{PROBE}--sensor--core").entity_id
    entities_before = er.async_entries_for_config_entry(entity_registry, entry.entry_id)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    inject_bt_advertisement(hass, service_info(probe_payload(core=2, low_battery=True)))
    await hass.async_block_till_done()
    core = get_state(hass, "sensor", f"{PROBE}--sensor--core")
    assert core.entity_id == core_id
    assert core.state == "21.1"
    assert get_state(hass, "binary_sensor", f"{PROBE}--battery").state == STATE_ON
    assert (
        er.async_entries_for_config_entry(entity_registry, entry.entry_id)
        == entities_before
    )

    inject_bt_advertisement(hass, service_info(gauge_payload(), address=GAUGE_ADDRESS))
    await hass.async_block_till_done()
    assert get_state(hass, "sensor", f"{GAUGE}--temperature").state == "150.0"
    assert get_state(hass, "binary_sensor", f"{GAUGE}--battery").state == STATE_OFF

    # The expiry timer must also be running again after the reload.
    await advance(hass, clock, STALE_TIMEOUT + 1)
    assert (
        get_state(hass, "sensor", f"{PROBE}--sensor--core").state == STATE_UNAVAILABLE
    )
    assert get_state(hass, "sensor", f"{GAUGE}--temperature").state == STATE_UNAVAILABLE
