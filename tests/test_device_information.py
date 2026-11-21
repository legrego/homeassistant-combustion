"""Firmware and hardware revisions, read on a schedule into the device registry."""

import asyncio
from unittest.mock import patch

import pytest
from bleak.exc import BleakError
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.combustion.const import (
    CONF_ENABLE_PREDICTIONS,
    DOMAIN,
    STALE_TIMEOUT,
)
from custom_components.combustion.revisions import REFRESH_INTERVAL, RETRY_INTERVAL
from tests.conftest import FakeClock
from tests.test_init import GAUGE, PROBE, setup_entry
from tests.test_prediction import connection  # noqa: F401 - fixture
from tests.test_repeater import (
    PROBE_FIRMWARE_REVISION,
    PROBE_HARDWARE_REVISION,
    connections,  # noqa: F401 - fixture
    enable_predictions,
    node_status_frame,
    relay,
    remaining,
    tick,
)
from tests.utils.bt_utils import (
    GAUGE_ADDRESS,
    NODE_ADDRESS,
    NODE_FIRMWARE,
    NODE_HARDWARE,
    PROBE_FIRMWARE,
    PROBE_HARDWARE,
    PROBE_SERIAL,
    gauge_payload,
    inject_bt_advertisement,
    probe_payload,
    service_info,
)


@pytest.fixture
def schedule() -> FakeClock:
    """Control the clock the revision schedule uses."""
    clock = FakeClock()
    with patch("custom_components.combustion.revisions.monotonic", clock):
        yield clock


def revisions(hass: HomeAssistant, identifier: str) -> tuple[str | None, str | None]:
    """Return the firmware and hardware revisions recorded for a device."""
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, identifier), entry.entry_id
    )
    assert device is not None
    return device.sw_version, device.hw_version


async def settle(hass: HomeAssistant, entry) -> None:
    """Tick, then let background sessions finish their reads and any brief connection end."""
    await tick(hass, entry)
    for _ in range(50):
        await asyncio.sleep(0)
    await hass.async_block_till_done()


def allow_brief_connection(entry) -> None:
    """Skip the pause between brief connections, which follows the real clock."""
    entry.runtime_data.revisions._monitor._retry_at = 0


def probe_requests(connections) -> list[int]:  # noqa: F811
    """Message types of revision requests sent through repeaters."""
    return [
        message_type
        for _, message_type, _ in connections.requests
        if message_type in (PROBE_FIRMWARE_REVISION, PROBE_HARDWARE_REVISION)
    ]


# With predictions enabled, prediction connections carry the reads.


async def test_direct_connection_reads_revisions(hass: HomeAssistant, connection):  # noqa: F811
    """A direct probe connection reads the probe's revisions, which outlast the connection."""
    client, establish = connection
    entry = await enable_predictions(hass)
    inject_bt_advertisement(hass, service_info(probe_payload()))
    await hass.async_block_till_done()  # Connected before the next tick.
    await settle(hass, entry)
    assert revisions(hass, PROBE) == (PROBE_FIRMWARE, PROBE_HARDWARE)
    assert establish.call_count == 1  # Read through the prediction connection.
    client.disconnect.assert_not_awaited()

    # Turning predictions off reloads the entry; the registry keeps the last revisions read.
    hass.config_entries.async_update_entry(
        entry, options={CONF_ENABLE_PREDICTIONS: False}
    )
    await hass.async_block_till_done()
    assert revisions(hass, PROBE) == (PROBE_FIRMWARE, PROBE_HARDWARE)


async def test_failed_read_retries_later(
    hass: HomeAssistant,
    connection,  # noqa: F811
    schedule,
):
    """A failed read leaves predictions running, and is retried after a while."""
    client, establish = connection
    client.read_gatt_char.side_effect = BleakError("read failed")
    entry = await enable_predictions(hass)
    inject_bt_advertisement(hass, service_info(probe_payload()))
    await hass.async_block_till_done()  # Connected before the next tick.
    await settle(hass, entry)
    assert revisions(hass, PROBE) == (None, None)
    client.disconnect.assert_not_awaited()
    assert client.read_gatt_char.await_count == 1

    await settle(hass, entry)
    assert client.read_gatt_char.await_count == 1
    schedule.advance(RETRY_INTERVAL)
    client.read_gatt_char.side_effect = None
    client.read_gatt_char.return_value = b"v2.5.0"
    await settle(hass, entry)
    assert revisions(hass, PROBE)[0] == "v2.5.0"
    assert establish.call_count == 1


async def test_repeater_reads_probe_revisions(
    hass: HomeAssistant,
    connections,  # noqa: F811
    schedule,
):
    """A repeater reads each probe it reports, then again a day later."""
    entry = await enable_predictions(hass)
    relay(hass)
    await settle(hass, entry)
    # Not until the repeater reports the probe; no brief connection is made meanwhile.
    assert revisions(hass, PROBE) == (None, None)
    assert connections.attempts == [NODE_ADDRESS]

    connections.emit(NODE_ADDRESS, node_status_frame(seconds=120))
    await settle(hass, entry)
    assert revisions(hass, PROBE) == (PROBE_FIRMWARE, PROBE_HARDWARE)
    assert probe_requests(connections) == [
        PROBE_FIRMWARE_REVISION,
        PROBE_HARDWARE_REVISION,
    ]
    assert all(serial == PROBE_SERIAL for *_, serial in connections.requests)

    # Later ticks and reconnections don't read again until the refresh interval passes.
    connections.emit(NODE_ADDRESS, node_status_frame(seconds=115))
    await settle(hass, entry)
    connections.clients[NODE_ADDRESS].drop()
    await hass.async_block_till_done()
    entry.runtime_data.repeater._retry_at = 0
    relay(hass)
    await settle(hass, entry)
    connections.emit(NODE_ADDRESS, node_status_frame(seconds=110))
    await settle(hass, entry)
    assert len(probe_requests(connections)) == 2
    assert remaining(hass) == "110"

    schedule.advance(REFRESH_INTERVAL)
    await settle(hass, entry)
    assert len(probe_requests(connections)) == 4
    assert connections.attempts == [NODE_ADDRESS, NODE_ADDRESS]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_unreachable_probe_keeps_session(hass: HomeAssistant, connections):  # noqa: F811
    """A repeater that can't reach a probe's revisions keeps relaying its predictions."""
    connections.unreachable_probes.add(PROBE_SERIAL)
    entry = await enable_predictions(hass)
    relay(hass)
    await settle(hass, entry)
    connections.emit(NODE_ADDRESS, node_status_frame(seconds=120))
    await settle(hass, entry)
    await settle(hass, entry)
    assert revisions(hass, PROBE) == (None, None)
    assert probe_requests(connections) == [PROBE_FIRMWARE_REVISION]
    connections.emit(NODE_ADDRESS, node_status_frame(seconds=100))
    await hass.async_block_till_done()
    assert remaining(hass) == "100"
    connections.clients[NODE_ADDRESS].disconnect.assert_not_awaited()
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_gauge_repeater_reads_its_own_revisions(
    hass: HomeAssistant,
    connections,  # noqa: F811
):
    """A Giant Grill Gauge used as the repeater reads its own revisions."""
    entry = await enable_predictions(hass)
    inject_bt_advertisement(hass, service_info(gauge_payload(), address=GAUGE_ADDRESS))
    await settle(hass, entry)
    await settle(hass, entry)
    assert connections.attempts == [GAUGE_ADDRESS]
    assert revisions(hass, GAUGE) == (NODE_FIRMWARE, NODE_HARDWARE)
    connections.clients[GAUGE_ADDRESS].disconnect.assert_not_awaited()
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_other_gauge_read_briefly(hass: HomeAssistant, connections):  # noqa: F811
    """A gauge that isn't the repeater gets a brief connection once a repeater session runs."""
    entry = await enable_predictions(hass)
    relay(hass)
    inject_bt_advertisement(
        hass, service_info(gauge_payload(), address=GAUGE_ADDRESS, rssi=-80)
    )
    await settle(hass, entry)
    assert connections.attempts == [NODE_ADDRESS]
    await settle(hass, entry)
    assert connections.attempts == [NODE_ADDRESS, GAUGE_ADDRESS]
    assert revisions(hass, GAUGE) == (NODE_FIRMWARE, NODE_HARDWARE)
    connections.clients[GAUGE_ADDRESS].disconnect.assert_awaited_once()
    connections.clients[NODE_ADDRESS].disconnect.assert_not_awaited()
    assert await hass.config_entries.async_unload(entry.entry_id)


# With predictions disabled, brief connections carry the reads.


async def test_passive_reads_through_repeater(
    hass: HomeAssistant,
    connections,  # noqa: F811
    schedule,
):
    """A brief repeater connection reads the probes it relays, then disconnects."""
    entry = await setup_entry(hass)
    relay(hass)
    await settle(hass, entry)
    assert entry.runtime_data.repeater is None
    assert connections.attempts == [NODE_ADDRESS]
    assert revisions(hass, PROBE) == (PROBE_FIRMWARE, PROBE_HARDWARE)
    connections.clients[NODE_ADDRESS].disconnect.assert_awaited_once()
    # No prediction entities appear from the brief session's relayed status.
    assert not er.async_get(hass).async_get_entity_id(
        "number", DOMAIN, f"{PROBE}--prediction_target"
    )

    allow_brief_connection(entry)
    await settle(hass, entry)
    assert connections.attempts == [NODE_ADDRESS]

    schedule.advance(REFRESH_INTERVAL)
    relay(hass)
    await settle(hass, entry)
    assert connections.attempts == [NODE_ADDRESS, NODE_ADDRESS]
    assert len(probe_requests(connections)) == 4
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_passive_reads_probe_directly(hass: HomeAssistant, connection):  # noqa: F811
    """Without a repeater, a probe heard directly is connected briefly."""
    client, establish = connection
    entry = await setup_entry(hass)
    inject_bt_advertisement(hass, service_info(probe_payload()))
    await settle(hass, entry)
    assert establish.call_count == 1
    assert revisions(hass, PROBE) == (PROBE_FIRMWARE, PROBE_HARDWARE)
    client.disconnect.assert_awaited_once()


async def test_passive_gauge_read(hass: HomeAssistant, connections):  # noqa: F811
    """A gauge is connected briefly to read its own revisions."""
    entry = await setup_entry(hass)
    inject_bt_advertisement(hass, service_info(gauge_payload(), address=GAUGE_ADDRESS))
    await settle(hass, entry)
    assert connections.attempts == [GAUGE_ADDRESS]
    assert revisions(hass, GAUGE) == (NODE_FIRMWARE, NODE_HARDWARE)
    connections.clients[GAUGE_ADDRESS].disconnect.assert_awaited_once()


async def test_unreachable_device_retries_later(
    hass: HomeAssistant,
    connections,  # noqa: F811
    schedule,
):
    """When no adapter can connect, the device is tried again after a while."""
    connections.unreachable.add(NODE_ADDRESS)
    entry = await setup_entry(hass)
    relay(hass)
    await settle(hass, entry)
    assert not connections.attempts
    connections.unreachable.clear()
    await settle(hass, entry)
    assert not connections.attempts

    schedule.advance(RETRY_INTERVAL)
    relay(hass)
    await settle(hass, entry)
    assert connections.attempts == [NODE_ADDRESS]
    assert revisions(hass, PROBE) == (PROBE_FIRMWARE, PROBE_HARDWARE)


async def test_offline_device_read_when_heard_again(
    hass: HomeAssistant,
    connection,  # noqa: F811
    clock,
):
    """A device is only read while it is online, so one that went offline is read on return."""
    client, establish = connection
    entry = await setup_entry(hass)
    inject_bt_advertisement(hass, service_info(probe_payload()))
    await hass.async_block_till_done()
    clock.advance(STALE_TIMEOUT + 1)
    await settle(hass, entry)
    assert not establish.called

    inject_bt_advertisement(hass, service_info(probe_payload(), time=1))
    await settle(hass, entry)
    assert establish.call_count == 1
    assert revisions(hass, PROBE) == (PROBE_FIRMWARE, PROBE_HARDWARE)


async def test_probe_without_route_is_not_connected(
    hass: HomeAssistant,
    connections,  # noqa: F811
):
    """A probe heard only through a node that no longer relays it can't be read yet."""
    entry = await setup_entry(hass)
    relay(hass)
    node = entry.runtime_data.meatnet.nodes[NODE_ADDRESS]
    node.relayed_probes.clear()
    await settle(hass, entry)
    assert not connections.attempts
    assert revisions(hass, PROBE) == (None, None)
