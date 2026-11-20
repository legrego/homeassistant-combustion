"""Exercise prediction data through mocked Bluetooth connections and HA entities."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest
from bleak.exc import BleakError
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.combustion.const import CONF_ENABLE_PREDICTIONS
from custom_components.combustion.prediction import PredictionMonitor
from tests.test_init import PROBE, get_state, setup_entry
from tests.utils.bt_utils import (
    NODE_ADDRESS,
    PROBE_ADDRESS,
    inject_bt_advertisement,
    probe_payload,
    service_info,
)


def status_packet(*, state=3, mode=1, prediction_type=1, seconds=120, target=65.0):
    """Encode the documented seven-byte prediction field in a probe status packet."""
    raw = (
        state
        | mode << 4
        | prediction_type << 6
        | round(target * 10) << 8
        | 200 << 18
        | seconds << 28
        | 700 << 45
    )
    return bytearray(bytes(23) + raw.to_bytes(7, "little"))


@pytest.fixture
def connection():
    """Capture status and disconnect callbacks without real Bluetooth I/O."""
    client = Mock()
    client.start_notify = AsyncMock()
    client.disconnect = AsyncMock()
    establish = AsyncMock(return_value=client)
    with (
        patch(
            "custom_components.combustion.prediction.establish_connection", establish
        ),
        patch(
            "custom_components.combustion.prediction.bluetooth.async_ble_device_from_address",
            return_value=service_info(probe_payload()).device,
        ),
    ):
        yield client, establish


def notify(client, **kwargs):
    """Send a raw status notification on the subscribed characteristic."""
    client.start_notify.call_args.args[1](None, status_packet(**kwargs))


async def connect(hass, monitor):
    """Begin the background connection and allow subscriptions to complete."""
    monitor.async_connect(PROBE_ADDRESS)
    await hass.async_block_till_done()


async def test_prediction_entities(hass: HomeAssistant, connection):
    """Connected predictions update sensors, count down, and clear on disconnect."""
    client, establish = connection
    entry = await setup_entry(hass)
    hass.config_entries.async_update_entry(
        entry, options={CONF_ENABLE_PREDICTIONS: True}
    )
    await hass.async_block_till_done()
    inject_bt_advertisement(hass, service_info(probe_payload()))
    await hass.async_block_till_done()
    assert establish.call_count == 1
    assert (
        get_state(hass, "sensor", f"{PROBE}--prediction_remaining").state
        == STATE_UNKNOWN
    )
    with patch("custom_components.combustion.prediction.monotonic", return_value=100):
        notify(client)
    await hass.async_block_till_done()
    assert get_state(hass, "sensor", f"{PROBE}--prediction_target").state == "65.0"
    assert (
        get_state(hass, "sensor", f"{PROBE}--prediction_status").state == "predicting"
    )
    with patch("custom_components.combustion.prediction.monotonic", return_value=104):
        entry.runtime_data.predictions[int(PROBE, 16)].async_tick()
        await hass.async_block_till_done()
        assert (
            get_state(hass, "sensor", f"{PROBE}--prediction_remaining").state == "116"
        )
    # Connected status must not replace advertised temperature readings.
    assert get_state(hass, "sensor", f"{PROBE}--sensor--core").state == "20.0"
    establish.call_args.kwargs["disconnected_callback"](client)
    notify(client)  # A queued notification after disconnect must not resurrect data.
    await hass.async_block_till_done()
    assert (
        get_state(hass, "sensor", f"{PROBE}--prediction_remaining").state
        == STATE_UNKNOWN
    )
    assert await hass.config_entries.async_unload(entry.entry_id)
    client.disconnect.assert_awaited_once()


@pytest.mark.parametrize(
    "mode,state,prediction_type,expected",
    [
        (0, 3, 1, None),
        (1, 2, 1, None),
        (1, 3, 0, None),
        (1, 3, 1, 120),
        (2, 3, 2, 120),
        (1, 4, 1, 0),
    ],
)
async def test_countdown_states(
    hass, connection, mode, state, prediction_type, expected
):
    """Only valid active predictions yield a remaining duration."""
    client, _ = connection
    monitor = PredictionMonitor(hass)
    await connect(hass, monitor)
    notify(client, mode=mode, state=state, prediction_type=prediction_type)
    assert monitor.seconds_remaining == expected
    await monitor.async_stop()


async def test_stale_and_malformed_status(hass, connection):
    """Bad packets do not refresh freshness; stale data becomes unknown."""
    client, _ = connection
    monitor = PredictionMonitor(hass)
    await connect(hass, monitor)
    callback = client.start_notify.call_args.args[1]
    with patch("custom_components.combustion.prediction.monotonic", return_value=100):
        notify(client)
    callback(None, bytearray(b"short"))
    with patch(
        "custom_components.combustion.prediction.ProbeStatus.from_data",
        side_effect=ValueError,
    ):
        callback(None, bytearray(30))
    with patch("custom_components.combustion.prediction.monotonic", return_value=116):
        monitor.async_tick()
    assert monitor.status is None
    assert monitor.seconds_remaining is None
    await monitor.async_stop()
    await monitor.async_stop()


@pytest.mark.parametrize("failure", ["connect", "subscribe"])
async def test_connection_backoff(hass, connection, failure):
    """Failures release partial connections and cannot hammer a probe with retries."""
    client, establish = connection
    if failure == "connect":
        establish.side_effect = BleakError("no slots")
    else:
        client.start_notify.side_effect = BleakError("subscribe failed")
    monitor = PredictionMonitor(hass)
    with patch("custom_components.combustion.prediction.monotonic", return_value=100):
        await connect(hass, monitor)
        await connect(hass, monitor)
    assert establish.call_count == 1
    if failure == "subscribe":
        client.disconnect.assert_awaited_once()
    with patch("custom_components.combustion.prediction.monotonic", return_value=161):
        await connect(hass, monitor)
    assert establish.call_count == 2
    await monitor.async_stop()


async def test_stop_during_connect(hass, connection):
    """Unloading cancels an unfinished connection and prevents new attempts."""
    _, establish = connection

    async def pending(*args, **kwargs):
        await asyncio.Event().wait()

    establish.side_effect = pending
    monitor = PredictionMonitor(hass)
    await connect(hass, monitor)
    await monitor.async_stop()
    await connect(hass, monitor)
    assert establish.call_count == 1


async def test_no_connectable_adapter(hass, connection):
    """Passive-only adapters never initiate connections."""
    _, establish = connection
    monitor = PredictionMonitor(hass)
    with patch(
        "custom_components.combustion.prediction.bluetooth.async_ble_device_from_address",
        return_value=None,
    ):
        await connect(hass, monitor)
    establish.assert_not_called()
    await monitor.async_stop()


async def test_repeaters_are_not_connected(hass, connection):
    """Relayed probes get unknown predictions without connecting to the repeater."""
    _, establish = connection
    entry = await setup_entry(hass)
    hass.config_entries.async_update_entry(
        entry, options={CONF_ENABLE_PREDICTIONS: True}
    )
    await hass.async_block_till_done()
    inject_bt_advertisement(
        hass, service_info(probe_payload(hop=0), address=NODE_ADDRESS)
    )
    await hass.async_block_till_done()
    establish.assert_not_called()
    assert (
        get_state(hass, "sensor", f"{PROBE}--prediction_remaining").state
        == STATE_UNKNOWN
    )
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_options(hass):
    """The options flow stores the connection choice and defaults it to off."""
    entry = await setup_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_ENABLE_PREDICTIONS: True}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.runtime_data.enable_predictions


async def test_disable_predictions_releases_connection(hass, connection):
    """Disabling the option closes connections while passive sensors keep updating."""
    client, establish = connection
    entry = await setup_entry(hass)
    hass.config_entries.async_update_entry(
        entry, options={CONF_ENABLE_PREDICTIONS: True}
    )
    await hass.async_block_till_done()
    inject_bt_advertisement(hass, service_info(probe_payload()))
    await hass.async_block_till_done()
    notify(client)
    hass.config_entries.async_update_entry(
        entry, options={CONF_ENABLE_PREDICTIONS: False}
    )
    await hass.async_block_till_done()
    client.disconnect.assert_awaited_once()
    inject_bt_advertisement(hass, service_info(probe_payload(low_battery=True)))
    await hass.async_block_till_done()
    assert get_state(hass, "sensor", f"{PROBE}--sensor--core").state == "20.0"
    assert establish.call_count == 1


async def test_unload_failure_keeps_prediction_connection(hass, connection):
    """A failed platform unload must leave the running integration intact."""
    from custom_components.combustion import async_unload_entry

    client, _ = connection
    entry = await setup_entry(hass)
    hass.config_entries.async_update_entry(
        entry, options={CONF_ENABLE_PREDICTIONS: True}
    )
    await hass.async_block_till_done()
    inject_bt_advertisement(hass, service_info(probe_payload()))
    await hass.async_block_till_done()
    with patch.object(
        hass.config_entries, "async_unload_platforms", return_value=False
    ):
        assert not await async_unload_entry(hass, entry)
    client.disconnect.assert_not_called()
    assert await hass.config_entries.async_unload(entry.entry_id)
    client.disconnect.assert_awaited_once()


async def test_real_probe_prediction_packet(hass, connection):
    """A captured 94-byte firmware status drives HA sensors and expires correctly."""
    client, _ = connection
    entry = await setup_entry(hass)
    hass.config_entries.async_update_entry(
        entry, options={CONF_ENABLE_PREDICTIONS: True}
    )
    await hass.async_block_till_done()
    inject_bt_advertisement(hass, service_info(probe_payload()))
    await hass.async_block_till_done()
    # Real status captured during an active removal prediction; includes newer firmware fields.
    packet = bytes.fromhex(
        "00000000ba000000f223819c903042584ccdfda94820e0537925641ae03e1100e8840ec012541404000000000000000000010000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    )
    monitor = entry.runtime_data.predictions[int(PROBE, 16)]
    with patch("custom_components.combustion.prediction.monotonic", return_value=100):
        client.start_notify.call_args.args[1](None, bytearray(packet))
        await hass.async_block_till_done()
        assert get_state(hass, "sensor", f"{PROBE}--prediction_target").state == "37.7"
        assert (
            get_state(hass, "sensor", f"{PROBE}--prediction_status").state
            == "predicting"
        )
        assert (
            get_state(hass, "sensor", f"{PROBE}--prediction_remaining").state == "422"
        )
    with patch("custom_components.combustion.prediction.monotonic", return_value=104):
        monitor.async_tick()
        await hass.async_block_till_done()
        assert (
            get_state(hass, "sensor", f"{PROBE}--prediction_remaining").state == "418"
        )
    with patch("custom_components.combustion.prediction.monotonic", return_value=116):
        monitor.async_tick()
        await hass.async_block_till_done()
        assert (
            get_state(hass, "sensor", f"{PROBE}--prediction_remaining").state
            == STATE_UNKNOWN
        )
    assert await hass.config_entries.async_unload(entry.entry_id)
