"""Configuring, resetting, and turning off SafeCook from Home Assistant."""

from time import monotonic

import pytest
import voluptuous as vol
from combustion_ble import FoodSafeMode, MeatNet, Serving, SimplifiedProduct
from combustion_ble.ble_data.food_safe_data import FoodSafeData
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.const import ATTR_DEVICE_ID, ATTR_ENTITY_ID, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.combustion.const import DOMAIN
from custom_components.combustion.prediction import RepeaterMonitor
from tests.test_cooking_target import connect_direct, entity_id, settle
from tests.test_init import GAUGE, PROBE, get_state, setup_entry
from tests.test_prediction import (  # noqa: F401
    connection,
    food_safe_bytes,
    make_monitor,
    notify,
)
from tests.test_repeater import (
    CONFIGURE_FOOD_SAFE,
    RESET_FOOD_SAFE,
    connections,  # noqa: F401 - fixture
    enable_predictions,
    node_status_frame,
    relay,
    tick,
)
from tests.utils.bt_utils import (
    GAUGE_ADDRESS,
    NODE_ADDRESS,
    PROBE_SERIAL,
    gauge_payload,
    inject_bt_advertisement,
    probe_payload,
    service_info,
)

RESET = f"{PROBE}--food_safety_reset"
OFF = f"{PROBE}--food_safety_off"


def device_id(hass: HomeAssistant, identifier: str = PROBE) -> str:
    """Look up a Combustion device's registry ID."""
    (entry,) = hass.config_entries.async_entries(DOMAIN)
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, identifier), entry.entry_id
    )
    assert device is not None
    return device.id


async def configure(hass: HomeAssistant, **data) -> None:
    """Call the configure SafeCook action for the probe."""
    await hass.services.async_call(
        DOMAIN,
        "configure_safecook",
        {ATTR_DEVICE_ID: device_id(hass)} | data,
        blocking=True,
    )


async def press(hass: HomeAssistant, unique_id: str) -> None:
    """Press one of the probe's buttons."""
    await hass.services.async_call(
        "button",
        SERVICE_PRESS,
        {ATTR_ENTITY_ID: entity_id(hass, "button", unique_id)},
        blocking=True,
    )


def sent_direct(client) -> tuple[int, bytes]:
    """Return the message type and payload of the last command written to the probe."""
    frame = client.write_gatt_char.call_args.args[1]
    return frame[4], frame[6 : 6 + frame[5]]


async def test_configure_directly(hass: HomeAssistant, connection):  # noqa: F811
    """The action writes a Simplified program with no parameters; status confirms it."""
    client, _ = connection
    await connect_direct(hass, client)

    await configure(hass, product="simplified_beef_cuts")
    message_type, payload = sent_direct(client)
    assert message_type == CONFIGURE_FOOD_SAFE
    program = FoodSafeData.from_bytes(payload)
    assert program.mode is FoodSafeMode.SIMPLIFIED
    assert program.product is SimplifiedProduct.BEEF_CUTS
    assert program.serving is Serving.SERVED_IMMEDIATELY
    # The firmware applies the product's rules, so nothing else is sent.
    assert payload == bytes([SimplifiedProduct.BEEF_CUTS << 3]) + bytes(9)

    await configure(
        hass, product="simplified_any_poultry", serving="cooked_and_chilled"
    )
    program = FoodSafeData.from_bytes(sent_direct(client)[1])
    assert program.product is SimplifiedProduct.ANY_POULTRY
    assert program.serving is Serving.COOKED_AND_CHILLED

    # The probe reports the program without a threshold, which isn't shown as 0 °C.
    notify(
        client,
        food_safe=food_safe_bytes(
            mode=0,
            product=1,
            serving=1,
            threshold=0,
            z_value=0,
            reference=0,
            d_value=0,
            target_log_reduction=0,
            log_reduction=0,
        ),
    )
    await hass.async_block_till_done()
    product = get_state(hass, "sensor", f"{PROBE}--food_safety_product")
    assert product.state == "simplified_any_poultry"
    threshold = get_state(hass, "sensor", f"{PROBE}--food_safety_threshold")
    assert threshold.state == STATE_UNKNOWN
    client.disconnect.assert_not_awaited()


async def test_buttons_directly(hass: HomeAssistant, connection):  # noqa: F811
    """Reset keeps the program; turning off writes the all-zero program the app writes."""
    client, _ = connection
    await connect_direct(hass, client)

    await press(hass, RESET)
    assert sent_direct(client) == (RESET_FOOD_SAFE, b"")

    await press(hass, OFF)
    assert sent_direct(client) == (CONFIGURE_FOOD_SAFE, bytes(10))
    client.disconnect.assert_not_awaited()


async def test_rejection_keeps_session(hass: HomeAssistant, connection):  # noqa: F811
    """A probe that rejects SafeCook raises a translated error and stays connected."""
    client, _ = connection
    client.command_success = False
    await connect_direct(hass, client)
    with pytest.raises(HomeAssistantError) as err:
        await configure(hass, product="simplified_eggs")
    assert err.value.translation_key == "food_safety_rejected"
    assert err.value.translation_placeholders == {"probe": "10001CCC"}
    with pytest.raises(HomeAssistantError) as err:
        await press(hass, RESET)
    assert err.value.translation_key == "food_safety_rejected"
    client.disconnect.assert_not_awaited()


async def test_through_repeater(hass: HomeAssistant, connections):  # noqa: F811
    """A probe reported by the repeater is configured through it, with its serial number."""
    connections.commands_expected = True
    entry = await enable_predictions(hass)
    relay(hass)
    await tick(hass, entry)
    connections.emit(NODE_ADDRESS, node_status_frame(mode=0))
    await tick(hass, entry)
    await settle(hass)
    node = connections.clients[NODE_ADDRESS]

    await configure(hass, product="simplified_fish_and_shellfish")
    frame = node.write_gatt_char.call_args.args[1]
    assert frame[4] == CONFIGURE_FOOD_SAFE
    assert int.from_bytes(frame[10:14], "little") == PROBE_SERIAL
    program = FoodSafeData.from_bytes(frame[14:24])
    assert program.product is SimplifiedProduct.FISH_AND_SHELLFISH

    await press(hass, RESET)
    frame = node.write_gatt_char.call_args.args[1]
    assert frame[4] == RESET_FOOD_SAFE
    assert int.from_bytes(frame[10:14], "little") == PROBE_SERIAL

    await press(hass, OFF)
    frame = node.write_gatt_char.call_args.args[1]
    assert (frame[4], frame[14:24]) == (CONFIGURE_FOOD_SAFE, bytes(10))

    connections.command_success = False
    with pytest.raises(HomeAssistantError) as err:
        await press(hass, OFF)
    assert err.value.translation_key == "food_safety_rejected"
    node.disconnect.assert_not_awaited()
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_without_connection(hass: HomeAssistant, connection):  # noqa: F811
    """Without a connection reaching the probe, the action fails with a translated error."""
    await enable_predictions(hass)
    inject_bt_advertisement(
        hass, service_info(probe_payload(hop=0), address=NODE_ADDRESS)
    )
    await hass.async_block_till_done()
    with pytest.raises(HomeAssistantError) as err:
        await configure(hass, product="simplified_leftovers")
    assert err.value.translation_key == "not_connected"


async def test_monitors_without_session(hass: HomeAssistant):
    """A monitor asked to send a command before its session runs sends nothing."""
    monitor, _, _ = make_monitor(hass, monotonic)

    async def direct(_session) -> None:
        raise AssertionError("no session to send through")

    with pytest.raises(ConnectionError):
        await monitor.async_command(direct)

    async def relayed(_session, _serial_number) -> None:
        raise AssertionError("no session to send through")

    with pytest.raises(ConnectionError):
        await RepeaterMonitor(hass, MeatNet()).async_command(PROBE_SERIAL, relayed)


async def test_predictions_disabled(hass: HomeAssistant):
    """Without prediction connections there are no SafeCook buttons, and the action explains."""
    await setup_entry(hass)
    inject_bt_advertisement(hass, service_info(probe_payload()))
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    for unique_id in (RESET, OFF):
        assert registry.async_get_entity_id("button", DOMAIN, unique_id) is None
    with pytest.raises(ServiceValidationError) as err:
        await configure(hass, product="simplified_leftovers")
    assert err.value.translation_key == "predictions_disabled"


async def test_invalid_devices(hass: HomeAssistant):
    """Gauges, unknown devices, and probes not heard yet are refused before any write."""
    entry = await setup_entry(hass)
    inject_bt_advertisement(hass, service_info(gauge_payload(), address=GAUGE_ADDRESS))
    await hass.async_block_till_done()

    async def call(device: str) -> ServiceValidationError:
        with pytest.raises(ServiceValidationError) as err:
            await hass.services.async_call(
                DOMAIN,
                "configure_safecook",
                {ATTR_DEVICE_ID: device, "product": "simplified_eggs"},
                blocking=True,
            )
        return err.value

    assert (await call(device_id(hass, GAUGE))).translation_key == "not_a_probe"
    assert (await call("missing")).translation_key == "not_a_probe"

    # A probe left in the registry from an earlier run, not heard since starting, while
    # another probe is.
    inject_bt_advertisement(hass, service_info(probe_payload()))
    await hass.async_block_till_done()
    stale = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "1234abcd")},
        model="Predictive Thermometer",
        serial_number="1234ABCD",
    )
    err = await call(stale.id)
    assert err.translation_key == "probe_not_heard"
    assert err.translation_placeholders == {"probe": "1234ABCD"}


@pytest.mark.parametrize(
    "data",
    [
        {},
        # Simplified Default, served immediately, would read back as SafeCook off.
        {"product": "simplified_default"},
        {"product": "integrated_poultry"},
        {"product": "simplified_eggs", "serving": "reheated"},
    ],
)
async def test_invalid_programs(hass: HomeAssistant, data):
    """Programs the action doesn't offer are refused by its schema."""
    await setup_entry(hass)
    inject_bt_advertisement(hass, service_info(probe_payload()))
    await hass.async_block_till_done()
    with pytest.raises(vol.Invalid):
        await configure(hass, **data)
