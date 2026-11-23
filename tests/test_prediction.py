"""Exercise prediction data through mocked Bluetooth connections and HA entities."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest
from bleak.exc import BleakError
from combustion_ble import MeatNet
from combustion_ble.const import UART_RX_CHARACTERISTIC, UART_TX_CHARACTERISTIC
from combustion_ble.utilities.crc16ccitt import crc16ccitt
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.combustion.const import CONF_ENABLE_PREDICTIONS, DOMAIN
from custom_components.combustion.prediction import PredictionMonitor
from custom_components.combustion.sensor import CONNECTED_SENSORS
from tests.test_init import PROBE, get_state, setup_entry
from tests.utils.bt_utils import (
    NODE_ADDRESS,
    PROBE_ADDRESS,
    PROBE_FIRMWARE,
    PROBE_HARDWARE,
    device_information,
    inject_bt_advertisement,
    probe_payload,
    service_info,
)


def status_packet(
    *,
    state=3,
    mode=1,
    prediction_type=1,
    seconds=120,
    target=65.0,
    food_safe: bytes | None = None,
):
    """Encode the documented seven-byte prediction field in a probe status packet.

    Pass ``food_safe`` (see :func:`food_safe_bytes`) for firmware that reports SafeCook.
    """
    raw = (
        state
        | mode << 4
        | prediction_type << 6
        | round(target * 10) << 8
        | 200 << 18
        | seconds << 28
        | 700 << 45
    )
    return bytearray(bytes(23) + raw.to_bytes(7, "little") + (food_safe or b""))


def food_safe_bytes(
    *,
    mode=1,
    product=2,
    serving=0,
    threshold=54.4,
    z_value=7.0,
    reference=70.0,
    d_value=0.2,
    target_log_reduction=6.5,
    state=0,
    log_reduction=2.3,
    seconds_above_threshold=300,
) -> bytes:
    """Encode the 18-byte food safe data and status that follow prediction status.

    Defaults are an Integrated program for ground meats, part of the way to safe.
    """

    def temperature(value: float) -> int:
        return round(value / 0.05) & 0x1FFF

    data = (
        mode
        | product << 3
        | serving << 13
        | temperature(threshold) << 16
        | temperature(z_value) << 29
        | temperature(reference) << 42
        | temperature(d_value) << 55
        | round(target_log_reduction * 10) << 68
    )
    status = state | round(log_reduction * 10) << 3 | seconds_above_threshold << 11
    return data.to_bytes(10, "little") + status.to_bytes(8, "little")


def probe_response_frame(message_type: int = 0x05, *, success: bool = True) -> bytes:
    """Build a probe's UART response with no payload, Set Prediction (0x05) by default."""
    body = bytes([message_type, int(success), 0])
    return b"\xca\xfe" + crc16ccitt(body).to_bytes(2, "little") + body


@pytest.fixture
def connection():
    """Capture status and disconnect callbacks without real Bluetooth I/O.

    The probe acknowledges every command with ``client.command_success``. Tests that send
    commands set ``client.commands_expected``; any other write fails the test.
    """
    client = Mock()
    client.start_notify = AsyncMock()
    client.is_connected = True
    client.stop_notify = AsyncMock()
    client.commands_expected = False
    client.command_success = True

    async def write_gatt_char(characteristic, frame, response):
        assert characteristic == UART_RX_CHARACTERISTIC
        uart = next(
            call.args[1]
            for call in client.start_notify.call_args_list
            if call.args[0] == UART_TX_CHARACTERISTIC
        )
        asyncio.get_running_loop().call_soon(
            uart,
            None,
            bytearray(probe_response_frame(frame[4], success=client.command_success)),
        )

    client.write_gatt_char = AsyncMock(side_effect=write_gatt_char)
    client.read_gatt_char = AsyncMock(
        side_effect=device_information(PROBE_FIRMWARE, PROBE_HARDWARE)
    )

    async def disconnect():
        client.is_connected = False
        establish.call_args.kwargs["disconnected_callback"](client)

    async def establish_client(*args, **kwargs):
        client.is_connected = True
        return client

    client.disconnect = AsyncMock(side_effect=disconnect)
    establish = AsyncMock(side_effect=establish_client)
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
        if not client.commands_expected:
            client.write_gatt_char.assert_not_called()


def notify(client, **kwargs):
    """Send a raw status notification on the subscribed characteristic."""
    client.start_notify.call_args.args[1](None, status_packet(**kwargs))


def make_monitor(hass, clock):
    """Discover a real library probe and attach an HA connection monitor."""
    meatnet = MeatNet(clock=clock)
    meatnet.handle_advertisement(PROBE_ADDRESS, probe_payload())
    probe = meatnet.probes[int(PROBE, 16)]
    return PredictionMonitor(hass, meatnet, probe), meatnet, probe


async def connect(hass, monitor):
    """Begin the background connection and allow subscriptions to complete."""
    monitor.async_connect(PROBE_ADDRESS)
    await hass.async_block_till_done()


async def test_prediction_entities(hass: HomeAssistant, connection, clock):
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
    notify(client)
    await hass.async_block_till_done()
    assert get_state(hass, "number", f"{PROBE}--prediction_target").state == "65.0"
    assert (
        get_state(hass, "sensor", f"{PROBE}--prediction_status").state == "predicting"
    )
    clock.advance(4)
    entry.runtime_data.meatnet.expire()
    await hass.async_block_till_done()
    assert get_state(hass, "sensor", f"{PROBE}--prediction_remaining").state == "116"
    # Connected status must not replace advertised temperature readings.
    assert get_state(hass, "sensor", f"{PROBE}--sensor--core").state == "20.0"
    client.is_connected = False
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
    hass, connection, clock, mode, state, prediction_type, expected
):
    """Only valid active predictions yield a remaining duration."""
    client, _ = connection
    monitor, meatnet, probe = make_monitor(hass, clock)
    await connect(hass, monitor)
    notify(client, mode=mode, state=state, prediction_type=prediction_type)
    assert probe.prediction.seconds_remaining == expected
    await monitor.async_stop()


async def test_stale_and_malformed_status(hass, connection, clock):
    """Bad packets do not refresh freshness; stale data becomes unknown."""
    client, _ = connection
    monitor, meatnet, probe = make_monitor(hass, clock)
    await connect(hass, monitor)
    callback = client.start_notify.call_args.args[1]
    notify(client)
    callback(None, bytearray(b"short"))
    with patch(
        "combustion_ble.probe_session.ProbeStatus.from_data",
        side_effect=ValueError,
    ):
        callback(None, bytearray(30))
    clock.advance(16)
    meatnet.expire()
    assert probe.prediction is None
    await monitor.async_stop()
    await monitor.async_stop()


@pytest.mark.parametrize("failure", ["connect", "subscribe"])
async def test_connection_backoff(hass, connection, clock, failure):
    """Failures release partial connections and cannot hammer a probe with retries."""
    client, establish = connection
    if failure == "connect":
        establish.side_effect = BleakError("no slots")
    else:
        client.start_notify.side_effect = BleakError("subscribe failed")
    monitor, meatnet, probe = make_monitor(hass, clock)
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


async def test_stop_during_connect(hass, connection, clock):
    """Unloading cancels an unfinished connection and prevents new attempts."""
    _, establish = connection

    async def pending(*args, **kwargs):
        await asyncio.Event().wait()

    establish.side_effect = pending
    monitor, meatnet, probe = make_monitor(hass, clock)
    await connect(hass, monitor)
    await monitor.async_stop()
    await connect(hass, monitor)
    assert establish.call_count == 1


async def test_no_connectable_adapter(hass, connection, clock):
    """Passive-only adapters never initiate connections."""
    _, establish = connection
    monitor, meatnet, probe = make_monitor(hass, clock)
    with patch(
        "custom_components.combustion.prediction.bluetooth.async_ble_device_from_address",
        return_value=None,
    ):
        await connect(hass, monitor)
    establish.assert_not_called()
    await monitor.async_stop()


async def test_options(hass):
    """The options flow stores the connection choice and defaults it to off."""
    entry = await setup_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "predictions"}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_ENABLE_PREDICTIONS: True}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.runtime_data.enable_predictions


async def test_other_entry_updates_keep_connections(hass, connection):
    """Discovery of a new address updates the entry, which must not drop connections."""
    client, establish = connection
    entry = await setup_entry(hass)
    hass.config_entries.async_update_entry(
        entry, options={CONF_ENABLE_PREDICTIONS: True}
    )
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    inject_bt_advertisement(hass, service_info(probe_payload()))
    await hass.async_block_till_done()
    hass.config_entries.async_update_entry(entry, title="Renamed")
    # Bluetooth discovery of another Combustion address updates the entry's discovery keys.
    inject_bt_advertisement(
        hass, service_info(probe_payload(hop=0), address=NODE_ADDRESS)
    )
    await hass.async_block_till_done()
    assert entry.runtime_data is coordinator
    client.disconnect.assert_not_called()
    assert establish.call_count == 1
    assert await hass.config_entries.async_unload(entry.entry_id)


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
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("number", DOMAIN, f"{PROBE}--prediction_target")
    assert registry.async_get_entity_id("button", DOMAIN, f"{PROBE}--prediction_cancel")
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{PROBE}--food_safety")
    hass.config_entries.async_update_entry(
        entry, options={CONF_ENABLE_PREDICTIONS: False}
    )
    await hass.async_block_till_done()
    client.disconnect.assert_awaited_once()
    # Prediction and SafeCook entities are removed rather than left behind as orphaned entities.
    for description in CONNECTED_SENSORS:
        assert not registry.async_get_entity_id(
            "sensor", DOMAIN, f"{PROBE}--{description.key}"
        )
    assert not registry.async_get_entity_id(
        "number", DOMAIN, f"{PROBE}--prediction_target"
    )
    assert not registry.async_get_entity_id(
        "button", DOMAIN, f"{PROBE}--prediction_cancel"
    )
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{PROBE}--sensor--core")
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


async def test_real_probe_prediction_packet(hass, connection, clock):
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
    client.start_notify.call_args.args[1](None, bytearray(packet))
    await hass.async_block_till_done()
    assert get_state(hass, "number", f"{PROBE}--prediction_target").state == "37.7"
    assert (
        get_state(hass, "sensor", f"{PROBE}--prediction_status").state == "predicting"
    )
    assert get_state(hass, "sensor", f"{PROBE}--prediction_remaining").state == "422"
    clock.advance(4)
    entry.runtime_data.meatnet.expire()
    await hass.async_block_till_done()
    assert get_state(hass, "sensor", f"{PROBE}--prediction_remaining").state == "418"
    clock.advance(12)
    # Advertisements remain fresh while connected status has stopped.
    entry.runtime_data.meatnet.handle_advertisement(PROBE_ADDRESS, probe_payload())
    entry.runtime_data.meatnet.expire()
    await hass.async_block_till_done()
    assert (
        get_state(hass, "sensor", f"{PROBE}--prediction_remaining").state
        == STATE_UNKNOWN
    )
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_reconnect_creates_fresh_session(hass, connection, clock):
    """A dropped connection clears library state and reconnects with fresh subscriptions."""
    client, establish = connection
    monitor, meatnet, probe = make_monitor(hass, clock)
    with patch("custom_components.combustion.prediction.monotonic", return_value=100):
        await connect(hass, monitor)
        notify(client)
        late_callback = client.start_notify.call_args.args[1]
        client.is_connected = False
        establish.call_args.kwargs["disconnected_callback"](client)
        await hass.async_block_till_done()
        late_callback(None, status_packet())
        assert probe.prediction is None
        await connect(hass, monitor)
        assert establish.call_count == 1
    # A working session that drops reconnects after a short delay, not the failure backoff.
    with patch("custom_components.combustion.prediction.monotonic", return_value=106):
        await connect(hass, monitor)
    assert establish.call_count == 2
    assert (
        client.start_notify.await_count == 4
    )  # Status and command notifications per session.
    notify(client, seconds=90)
    assert probe.prediction.seconds_remaining == 90
    await monitor.async_stop()
    assert probe.prediction is None
    assert not client.is_connected


async def test_unsubscribe_failure_releases_session_after_disconnect(
    hass, connection, clock
):
    """Failed subscription cleanup must not prevent a subsequent session on the probe."""
    client, establish = connection
    monitor, meatnet, probe = make_monitor(hass, clock)
    await connect(hass, monitor)
    notify(client)
    client.stop_notify.side_effect = BleakError("unsubscribe failed")
    await monitor.async_stop()
    assert probe.prediction is None
    assert not client.is_connected
    client.stop_notify.side_effect = None
    replacement = PredictionMonitor(hass, meatnet, probe)
    await connect(hass, replacement)
    assert establish.call_count == 2
    notify(client, seconds=30)
    assert probe.prediction.seconds_remaining == 30
    await replacement.async_stop()


async def test_disconnect_before_session_start(hass, connection, clock):
    """A connection lost before subscriptions must leave no usable prediction or session."""
    client, establish = connection
    monitor, meatnet, probe = make_monitor(hass, clock)

    async def lost_during_connect(*args, **kwargs):
        client.is_connected = False
        kwargs["disconnected_callback"](client)
        return client

    establish.side_effect = lost_during_connect
    await connect(hass, monitor)
    client.start_notify.assert_not_called()
    assert probe.prediction is None
    await monitor.async_stop()


async def test_disconnect_error_after_connection_closed(hass, connection, clock):
    """A disconnect error must not prevent releasing library session ownership."""
    client, _ = connection
    monitor, meatnet, probe = make_monitor(hass, clock)
    await connect(hass, monitor)

    async def failed_disconnect():
        client.is_connected = False
        raise BleakError("disconnect failed after link closed")

    client.disconnect.side_effect = failed_disconnect
    await monitor.async_stop()
    assert probe.prediction is None
    assert not client.is_connected


def stalled_unsubscribe(client):
    """Make unsubscribing wait until released. Return the started and release events."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def stop_notify(_characteristic):
        started.set()
        await release.wait()

    client.stop_notify.side_effect = stop_notify
    return started, release


async def test_probe_heard_while_unloading_is_not_connected(hass, connection):
    """Advertisements arriving while connections are released must not start new ones."""
    client, establish = connection
    entry = await setup_entry(hass)
    hass.config_entries.async_update_entry(
        entry, options={CONF_ENABLE_PREDICTIONS: True}
    )
    await hass.async_block_till_done()
    inject_bt_advertisement(hass, service_info(probe_payload()))
    await hass.async_block_till_done()
    assert establish.call_count == 1
    coordinator = entry.runtime_data
    unsubscribing, release = stalled_unsubscribe(client)
    unload = hass.async_create_task(hass.config_entries.async_unload(entry.entry_id))
    await unsubscribing.wait()
    # Still listening: the entry stops listening only after unloading finishes.
    other = 0x10002CCC
    inject_bt_advertisement(
        hass,
        service_info(probe_payload(serial_number=other), address="CC:CC:CC:CC:CC:04"),
    )
    release.set()
    assert await unload
    assert establish.call_count == 1
    assert other not in coordinator.predictions
    assert not client.is_connected


async def test_unreleased_session_is_cleaned_up_before_reconnecting(
    hass, connection, clock
):
    """A link that could not be closed is retried, never stacked under a new connection."""
    client, establish = connection
    monitor, meatnet, probe = make_monitor(hass, clock)
    disconnect = client.disconnect.side_effect
    client.start_notify.side_effect = [None, BleakError("subscribe failed")]
    client.stop_notify.side_effect = BleakError("unsubscribe failed")
    client.disconnect.side_effect = BleakError("disconnect failed")
    with patch("custom_components.combustion.prediction.monotonic", return_value=100):
        await connect(hass, monitor)
    assert establish.call_count == 1
    assert client.is_connected
    # Cleanup still fails, so no second connection is opened while the first holds a slot.
    with patch("custom_components.combustion.prediction.monotonic", return_value=161):
        await connect(hass, monitor)
    assert establish.call_count == 1
    client.start_notify.side_effect = None
    client.stop_notify.side_effect = None
    client.disconnect.side_effect = disconnect
    with patch("custom_components.combustion.prediction.monotonic", return_value=222):
        await connect(hass, monitor)
    assert establish.call_count == 2
    notify(client, seconds=30)
    assert probe.prediction.seconds_remaining == 30
    await monitor.async_stop()
    assert not client.is_connected


async def test_cancel_during_unsubscribe_still_disconnects(hass, connection, clock):
    """Cancelling cleanup, for example at shutdown, still closes the link and releases the probe."""
    client, establish = connection
    monitor, meatnet, probe = make_monitor(hass, clock)
    await connect(hass, monitor)
    unsubscribing, _ = stalled_unsubscribe(client)
    task = monitor._task
    task.cancel()
    await unsubscribing.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    client.disconnect.assert_awaited_once()
    assert not client.is_connected
    client.stop_notify.side_effect = None
    replacement = PredictionMonitor(hass, meatnet, probe)
    await connect(hass, replacement)
    assert establish.call_count == 2
    notify(client, seconds=30)
    assert probe.prediction.seconds_remaining == 30
    await replacement.async_stop()


async def test_stop_propagates_its_own_cancellation(hass, connection, clock):
    """Stopping a monitor must not swallow a cancellation aimed at the caller."""
    client, _ = connection
    monitor, meatnet, probe = make_monitor(hass, clock)
    await connect(hass, monitor)
    unsubscribing, release = stalled_unsubscribe(client)
    stopping = hass.async_create_task(monitor.async_stop())
    await unsubscribing.wait()
    stopping.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopping
    release.set()
    await hass.async_block_till_done()
    assert not client.is_connected


async def test_failed_attempt_disconnect_does_not_end_retried_session(
    hass, connection, clock
):
    """A disconnect reported by a failed attempt must not close a successful retry."""
    client, establish = connection
    monitor, meatnet, probe = make_monitor(hass, clock)

    async def retried_connect(*args, **kwargs):
        kwargs["disconnected_callback"](client)  # The first attempt drops.
        client.is_connected = True  # The retry succeeds.
        return client

    establish.side_effect = retried_connect
    await connect(hass, monitor)
    assert monitor._task is not None
    client.disconnect.assert_not_called()
    notify(client, seconds=30)
    assert probe.prediction.seconds_remaining == 30
    await monitor.async_stop()
    assert not client.is_connected
