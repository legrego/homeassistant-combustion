"""Predictions through a MeatNet repeater connection, and how it replaces direct connections."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from combustion_ble.const import UART_RX_CHARACTERISTIC, UART_TX_CHARACTERISTIC
from combustion_ble.utilities.crc16ccitt import crc16ccitt
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.combustion.const import CONF_ENABLE_PREDICTIONS
from tests.test_init import PROBE, get_state, setup_entry
from tests.test_prediction import status_packet
from tests.utils.bt_utils import (
    NODE_ADDRESS,
    NODE_FIRMWARE,
    NODE_HARDWARE,
    PROBE_ADDRESS,
    PROBE_FIRMWARE,
    PROBE_HARDWARE,
    PROBE_SERIAL,
    device_information,
    inject_bt_advertisement,
    probe_payload,
    service_info,
)

OTHER_NODE_ADDRESS = "CC:CC:CC:CC:CC:05"
THIRD_NODE_ADDRESS = "CC:CC:CC:CC:CC:06"


def node_status_frame(
    *, serial_number=PROBE_SERIAL, hop=0, food_safe: bytes | None = None, **status
) -> bytes:
    """Build a repeater's Probe Status (0x45) UART message, per the MeatNet node spec."""
    payload = (
        serial_number.to_bytes(4, "little")
        + bytes(status_packet(**status))
        + (food_safe or bytes(18))  # Food safe data and status
        + bytes([hop << 6])  # Network information
        + bytes(1 + 1 + 22 + 22 + 4 + 2)  # Overheating, preferences, alarms, session
    )
    body = b"\x45" + (1).to_bytes(4, "little") + bytes([len(payload)]) + payload
    return b"\xca\xfe" + crc16ccitt(body).to_bytes(2, "little") + body


PROBE_FIRMWARE_REVISION = 0x46
PROBE_HARDWARE_REVISION = 0x47
SET_PREDICTION = 0x05
CONFIGURE_FOOD_SAFE = 0x07
RESET_FOOD_SAFE = 0x08
COMMANDS = (SET_PREDICTION, CONFIGURE_FOOD_SAFE, RESET_FOOD_SAFE)


def node_response_frame(
    message_type: int, request_id: bytes, payload: bytes, *, success: bool = True
) -> bytes:
    """Build a repeater's UART response, per the MeatNet node spec."""
    body = (
        bytes([message_type | 0x80])
        + request_id
        + bytes(4)  # Response ID
        + bytes([int(success), len(payload)])
        + payload
    )
    return b"\xca\xfe" + crc16ccitt(body).to_bytes(2, "little") + body


class Connections:
    """Fake Bluetooth clients, one per connected address, with no real I/O.

    Repeaters answer requests for a probe's revisions, unless its serial number is in
    ``unreachable_probes``.
    """

    def __init__(self) -> None:
        """Initialize."""
        self.clients: dict[str, Mock] = {}
        self.attempts: list[str] = []
        self.failing: set[str] = set()
        self.unreachable: set[str] = set()
        self.unreachable_probes: set[int] = set()
        self.failing_disconnects = 0
        """How many disconnects fail, leaving the client connected."""
        self.stalled_reads: set[str] = set()
        """Addresses whose characteristic reads never complete."""
        self.disconnect_gate: asyncio.Event | None = None
        """When set, disconnects wait for this event, as a slow disconnect would."""
        self.commands_expected = False
        """Whether the test sends commands. Otherwise any command fails the test."""
        self.command_success = True
        self.requests: list[tuple[str, int, int]] = []
        """Node requests written: address, message type, and probe serial number."""

    async def establish(
        self, _client_class, device, _name, *, disconnected_callback, **_
    ):
        """Stand in for bleak-retry-connector's establish_connection."""
        self.attempts.append(device.address)
        if device.address in self.failing:
            raise BleakError("no free connection slots")
        client = Mock(is_connected=True)
        client.callbacks = {}

        async def start_notify(characteristic, callback):
            client.callbacks[characteristic] = callback

        async def disconnect():
            if self.disconnect_gate is not None:
                await self.disconnect_gate.wait()
            if self.failing_disconnects:
                self.failing_disconnects -= 1
                raise BleakError("disconnect failed")
            if client.is_connected:
                client.is_connected = False
                disconnected_callback(client)

        client.start_notify = AsyncMock(side_effect=start_notify)
        client.stop_notify = AsyncMock()

        async def write_gatt_char(characteristic, frame, response):
            assert characteristic == UART_RX_CHARACTERISTIC
            message_type, request_id = frame[4], frame[5:9]
            serial = frame[10:14]
            self.requests.append(
                (device.address, message_type, int.from_bytes(serial, "little"))
            )
            if message_type in COMMANDS:
                response = node_response_frame(
                    message_type, request_id, b"", success=self.command_success
                )
            else:
                revision = {
                    PROBE_FIRMWARE_REVISION: PROBE_FIRMWARE,
                    PROBE_HARDWARE_REVISION: PROBE_HARDWARE,
                }.get(message_type)
                if revision is None:
                    return
                success = (
                    int.from_bytes(serial, "little") not in self.unreachable_probes
                )
                response = node_response_frame(
                    message_type,
                    request_id,
                    serial + revision.encode().ljust(16, b"\x00") if success else b"",
                    success=success,
                )
            asyncio.get_running_loop().call_soon(self.emit, device.address, response)

        client.write_gatt_char = AsyncMock(side_effect=write_gatt_char)
        client.read_gatt_char = AsyncMock(
            side_effect=stall
            if device.address in self.stalled_reads
            else device_information(NODE_FIRMWARE, NODE_HARDWARE)
        )
        client.disconnect = AsyncMock(side_effect=disconnect)
        client.drop = lambda: (
            setattr(client, "is_connected", False),
            disconnected_callback(client),
        )
        self.clients[device.address] = client
        return client

    def emit(self, address: str, data: bytes) -> None:
        """Send a UART notification from the client connected to ``address``."""
        self.clients[address].callbacks[UART_TX_CHARACTERISTIC](None, bytearray(data))


async def stall(*_args) -> bytes:
    """Stand in for a characteristic read that never completes."""
    await asyncio.Event().wait()
    raise AssertionError("unreachable")


@pytest.fixture
def connections():
    """Every address is reachable through a connection-capable adapter."""
    fake = Connections()
    with (
        patch(
            "custom_components.combustion.prediction.establish_connection",
            side_effect=fake.establish,
        ),
        patch(
            "custom_components.combustion.prediction.bluetooth.async_ble_device_from_address",
            side_effect=lambda _hass, address, connectable: (
                None
                if address in fake.unreachable
                else BLEDevice(address=address, name=None, details=None)
            ),
        ),
    ):
        yield fake
    if not fake.commands_expected:
        # Only revisions are requested; commands are never sent unless asked.
        assert all(request[1] not in COMMANDS for request in fake.requests)


async def enable_predictions(hass: HomeAssistant):
    """Set up the integration with predictions enabled."""
    entry = await setup_entry(hass)
    hass.config_entries.async_update_entry(
        entry, options={CONF_ENABLE_PREDICTIONS: True}
    )
    await hass.async_block_till_done()
    return entry


async def tick(hass: HomeAssistant, entry) -> None:
    """Run the integration's once-per-second tick."""
    entry.runtime_data._async_tick(dt_util.utcnow())
    await hass.async_block_till_done()


def relay(hass: HomeAssistant, address: str = NODE_ADDRESS, rssi: int = -61) -> None:
    """Deliver a repeater's relayed advertisement for the probe."""
    inject_bt_advertisement(
        hass, service_info(probe_payload(hop=0), address=address, rssi=rssi)
    )


def remaining(hass: HomeAssistant) -> str:
    """State of the probe's cooking time remaining sensor."""
    return get_state(hass, "sensor", f"{PROBE}--prediction_remaining").state


async def test_predictions_through_repeater(hass: HomeAssistant, connections):
    """A probe heard only through a repeater gets predictions over the repeater's connection."""
    entry = await enable_predictions(hass)
    relay(hass)
    await hass.async_block_till_done()
    assert not connections.attempts
    await tick(hass, entry)
    assert connections.attempts == [NODE_ADDRESS]
    node = connections.clients[NODE_ADDRESS]
    assert list(node.callbacks) == [UART_TX_CHARACTERISTIC]
    assert remaining(hass) == STATE_UNKNOWN

    connections.emit(NODE_ADDRESS, node_status_frame(seconds=120))
    await hass.async_block_till_done()
    assert get_state(hass, "number", f"{PROBE}--prediction_target").state == "65.0"
    assert (
        get_state(hass, "sensor", f"{PROBE}--prediction_status").state == "predicting"
    )
    assert remaining(hass) == "120"
    assert not entry.runtime_data.predictions  # No direct connection was needed.

    # Ticks while connected don't open more connections.
    await tick(hass, entry)
    assert connections.attempts == [NODE_ADDRESS]

    node.drop()
    await hass.async_block_till_done()
    assert remaining(hass) == STATE_UNKNOWN
    assert await hass.config_entries.async_unload(entry.entry_id)
    node.disconnect.assert_awaited_once()


async def test_repeater_replaces_direct_connection(hass: HomeAssistant, connections):
    """The repeater replaces a direct connection, which returns if the repeater is lost."""
    entry = await enable_predictions(hass)
    coordinator = entry.runtime_data
    inject_bt_advertisement(hass, service_info(probe_payload()))
    await hass.async_block_till_done()
    assert connections.attempts == [PROBE_ADDRESS]
    direct = connections.clients[PROBE_ADDRESS]

    relay(hass)
    await tick(hass, entry)
    assert connections.attempts == [PROBE_ADDRESS, NODE_ADDRESS]
    # Connected but not yet reporting the probe: the direct connection stays.
    assert direct.is_connected

    connections.emit(NODE_ADDRESS, node_status_frame(seconds=120))
    await tick(hass, entry)
    assert not direct.is_connected
    assert not coordinator.predictions
    # The repeater's prediction carries on without a gap.
    assert remaining(hass) == "120"

    # Direct advertisements don't reconnect while the repeater reports the probe.
    inject_bt_advertisement(hass, service_info(probe_payload(low_battery=True)))
    await hass.async_block_till_done()
    assert connections.attempts == [PROBE_ADDRESS, NODE_ADDRESS]

    connections.clients[NODE_ADDRESS].drop()
    await hass.async_block_till_done()
    assert remaining(hass) == STATE_UNKNOWN
    inject_bt_advertisement(hass, service_info(probe_payload()))
    await hass.async_block_till_done()
    assert connections.attempts == [PROBE_ADDRESS, NODE_ADDRESS, PROBE_ADDRESS]
    assert connections.clients[PROBE_ADDRESS].is_connected

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not connections.clients[PROBE_ADDRESS].is_connected


async def test_failed_repeater_tries_another(hass: HomeAssistant, connections):
    """A repeater that can't be connected backs off, then another repeater is tried first."""
    entry = await enable_predictions(hass)
    relay(hass, NODE_ADDRESS, rssi=-50)
    relay(hass, OTHER_NODE_ADDRESS, rssi=-80)
    connections.failing.add(NODE_ADDRESS)
    with patch("custom_components.combustion.prediction.monotonic", return_value=100):
        await tick(hass, entry)
        await tick(hass, entry)
    assert connections.attempts == [NODE_ADDRESS]
    with patch("custom_components.combustion.prediction.monotonic", return_value=161):
        await tick(hass, entry)
    assert connections.attempts == [NODE_ADDRESS, OTHER_NODE_ADDRESS]
    connections.emit(OTHER_NODE_ADDRESS, node_status_frame(hop=1, seconds=60))
    await hass.async_block_till_done()
    assert remaining(hass) == "60"
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("all_fail", [False, True])
async def test_repeater_failures_do_not_starve_candidates(
    hass: HomeAssistant, connections, all_fail
):
    """Try every reachable repeater, and retry failed candidates in a fresh round."""
    entry = await enable_predictions(hass)
    relay(hass, NODE_ADDRESS, rssi=-40)
    relay(hass, OTHER_NODE_ADDRESS, rssi=-50)
    relay(hass, THIRD_NODE_ADDRESS, rssi=-60)
    connections.failing.update({NODE_ADDRESS, OTHER_NODE_ADDRESS})
    if all_fail:
        connections.failing.add(THIRD_NODE_ADDRESS)
    for now in (100, 161, 222):
        with patch(
            "custom_components.combustion.prediction.monotonic", return_value=now
        ):
            await tick(hass, entry)
    assert connections.attempts == [
        NODE_ADDRESS,
        OTHER_NODE_ADDRESS,
        THIRD_NODE_ADDRESS,
    ]
    working = THIRD_NODE_ADDRESS
    if all_fail:
        # The second repeater recovers. A fresh round must get past the failing first one.
        connections.failing.remove(OTHER_NODE_ADDRESS)
        for now in (283, 344):
            with patch(
                "custom_components.combustion.prediction.monotonic", return_value=now
            ):
                await tick(hass, entry)
        assert connections.attempts[-2:] == [NODE_ADDRESS, OTHER_NODE_ADDRESS]
        working = OTHER_NODE_ADDRESS
    connections.emit(working, node_status_frame(seconds=60))
    await hass.async_block_till_done()
    assert remaining(hass) == "60"
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("retry_on_unload", [False, True])
async def test_failed_direct_release_is_retained(
    hass: HomeAssistant, connections, retry_on_unload
):
    """Failed retirement is retried with backoff, blocks replacements, and survives to unload."""
    entry = await enable_predictions(hass)
    coordinator = entry.runtime_data
    inject_bt_advertisement(hass, service_info(probe_payload()))
    relay(hass)
    await tick(hass, entry)
    direct = connections.clients[PROBE_ADDRESS]
    disconnect = direct.disconnect.side_effect
    direct.stop_notify.side_effect = BleakError("unsubscribe failed")
    direct.disconnect.side_effect = BleakError("disconnect failed")
    with patch("custom_components.combustion.prediction.monotonic", return_value=100):
        connections.emit(NODE_ADDRESS, node_status_frame())
        await tick(hass, entry)
        assert direct.is_connected
        # Repeated ticks must not retry cleanup before the backoff elapses.
        attempts = direct.disconnect.await_count
        await tick(hass, entry)
        assert direct.disconnect.await_count == attempts
    with patch("custom_components.combustion.prediction.monotonic", return_value=161):
        await tick(hass, entry)
        assert direct.disconnect.await_count == attempts + 1
        # Losing the repeater must not start a replacement over an unreleased session.
        connections.clients[NODE_ADDRESS].drop()
        await hass.async_block_till_done()
        inject_bt_advertisement(hass, service_info(probe_payload(low_battery=True)))
        await hass.async_block_till_done()
        assert connections.attempts == [PROBE_ADDRESS, NODE_ADDRESS]
    direct.stop_notify.side_effect = None
    direct.disconnect.side_effect = disconnect
    if not retry_on_unload:
        with patch(
            "custom_components.combustion.prediction.monotonic", return_value=222
        ):
            await tick(hass, entry)
        assert not direct.is_connected
        inject_bt_advertisement(hass, service_info(probe_payload()))
        await hass.async_block_till_done()
        assert connections.attempts.count(PROBE_ADDRESS) == 2
        assert coordinator.meatnet.probes[PROBE_SERIAL]._prediction_session is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not direct.is_connected
    assert not coordinator._retiring


async def test_no_repeater_without_predictions(hass: HomeAssistant, connections):
    """With predictions disabled, repeaters are only connected briefly, to read revisions."""
    entry = await setup_entry(hass)
    relay(hass)
    await tick(hass, entry)
    assert entry.runtime_data.repeater is None
    for _ in range(50):
        await asyncio.sleep(0)
    assert connections.attempts == [NODE_ADDRESS]
    connections.clients[NODE_ADDRESS].disconnect.assert_awaited_once()
    await tick(hass, entry)
    assert connections.attempts == [NODE_ADDRESS]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_unreachable_repeater_is_skipped(hass: HomeAssistant, connections):
    """A repeater no connection-capable adapter can reach is passed over for the next one."""
    entry = await enable_predictions(hass)
    relay(hass, NODE_ADDRESS, rssi=-50)
    relay(hass, OTHER_NODE_ADDRESS, rssi=-80)
    connections.unreachable.update({NODE_ADDRESS, OTHER_NODE_ADDRESS})
    await tick(hass, entry)
    assert not connections.attempts
    connections.unreachable.discard(OTHER_NODE_ADDRESS)
    await tick(hass, entry)
    assert connections.attempts == [OTHER_NODE_ADDRESS]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_unload_waits_for_direct_release(hass: HomeAssistant, connections):
    """Unloading while a redundant direct connection is being released waits for it."""
    entry = await enable_predictions(hass)
    inject_bt_advertisement(hass, service_info(probe_payload()))
    relay(hass)
    await tick(hass, entry)
    direct = connections.clients[PROBE_ADDRESS]
    unsubscribing = asyncio.Event()
    release = asyncio.Event()

    async def stalled_stop_notify(_characteristic):
        unsubscribing.set()
        await release.wait()

    direct.stop_notify.side_effect = stalled_stop_notify
    connections.emit(NODE_ADDRESS, node_status_frame())
    entry.runtime_data._async_tick(dt_util.utcnow())
    await unsubscribing.wait()
    unload = hass.async_create_task(hass.config_entries.async_unload(entry.entry_id))
    await asyncio.sleep(0)
    assert not unload.done()
    release.set()
    assert await unload
    assert not direct.is_connected
