#!/usr/bin/env python3
"""Capture read-only probe prediction notifications as a shareable JSONL report.

Run from the repository root with its installed dependencies:
    poetry run python scripts/capture_predictions.py --seconds 120

Set a cooking target in the Combustion app or Display first. This script never
writes a target or starts/cancels a cook. It temporarily uses one BLE connection.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import platform
import sys
import tempfile
from contextlib import suppress
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from time import monotonic

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from combustion_ble import (
    COMBUSTION_MANUFACTURER_ID,
    MeatNet,
    ProbeAdvertisement,
    ProbeSession,
    parse_advertisement,
)
from combustion_ble.const import DEVICE_STATUS_CHARACTERISTIC


def positive_seconds(value: str) -> float:
    """Reject nonpositive or nonfinite capture durations."""
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("must be a positive, finite number")
    return result


async def capture(args: argparse.Namespace, emit) -> int:
    """Discover a direct probe and record raw and decoded connected statuses."""
    meatnet = MeatNet()
    probes: dict[str, BLEDevice] = {}
    seen: set[str] = set()

    def discovered(device, advertisement):
        payload = advertisement.manufacturer_data.get(COMBUSTION_MANUFACTURER_ID)
        if payload is None:
            return
        meatnet.handle_advertisement(device.address, payload, advertisement.rssi)
        parsed = parse_advertisement(payload)
        if not isinstance(parsed, ProbeAdvertisement) or not parsed.has_probe:
            return
        serial = parsed.serial_number_string
        route = "repeater" if parsed.is_repeated else "direct"
        identity = f"{serial}/{route}"
        if identity not in seen:
            seen.add(identity)
            emit("discovery", serial=serial, route=route, rssi=advertisement.rssi)
        if not parsed.is_repeated:
            probes[serial] = device

    emit("scan_started", seconds=args.scan_seconds)
    async with BleakScanner(detection_callback=discovered):
        await asyncio.sleep(args.scan_seconds)
    emit("scan_finished", direct_probes=sorted(probes))
    if args.list:
        return 0
    serial = args.serial.upper().removeprefix("0X").zfill(8) if args.serial else None
    if serial is None and len(probes) == 1:
        serial = next(iter(probes))
    if serial not in probes:
        emit(
            "selection_failed",
            requested_serial=serial,
            message="Use --serial with a direct probe listed above. Take the probe out of its charger and keep it near this computer.",
        )
        return 1

    probe = meatnet.probes[int(serial, 16)]
    session = None
    disconnected = asyncio.Event()
    intentional_disconnect = False
    unexpected_disconnect = False
    received = 0
    malformed = 0
    previous = None
    max_gap = 0.0
    states: set[str] = set()
    started = monotonic()

    def on_disconnect(_client):
        nonlocal unexpected_disconnect
        if session is not None:
            session.disconnected()
        unexpected_disconnect |= not intentional_disconnect
        emit("disconnected", intentional=intentional_disconnect)
        disconnected.set()

    def notification(_sender, data):
        nonlocal received, malformed, previous, max_gap
        now = monotonic()
        if previous is not None:
            max_gap = max(max_gap, now - previous)
        previous = now
        prediction = probe.prediction
        if prediction is None:
            malformed += 1
            emit(
                "malformed_status",
                raw_hex=bytes(data).hex(),
                error="Library did not publish a prediction status",
            )
            return
        received += 1
        states.add(prediction.state.name)
        emit(
            "status",
            elapsed_seconds=round(now - started, 3),
            raw_hex=bytes(data).hex(),
            packet_bytes=len(data),
            state=prediction.state.name,
            mode=prediction.mode.name,
            prediction_type=prediction.type.name,
            target_celsius=prediction.set_point_temperature,
            estimated_core_celsius=prediction.estimated_core_temperature,
            prediction_seconds=prediction.seconds_remaining,
        )

    class RecordingClient:
        """Observe raw notifications while ProbeSession owns their interpretation."""

        def __init__(self, client):
            self.client = client

        def __getattr__(self, name):
            return getattr(self.client, name)

        async def start_notify(self, characteristic, callback):
            if characteristic != DEVICE_STATUS_CHARACTERISTIC:
                return await self.client.start_notify(characteristic, callback)

            def record(sender, data):
                nonlocal malformed
                previous_update = probe.prediction_updated_at
                callback(sender, data)
                # Identical valid packets refresh the public timestamp too.
                if probe.prediction_updated_at == previous_update:
                    malformed += 1
                    emit(
                        "malformed_status",
                        raw_hex=bytes(data).hex(),
                        error="Status was ignored by the library",
                    )
                else:
                    notification(sender, data)

            await self.client.start_notify(characteristic, record)

    try:
        emit("connecting", serial=serial)
        async with BleakClient(
            probes[serial], disconnected_callback=on_disconnect, timeout=30
        ) as client:
            emit("connected", serial=serial)
            session = ProbeSession(meatnet, int(serial, 16), RecordingClient(client))
            try:
                async with asyncio.timeout(30):
                    await session.start()
                emit("subscribed", capture_seconds=args.seconds)
                with suppress(TimeoutError):
                    await asyncio.wait_for(disconnected.wait(), timeout=args.seconds)
            finally:
                intentional_disconnect = True
                try:
                    async with asyncio.timeout(10):
                        await session.stop()
                finally:
                    # The outer client context releases the connection even if stop fails.
                    session.disconnected()
    finally:
        tail_gap = monotonic() - previous if previous is not None else 0
        emit(
            "summary",
            valid_notifications=received,
            malformed_notifications=malformed,
            states_seen=sorted(states),
            max_notification_gap_seconds=round(max(max_gap, tail_gap), 3),
            unexpected_disconnect=unexpected_disconnect,
            message="No prediction status was received."
            if received == 0
            else "Raw and decoded statuses captured. Share this report for verification.",
        )
    return (
        0
        if received
        and not malformed
        and intentional_disconnect
        and not unexpected_disconnect
        else 1
    )


def main() -> int:
    """Write a flushed report, including errors and interruptions."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", help="probe serial printed on the probe (hex)")
    parser.add_argument("--seconds", type=positive_seconds, default=60)
    parser.add_argument("--scan-seconds", type=positive_seconds, default=10)
    parser.add_argument(
        "--list", action="store_true", help="list probes without connecting"
    )
    default = Path(tempfile.gettempdir()) / datetime.now(UTC).strftime(
        "combustion-predictions-%Y%m%dT%H%M%S%fZ.jsonl"
    )
    parser.add_argument("--output", type=Path, default=default)
    args = parser.parse_args()
    print(f"Report: {args.output.resolve()}", file=sys.stderr)
    with args.output.open("x", encoding="utf-8") as report:

        def emit(event, **fields):
            row = json.dumps(
                {"time": datetime.now(UTC).isoformat(), "event": event, **fields}
            )
            print(row, flush=True)
            report.write(row + "\n")
            report.flush()

        emit(
            "environment",
            python=platform.python_version(),
            system=platform.system(),
            bleak=version("bleak"),
            combustion_ble=version("combustion_ble"),
        )
        try:
            return asyncio.run(capture(args, emit))
        except KeyboardInterrupt:
            emit("interrupted")
            return 130
        except Exception as error:
            emit("error", error_type=type(error).__name__, message=str(error))
            return 1
        finally:
            print(f"Report saved: {args.output.resolve()}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
