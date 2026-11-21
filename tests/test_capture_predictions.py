"""Exercise the diagnostic capture through the library's real sessions."""

import asyncio
from argparse import Namespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from combustion_ble.const import DEVICE_STATUS_CHARACTERISTIC, UART_TX_CHARACTERISTIC

from scripts.capture_predictions import capture
from tests.test_prediction import food_safe_bytes, status_packet
from tests.test_repeater import node_status_frame
from tests.utils.bt_utils import (
    NODE_ADDRESS,
    PROBE_SERIAL,
    probe_payload,
    service_info,
)


def fake_client(notifications):
    """Make a connected client that sends ``notifications(characteristic, callback)`` on subscribe."""
    client = Mock()
    client.is_connected = True
    client.stop_notify = AsyncMock()
    client.write_gatt_char = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.on_disconnect = None

    async def leave(*args):
        client.is_connected = False
        client.on_disconnect(client)

    client.__aexit__ = AsyncMock(side_effect=leave)

    async def subscribe(characteristic, callback):
        notifications(characteristic, callback)

    client.start_notify = AsyncMock(side_effect=subscribe)
    return client


async def run_capture(advertisements, client, **args):
    """Run a capture against fake Bluetooth and return its report rows and exit code."""
    rows = []

    def connect(device, *, disconnected_callback, timeout):
        client.address = device.address
        client.on_disconnect = disconnected_callback
        return client

    class Scanner:
        def __init__(self, detection_callback):
            self.callback = detection_callback

        async def __aenter__(self):
            for info in advertisements:
                self.callback(info.device, info.advertisement)
            return self

        async def __aexit__(self, *args):
            pass

    namespace = Namespace(
        scan_seconds=0.001, seconds=0.001, serial=None, list=False, repeater=None
    )
    for key, value in args.items():
        setattr(namespace, key, value)
    with (
        patch("scripts.capture_predictions.BleakScanner", Scanner),
        patch("scripts.capture_predictions.BleakClient", connect),
    ):
        result = await capture(
            namespace, lambda event, **fields: rows.append({"event": event, **fields})
        )
    client.write_gatt_char.assert_not_called()
    if client.on_disconnect is not None:
        assert not client.is_connected
    return rows, result


@pytest.mark.parametrize("scenario", ["success", "disconnect", "malformed"])
async def test_capture_session(scenario):
    """Capture statuses and connection failures without Bluetooth hardware or cook writes."""
    client = None

    def notify(characteristic, callback):
        if characteristic == DEVICE_STATUS_CHARACTERISTIC:
            callback(
                None,
                bytearray(b"short") if scenario == "malformed" else status_packet(),
            )
            if scenario == "disconnect":
                asyncio.get_running_loop().call_soon(client.on_disconnect, client)

    client = fake_client(notify)
    rows, result = await run_capture([service_info(probe_payload())], client)
    assert result == (0 if scenario == "success" else 1)
    assert rows[-1]["unexpected_disconnect"] == (scenario == "disconnect")
    assert rows[-1]["malformed_notifications"] == (1 if scenario == "malformed" else 0)
    if scenario != "malformed":
        status = next(row for row in rows if row["event"] == "status")
        assert status["prediction_seconds"] == 120
        assert status["target_celsius"] == 65.0
        assert status["state"] == "PREDICTING"
        # Firmware without SafeCook.
        assert status["food_safe"] is None
        assert status["food_safe_raw_hex"] is None
        assert rows[-1]["food_safe_seen"]["10001CCC"]["reported"] is False


async def test_capture_direct_food_safe():
    """Record the decoded SafeCook program and its raw bytes from a direct probe."""
    food_safe = food_safe_bytes()

    def notify(characteristic, callback):
        if characteristic == DEVICE_STATUS_CHARACTERISTIC:
            callback(None, status_packet(food_safe=food_safe))

    rows, result = await run_capture(
        [service_info(probe_payload())], fake_client(notify)
    )
    assert result == 0
    status = next(row for row in rows if row["event"] == "status")
    assert status["food_safe_raw_hex"] == food_safe.hex()
    assert status["food_safe"]["state"] == "NOT_SAFE"
    assert status["food_safe"]["product"] == "MEATS_GROUND"
    assert status["food_safe"]["log_reduction"] == 2.3
    seen = rows[-1]["food_safe_seen"]["10001CCC"]
    assert seen["reported"] is True
    assert seen["states"] == ["NOT_SAFE"]
    assert seen["programs"] == [
        {
            "mode": "INTEGRATED",
            "product": "MEATS_GROUND",
            "serving": "SERVED_IMMEDIATELY",
            "threshold_celsius": 54.4,
            "z_value": 7.0,
            "reference_celsius": 70.0,
            "d_value": 0.2,
            "target_log_reduction": 6.5,
        }
    ]


@pytest.mark.parametrize("repeater", ["auto", NODE_ADDRESS.lower(), "AA:AA"])
async def test_capture_repeater(repeater):
    """Record every probe a repeater relays, with its raw UART notifications."""
    other_serial = 0x10002DDD
    frames = node_status_frame(
        food_safe=food_safe_bytes(mode=0, product=0, state=1)
    ) + node_status_frame(serial_number=other_serial)

    def notify(characteristic, callback):
        if characteristic == UART_TX_CHARACTERISTIC:
            # One notification holds both frames.
            callback(None, bytearray(frames))

    advertisements = [
        service_info(probe_payload(hop=0), address=NODE_ADDRESS),
        service_info(
            probe_payload(serial_number=other_serial, hop=0), address=NODE_ADDRESS
        ),
    ]
    rows, result = await run_capture(
        advertisements, fake_client(notify), repeater=repeater
    )
    if repeater == "AA:AA":
        assert result == 1
        assert rows[-1]["event"] == "selection_failed"
        return
    assert result == 0
    scan = next(row for row in rows if row["event"] == "scan_finished")
    assert scan["repeaters"][NODE_ADDRESS]["relayed_probes"] == [
        f"{PROBE_SERIAL:08X}",
        f"{other_serial:08X}",
    ]
    assert (
        next(row for row in rows if row["event"] == "uart")["raw_hex"] == frames.hex()
    )
    statuses = {row["serial"]: row for row in rows if row["event"] == "status"}
    assert statuses["10001CCC"]["repeater"] == NODE_ADDRESS
    assert statuses["10001CCC"]["food_safe"]["mode"] == "SIMPLIFIED"
    assert statuses["10001CCC"]["food_safe"]["product"] == "DEFAULT"
    assert statuses["10001CCC"]["food_safe"]["state"] == "SAFE"
    # All-zero food safe bytes still decode: a reset program looks like this.
    assert statuses["10002DDD"]["food_safe"]["state"] == "NOT_SAFE"
    assert set(rows[-1]["food_safe_seen"]) == {"10001CCC", "10002DDD"}


async def test_capture_repeater_for_one_probe():
    """With --serial, a repeater capture only records that probe."""
    frames = node_status_frame() + node_status_frame(serial_number=0x10002DDD)

    def notify(characteristic, callback):
        if characteristic == UART_TX_CHARACTERISTIC:
            callback(None, bytearray(frames))

    advertisements = [
        service_info(probe_payload(hop=0), address=NODE_ADDRESS),
        service_info(
            probe_payload(serial_number=0x10002DDD, hop=0), address=NODE_ADDRESS
        ),
    ]
    rows, result = await run_capture(
        advertisements, fake_client(notify), repeater="auto", serial="10001ccc"
    )
    assert result == 0
    assert [row["serial"] for row in rows if row["event"] == "status"] == ["10001CCC"]
