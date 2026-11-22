"""SafeCook food safety sensors, read from the same connected status as predictions."""

import pytest
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant

from custom_components.combustion.const import CONF_ENABLE_PREDICTIONS
from custom_components.combustion.sensor import FOOD_SAFETY_SENSORS
from tests.test_init import PROBE, get_state, setup_entry
from tests.test_prediction import connection, food_safe_bytes, status_packet  # noqa: F401
from tests.test_repeater import (  # noqa: F401
    connections,
    enable_predictions,
    node_status_frame,
    relay,
    tick,
)
from tests.utils.bt_utils import NODE_ADDRESS, inject_bt_advertisement, probe_payload
from tests.utils.bt_utils import service_info as make_service_info

SELECT_KEYS = ["food_safety_product", "food_safety_serving"]
KEYS = [description.key for description in FOOD_SAFETY_SENSORS] + SELECT_KEYS


def states(hass: HomeAssistant) -> dict[str, str]:
    """State of every SafeCook sensor and select, by description key."""
    return {
        key: get_state(
            hass, "select" if key in SELECT_KEYS else "sensor", f"{PROBE}--{key}"
        ).state
        for key in KEYS
    }


async def connect_direct(hass: HomeAssistant, connection):  # noqa: F811
    """Set up predictions and connect to the probe directly."""
    client, _ = connection
    entry = await setup_entry(hass)
    hass.config_entries.async_update_entry(
        entry, options={CONF_ENABLE_PREDICTIONS: True}
    )
    await hass.async_block_till_done()
    inject_bt_advertisement(hass, make_service_info(probe_payload()))
    await hass.async_block_till_done()
    await tick(hass, entry)  # The selects learn of the connection on the next tick.

    async def send(**kwargs):
        client.start_notify.call_args.args[1](None, status_packet(**kwargs))
        await hass.async_block_till_done()

    return entry, client, send


async def test_integrated_program(hass: HomeAssistant, connection):  # noqa: F811
    """An Integrated program reports its progress, and clears on disconnect."""
    entry, client, send = await connect_direct(hass, connection)
    assert set(states(hass).values()) == {STATE_UNKNOWN}

    await send(food_safe=food_safe_bytes())
    assert states(hass) == {
        "food_safety": "not_safe",
        "food_safety_threshold": "54.4",
        "food_safety_time_above_threshold": "300",
        "food_safety_log_reduction": "2.3",
        "food_safety_target_log_reduction": "6.5",
        "food_safety_product": "integrated_meats_ground",
        "food_safety_serving": "served_immediately",
    }
    threshold = get_state(hass, "sensor", f"{PROBE}--food_safety_threshold")
    # Part of the program, so it records no long-term statistics.
    assert "state_class" not in threshold.attributes

    await send(
        food_safe=food_safe_bytes(
            state=1, log_reduction=6.5, serving=1, seconds_above_threshold=900
        )
    )
    assert get_state(hass, "sensor", f"{PROBE}--food_safety").state == "safe"
    assert (
        get_state(hass, "select", f"{PROBE}--food_safety_serving").state
        == "cooked_and_chilled"
    )

    client.is_connected = False
    _, establish = connection
    establish.call_args.kwargs["disconnected_callback"](client)
    await hass.async_block_till_done()
    assert set(states(hass).values()) == {STATE_UNKNOWN}
    # The selects learn that the connection ended on the next tick.
    await tick(hass, entry)
    assert states(hass) == dict.fromkeys(KEYS, STATE_UNKNOWN) | dict.fromkeys(
        SELECT_KEYS, STATE_UNAVAILABLE
    )
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_simplified_program(hass: HomeAssistant, connection):  # noqa: F811
    """Simplified mode has no log reduction, so those sensors stay unknown."""
    entry, _, send = await connect_direct(hass, connection)
    await send(
        food_safe=food_safe_bytes(
            mode=0, product=1, threshold=73.9, state=2, log_reduction=0
        )
    )
    result = states(hass)
    assert result["food_safety"] == "safety_impossible"
    assert result["food_safety_product"] == "simplified_any_poultry"
    assert result["food_safety_threshold"] == "73.9"
    assert result["food_safety_log_reduction"] == STATE_UNKNOWN
    assert result["food_safety_target_log_reduction"] == STATE_UNKNOWN
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(
    "program",
    [
        # Integrated product 3 is deprecated, so the library keeps it as an integer.
        {"mode": 1, "product": 3, "state": 5, "serving": 4},
        # An unknown mode leaves the product uninterpreted.
        {"mode": 5, "product": 1},
    ],
)
async def test_unknown_values(hass: HomeAssistant, connection, program):  # noqa: F811
    """Values this integration doesn't know are unknown rather than errors."""
    entry, _, send = await connect_direct(hass, connection)
    await send(food_safe=food_safe_bytes(**program))
    result = states(hass)
    assert result["food_safety_product"] == STATE_UNKNOWN
    if "state" in program:
        assert result["food_safety"] == STATE_UNKNOWN
        assert result["food_safety_serving"] == STATE_UNKNOWN
    else:
        assert result["food_safety_log_reduction"] == STATE_UNKNOWN
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_firmware_without_food_safety(hass: HomeAssistant, connection):  # noqa: F811
    """Probes whose firmware predates SafeCook still get predictions, and unknown SafeCook."""
    entry, _, send = await connect_direct(hass, connection)
    await send()
    assert get_state(hass, "sensor", f"{PROBE}--prediction_remaining").state == "120"
    assert set(states(hass).values()) == {STATE_UNKNOWN}
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_food_safety_through_repeater(hass: HomeAssistant, connections):  # noqa: F811
    """A repeater's relayed status carries SafeCook data too."""
    entry = await enable_predictions(hass)
    relay(hass)
    await hass.async_block_till_done()
    await tick(hass, entry)
    connections.emit(
        NODE_ADDRESS, node_status_frame(food_safe=food_safe_bytes(state=1))
    )
    await hass.async_block_till_done()
    assert get_state(hass, "sensor", f"{PROBE}--food_safety").state == "safe"
    assert (
        get_state(hass, "select", f"{PROBE}--food_safety_product").state
        == "integrated_meats_ground"
    )
    assert await hass.config_entries.async_unload(entry.entry_id)


# Food safe data and status bytes relayed by a Booster, from capture reports on 2026-11-21.
CAPTURED_OFF = bytes(18)
CAPTURED_POULTRY = bytes.fromhex("010084c40fc0129c6904") + bytes(8)
CAPTURED_MEATS_GROUND = bytes.fromhex("1100e8840ec012541404") + bytes(8)


@pytest.mark.parametrize(
    ("food_safe", "expected"),
    [
        (
            CAPTURED_POULTRY,
            {
                "food_safety": "not_safe",
                "food_safety_threshold": "57.8",
                "food_safety_time_above_threshold": "0",
                "food_safety_log_reduction": "0.0",
                "food_safety_target_log_reduction": "7.0",
                "food_safety_product": "integrated_poultry",
                "food_safety_serving": "served_immediately",
            },
        ),
        (
            CAPTURED_MEATS_GROUND,
            {
                "food_safety": "not_safe",
                "food_safety_threshold": "62.8",
                "food_safety_time_above_threshold": "0",
                "food_safety_log_reduction": "0.0",
                "food_safety_target_log_reduction": "6.5",
                "food_safety_product": "integrated_meats_ground",
                "food_safety_serving": "served_immediately",
            },
        ),
        # Turning SafeCook off in the app writes an all-zero program.
        (CAPTURED_OFF, dict.fromkeys(KEYS, STATE_UNKNOWN) | {"food_safety": "off"}),
    ],
    ids=["poultry", "meats_ground", "off"],
)
async def test_captured_programs(
    hass: HomeAssistant,
    connections,  # noqa: F811
    food_safe,
    expected,
):
    """Programs captured from real hardware decode to the expected sensor states."""
    entry = await enable_predictions(hass)
    relay(hass)
    await hass.async_block_till_done()
    await tick(hass, entry)
    connections.emit(NODE_ADDRESS, node_status_frame(food_safe=food_safe))
    await hass.async_block_till_done()
    assert states(hass) == expected
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_turning_safecook_off(hass: HomeAssistant, connection):  # noqa: F811
    """Turning SafeCook off clears the program sensors, and turning it on restores them."""
    entry, _, send = await connect_direct(hass, connection)
    await send(food_safe=food_safe_bytes())
    assert get_state(hass, "sensor", f"{PROBE}--food_safety").state == "not_safe"
    await send(food_safe=CAPTURED_OFF)
    assert states(hass) == dict.fromkeys(KEYS, STATE_UNKNOWN) | {"food_safety": "off"}
    # A Simplified program for the default product is a real program, not off.
    await send(food_safe=food_safe_bytes(mode=0, product=0, threshold=73.9))
    assert get_state(hass, "sensor", f"{PROBE}--food_safety").state == "not_safe"
    assert (
        get_state(hass, "select", f"{PROBE}--food_safety_product").state
        == "simplified_default"
    )
    assert await hass.config_entries.async_unload(entry.entry_id)
