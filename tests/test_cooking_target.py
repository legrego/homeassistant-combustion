"""Setting and cancelling a probe's cooking target from Home Assistant."""

import asyncio
from time import monotonic

import pytest
from bleak.exc import BleakError
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.components.number import ATTR_VALUE, SERVICE_SET_VALUE
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.combustion.const import DOMAIN, MEATNET_UNIQUE_ID
from custom_components.combustion.prediction import RECONNECT_DELAY
from tests.test_init import PROBE, get_state, setup_entry
from tests.test_prediction import connection, notify  # noqa: F401 - fixture
from tests.test_repeater import (
    SET_PREDICTION,
    connections,  # noqa: F401 - fixture
    enable_predictions,
    node_status_frame,
    relay,
    stall,
    tick,
)
from tests.utils.bt_utils import (
    NODE_ADDRESS,
    PROBE_SERIAL,
    inject_bt_advertisement,
    probe_payload,
    service_info,
)

TARGET = f"{PROBE}--prediction_target"
CANCEL = f"{PROBE}--prediction_cancel"


def entity_id(hass: HomeAssistant, platform: str, unique_id: str) -> str:
    """Look up an entity ID by unique ID."""
    found = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    assert found is not None
    return found


async def set_target(hass: HomeAssistant, value: float) -> None:
    """Set the probe's cooking target through the number entity."""
    await hass.services.async_call(
        "number",
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id(hass, "number", TARGET), ATTR_VALUE: value},
        blocking=True,
    )


async def cancel_target(hass: HomeAssistant) -> None:
    """Press the probe's cancel button."""
    await hass.services.async_call(
        "button",
        SERVICE_PRESS,
        {ATTR_ENTITY_ID: entity_id(hass, "button", CANCEL)},
        blocking=True,
    )


def sent_prediction(frame: bytes) -> tuple[int, int]:
    """Decode the mode and set point (in 0.1 Celsius) from a Set Prediction payload."""
    raw = int.from_bytes(frame[-2:], "little")
    return raw >> 10, raw & 0x3FF


async def connect_direct(hass: HomeAssistant, client):
    """Enable predictions and connect directly to the probe."""
    client.commands_expected = True
    entry = await enable_predictions(hass)
    inject_bt_advertisement(hass, service_info(probe_payload()))
    await hass.async_block_till_done()
    await tick(hass, entry)  # The controls learn of the connection on the next tick.
    return entry


async def test_set_and_cancel_directly(hass: HomeAssistant, connection):  # noqa: F811
    """The target is set and cancelled over a direct connection; status confirms it."""
    client, _ = connection
    entry = await connect_direct(hass, client)
    assert get_state(hass, "number", TARGET).state == STATE_UNKNOWN

    await set_target(hass, 54.4)
    assert sent_prediction(client.write_gatt_char.call_args.args[1]) == (1, 544)
    # The number follows the probe's status, not the command.
    assert get_state(hass, "number", TARGET).state == STATE_UNKNOWN
    notify(client, target=54.4)
    await hass.async_block_till_done()
    assert get_state(hass, "number", TARGET).state == "54.4"

    await cancel_target(hass)
    assert sent_prediction(client.write_gatt_char.call_args.args[1]) == (0, 0)
    notify(client, mode=0)
    await hass.async_block_till_done()
    assert get_state(hass, "number", TARGET).state == STATE_UNKNOWN
    client.disconnect.assert_not_awaited()
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_fahrenheit_target_is_clamped(hass: HomeAssistant, connection):  # noqa: F811
    """The highest target shown in Fahrenheit converts back within the probe's range."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    client, _ = connection
    await connect_direct(hass, client)
    state = get_state(hass, "number", TARGET)
    assert state.attributes["unit_of_measurement"] == "°F"
    await set_target(hass, state.attributes["max"])
    assert sent_prediction(client.write_gatt_char.call_args.args[1]) == (1, 1023)


async def test_direct_rejection_keeps_session(hass: HomeAssistant, connection):  # noqa: F811
    """A probe that rejects the target raises a translated error and stays connected."""
    client, _ = connection
    client.command_success = False
    await connect_direct(hass, client)
    with pytest.raises(HomeAssistantError) as err:
        await set_target(hass, 60)
    assert err.value.translation_key == "command_rejected"
    client.disconnect.assert_not_awaited()
    assert get_state(hass, "number", TARGET).state != STATE_UNAVAILABLE


async def test_direct_failure_reconnects(hass: HomeAssistant, connection):  # noqa: F811
    """A failed direct command closes its session, so the connection is released."""
    client, establish = connection
    entry = await connect_direct(hass, client)
    client.write_gatt_char.side_effect = BleakError("write failed")
    with pytest.raises(HomeAssistantError) as err:
        await cancel_target(hass)
    assert err.value.translation_key == "not_connected"
    await hass.async_block_till_done()
    client.disconnect.assert_awaited_once()
    # The controls learn that the connection ended on the next tick.
    await tick(hass, entry)
    assert get_state(hass, "number", TARGET).state == STATE_UNAVAILABLE
    assert get_state(hass, "button", CANCEL).state == STATE_UNAVAILABLE


async def test_failure_interrupts_revision_read(hass: HomeAssistant, connection):  # noqa: F811
    """A failed command ends the session at once, even while a revision read is stalled."""
    client, _ = connection
    client.read_gatt_char.side_effect = stall
    entry = await connect_direct(hass, client)
    await settle(hass)
    client.read_gatt_char.assert_awaited()
    client.write_gatt_char.side_effect = BleakError("write failed")
    with pytest.raises(HomeAssistantError) as err:
        await cancel_target(hass)
    assert err.value.translation_key == "not_connected"
    await settle(hass)
    client.disconnect.assert_awaited_once()
    # The session ran, so it reconnects after the usual delay, not the failure backoff.
    monitor = entry.runtime_data.predictions[PROBE_SERIAL]
    assert monitor._retry_at <= monotonic() + RECONNECT_DELAY
    await tick(hass, entry)
    assert get_state(hass, "number", TARGET).state == STATE_UNAVAILABLE


async def test_unavailable_without_connection(hass: HomeAssistant, connection):  # noqa: F811
    """Without a prediction connection, the controls are unavailable and commands fail."""
    client, establish = connection
    entry = await enable_predictions(hass)
    # Heard only through a repeater that isn't connected, so nothing can reach the probe.
    inject_bt_advertisement(
        hass, service_info(probe_payload(hop=0), address=NODE_ADDRESS)
    )
    await hass.async_block_till_done()
    assert get_state(hass, "number", TARGET).state == STATE_UNAVAILABLE
    assert get_state(hass, "button", CANCEL).state == STATE_UNAVAILABLE
    probe = entry.runtime_data.meatnet.probes[PROBE_SERIAL]
    with pytest.raises(HomeAssistantError) as err:
        await entry.runtime_data.async_set_prediction(probe, 60)
    assert err.value.translation_key == "not_connected"
    assert err.value.translation_placeholders == {"probe": "10001CCC"}


async def settle(hass: HomeAssistant) -> None:
    """Let the repeater's background session finish its request round trips."""
    for _ in range(20):
        await asyncio.sleep(0)
    await hass.async_block_till_done()


async def test_set_and_cancel_through_repeater(hass: HomeAssistant, connections):  # noqa: F811
    """A probe reported by the repeater is commanded through it, with its serial number."""
    connections.commands_expected = True
    entry = await enable_predictions(hass)
    relay(hass)
    await tick(hass, entry)
    assert get_state(hass, "number", TARGET).state == STATE_UNAVAILABLE
    connections.emit(NODE_ADDRESS, node_status_frame(mode=0))
    await tick(hass, entry)
    await settle(hass)
    assert get_state(hass, "number", TARGET).state == STATE_UNKNOWN

    await set_target(hass, 63)
    node = connections.clients[NODE_ADDRESS]
    frame = node.write_gatt_char.call_args.args[1]
    assert frame[4] == SET_PREDICTION
    assert int.from_bytes(frame[10:14], "little") == PROBE_SERIAL
    assert sent_prediction(frame) == (1, 630)
    connections.emit(NODE_ADDRESS, node_status_frame(target=63))
    await hass.async_block_till_done()
    assert get_state(hass, "number", TARGET).state == "63.0"

    await cancel_target(hass)
    assert sent_prediction(node.write_gatt_char.call_args.args[1]) == (0, 0)

    # A rejection leaves the repeater session running.
    connections.command_success = False
    with pytest.raises(HomeAssistantError) as err:
        await set_target(hass, 70)
    assert err.value.translation_key == "command_rejected"
    node.disconnect.assert_not_awaited()
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_legacy_target_sensor_is_removed(hass: HomeAssistant):
    """The read-only target sensor that the number replaced is removed from the registry."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MEATNET_UNIQUE_ID)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create("sensor", DOMAIN, TARGET, config_entry=entry)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert not registry.async_get_entity_id("sensor", DOMAIN, TARGET)


async def test_controls_need_predictions(hass: HomeAssistant):
    """Without the predictions option, no cooking target controls are added."""
    await setup_entry(hass)
    inject_bt_advertisement(hass, service_info(probe_payload()))
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    assert not registry.async_get_entity_id("number", DOMAIN, TARGET)
    assert not registry.async_get_entity_id("button", DOMAIN, CANCEL)
