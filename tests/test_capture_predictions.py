"""Exercise the diagnostic capture through the library's real ProbeSession."""

import asyncio
from argparse import Namespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from combustion_ble.const import DEVICE_STATUS_CHARACTERISTIC

from scripts.capture_predictions import capture
from tests.test_prediction import status_packet
from tests.utils.bt_utils import probe_payload, service_info


@pytest.mark.parametrize("scenario", ["success", "disconnect", "malformed"])
async def test_capture_session(scenario):
    """Capture statuses and connection failures without Bluetooth hardware or cook writes."""
    rows = []
    info = service_info(probe_payload())
    client = Mock()
    client.is_connected = True
    client.stop_notify = AsyncMock()
    client.write_gatt_char = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    on_disconnect = None

    async def leave(*args):
        client.is_connected = False
        on_disconnect(client)

    client.__aexit__ = AsyncMock(side_effect=leave)

    async def subscribe(characteristic, callback):
        if characteristic == DEVICE_STATUS_CHARACTERISTIC:
            callback(
                None,
                bytearray(b"short") if scenario == "malformed" else status_packet(),
            )
            if scenario == "disconnect":
                asyncio.get_running_loop().call_soon(on_disconnect, client)

    client.start_notify = AsyncMock(side_effect=subscribe)

    def connect(device, *, disconnected_callback, timeout):
        nonlocal on_disconnect
        on_disconnect = disconnected_callback
        return client

    class Scanner:
        def __init__(self, detection_callback):
            self.callback = detection_callback

        async def __aenter__(self):
            self.callback(info.device, info.advertisement)
            return self

        async def __aexit__(self, *args):
            pass

    args = Namespace(scan_seconds=0.001, seconds=0.001, serial=None, list=False)
    with (
        patch("scripts.capture_predictions.BleakScanner", Scanner),
        patch("scripts.capture_predictions.BleakClient", connect),
    ):
        result = await capture(
            args, lambda event, **fields: rows.append({"event": event, **fields})
        )
    assert result == (0 if scenario == "success" else 1)
    assert rows[-1]["unexpected_disconnect"] == (scenario == "disconnect")
    assert rows[-1]["malformed_notifications"] == (1 if scenario == "malformed" else 0)
    if scenario != "malformed":
        status = next(row for row in rows if row["event"] == "status")
        assert status["prediction_seconds"] == 120
        assert status["target_celsius"] == 65.0
        assert status["state"] == "PREDICTING"
    client.write_gatt_char.assert_not_called()
    assert not client.is_connected
