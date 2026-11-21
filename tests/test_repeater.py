"""Predictions through a MeatNet repeater connection, and how it replaces direct connections."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from combustion_ble.const import UART_TX_CHARACTERISTIC
from combustion_ble.utilities.crc16ccitt import crc16ccitt
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.combustion.const import CONF_ENABLE_PREDICTIONS
from tests.test_init import PROBE, get_state, setup_entry
from tests.test_prediction import status_packet
from tests.utils.bt_utils import (
    NODE_ADDRESS,
    PROBE_ADDRESS,
    PROBE_SERIAL,
    inject_bt_advertisement,
    probe_payload,
    service_info,
)

OTHER_NODE_ADDRESS = "CC:CC:CC:CC:CC:05"
THIRD_NODE_ADDRESS = "CC:CC:CC:CC:CC:06"


def node_status_frame(*, serial_number=PROBE_SERIAL, hop=0, **status) -> bytes:
    """Build a repeater's Probe Status (0x45) UART message, per the MeatNet node spec."""
    payload = (
        serial_number.to_bytes(4, "little")
        + bytes(status_packet(**status))
        + bytes(18)  # Food safe data and status
        + bytes([hop << 6])  # Network information
        + bytes(1 + 1 + 22 + 22 + 4 + 2)  # Overheating, preferences, alarms, session
    )
    body = b"\x45" + (1).to_bytes(4, "little") + bytes([len(payload)]) + payload
    return b"\xca\xfe" + crc16ccitt(body).to_bytes(2, "little") + body


class Connections:
    """Fake Bluetooth clients, one per connected address, with no real I/O."""

    def __init__(self) -> None:
        """Initialize."""
        self.clients: dict[str, Mock] = {}
        self.attempts: list[str] = []
        self.failing: set[str] = set()
        self.unreachable: set[str] = set()

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
            if client.is_connected:
                client.is_connected = False
                disconnected_callback(client)

        client.start_notify = AsyncMock(side_effect=start_notify)
        client.stop_notify = AsyncMock()
        client.write_gatt_char = AsyncMock()
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
    for client in fake.clients.values():
        client.write_gatt_char.assert_not_called()  # Predictions are read-only.


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
    assert get_state(hass, "sensor", f"{PROBE}--prediction_target").state == "65.0"
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
    """With predictions disabled, repeaters are never connected."""
    entry = await setup_entry(hass)
    relay(hass)
    await tick(hass, entry)
    assert entry.runtime_data.repeater is None
    assert not connections.attempts
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
