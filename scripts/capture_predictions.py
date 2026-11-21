#!/usr/bin/env python3
"""Capture read-only probe prediction and SafeCook notifications as a shareable JSONL report.

Run from the repository root with its installed dependencies:
    poetry run python scripts/capture_predictions.py --seconds 120
    poetry run python scripts/capture_predictions.py --repeater --note "never set up SafeCook"

By default this connects to one probe directly. ``--repeater`` instead connects to a MeatNet
repeater (Display, Booster, Giant Grill Gauge, or Engine) and records every probe it relays; pass a
repeater's address to choose one. ``--note`` labels the report, for example with what was set up
in the Combustion app beforehand.

Set any cooking target or SafeCook program in the Combustion app or Display first. This script
never writes a target or a SafeCook program, and never starts, cancels, or resets anything. It
temporarily uses one BLE connection.
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
from enum import Enum
from importlib.metadata import version
from pathlib import Path
from time import monotonic

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from combustion_ble import (
    COMBUSTION_MANUFACTURER_ID,
    MeatNet,
    NodeSession,
    ProbeAdvertisement,
    ProbeSession,
    parse_advertisement,
)
from combustion_ble.const import DEVICE_STATUS_CHARACTERISTIC, UART_TX_CHARACTERISTIC

# Food safe data and status follow the 30-byte prediction-era probe status.
FOOD_SAFE_BYTES = slice(30, 48)

# Program fields: what was chosen in the app. The rest of FoodSafe is progress.
FOOD_SAFE_PROGRAM_FIELDS = (
    "mode",
    "product",
    "serving",
    "threshold_celsius",
    "z_value",
    "reference_celsius",
    "d_value",
    "target_log_reduction",
)


def positive_seconds(value: str) -> float:
    """Reject nonpositive or nonfinite capture durations."""
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("must be a positive, finite number")
    return result


def _name(value):
    """Enum member name, or the raw integer the library kept for an unknown value."""
    return value.name if isinstance(value, Enum) else value


def food_safe_fields(food_safe) -> dict | None:
    """Decode SafeCook program and progress. None when the firmware doesn't report it."""
    if food_safe is None:
        return None
    return {
        "state": _name(food_safe.state),
        "log_reduction": food_safe.log_reduction,
        "seconds_above_threshold": food_safe.seconds_above_threshold,
        "mode": _name(food_safe.mode),
        "product": _name(food_safe.product),
        "serving": _name(food_safe.serving),
        "threshold_celsius": food_safe.threshold_temperature,
        "z_value": food_safe.z_value,
        "reference_celsius": food_safe.reference_temperature,
        "d_value": food_safe.d_value,
        "target_log_reduction": food_safe.target_log_reduction,
    }


class Recorder:
    """Emit a status row whenever the library publishes new connected data for a probe."""

    def __init__(self, meatnet: MeatNet, emit, started: float) -> None:
        """Initialize."""
        self.meatnet = meatnet
        self.emit = emit
        self.started = started
        self.received = 0
        self.malformed = 0
        self.previous: float | None = None
        self.max_gap = 0.0
        self.states: set[str] = set()
        self.food_safe: dict[str, dict] = {}
        self._updated: dict[int, float | None] = {}

    def updated(self, probe) -> bool:
        """Whether the probe's connected status changed since the last call."""
        last = self._updated.get(probe.serial_number)
        self._updated[probe.serial_number] = probe.prediction_updated_at
        return probe.prediction_updated_at is not None and (
            probe.prediction_updated_at != last
        )

    def status(self, probe, **extra) -> None:
        """Record a probe's current prediction and SafeCook data."""
        now = monotonic()
        if self.previous is not None:
            self.max_gap = max(self.max_gap, now - self.previous)
        self.previous = now
        prediction = probe.prediction
        self.received += 1
        self.states.add(prediction.state.name)
        food_safe = food_safe_fields(probe.food_safe)
        seen = self.food_safe.setdefault(
            probe.serial_number_string,
            {"reported": False, "states": set(), "programs": []},
        )
        if food_safe is not None:
            seen["reported"] = True
            seen["states"].add(food_safe["state"])
            program = {field: food_safe[field] for field in FOOD_SAFE_PROGRAM_FIELDS}
            if program not in seen["programs"]:
                seen["programs"].append(program)
        self.emit(
            "status",
            serial=probe.serial_number_string,
            elapsed_seconds=round(now - self.started, 3),
            **extra,
            state=prediction.state.name,
            mode=prediction.mode.name,
            prediction_type=prediction.type.name,
            target_celsius=prediction.set_point_temperature,
            estimated_core_celsius=prediction.estimated_core_temperature,
            prediction_seconds=prediction.seconds_remaining,
            food_safe=food_safe,
        )

    def summary(self, unexpected_disconnect: bool) -> None:
        """Record what the capture saw, including every distinct SafeCook program per probe."""
        tail_gap = monotonic() - self.previous if self.previous is not None else 0
        self.emit(
            "summary",
            valid_notifications=self.received,
            malformed_notifications=self.malformed,
            states_seen=sorted(self.states),
            food_safe_seen={
                serial: {
                    "reported": seen["reported"],
                    "states": sorted(seen["states"]),
                    "programs": seen["programs"],
                }
                for serial, seen in sorted(self.food_safe.items())
            },
            max_notification_gap_seconds=round(max(self.max_gap, tail_gap), 3),
            unexpected_disconnect=unexpected_disconnect,
            message="No prediction status was received."
            if self.received == 0
            else "Raw and decoded statuses captured. Share this report for verification.",
        )


class RecordingClient:
    """Observe raw notifications while the library's session owns their interpretation."""

    def __init__(self, client, characteristic, observe) -> None:
        """Initialize."""
        self.client = client
        self.characteristic = characteristic
        self.observe = observe

    def __getattr__(self, name):
        """Delegate everything else to the real client."""
        return getattr(self.client, name)

    async def start_notify(self, characteristic, callback):
        """Pass ``characteristic`` notifications through ``observe`` after the session."""
        if characteristic != self.characteristic:
            return await self.client.start_notify(characteristic, callback)

        def record(sender, data):
            self.observe(callback, sender, data)

        await self.client.start_notify(characteristic, record)


async def scan(args: argparse.Namespace, emit, meatnet: MeatNet):
    """Discover probes and repeaters. Returns the Bluetooth devices of each, by key."""
    probes: dict[str, BLEDevice] = {}
    repeaters: dict[str, BLEDevice] = {}
    seen: set[str] = set()

    def discovered(device, advertisement):
        payload = advertisement.manufacturer_data.get(COMBUSTION_MANUFACTURER_ID)
        if payload is None:
            return
        meatnet.handle_advertisement(device.address, payload, advertisement.rssi)
        if device.address in meatnet.nodes:
            repeaters[device.address] = device
        parsed = parse_advertisement(payload)
        if not isinstance(parsed, ProbeAdvertisement) or not parsed.has_probe:
            return
        serial = parsed.serial_number_string
        route = "repeater" if parsed.is_repeated else "direct"
        identity = f"{serial}/{route}/{device.address if parsed.is_repeated else ''}"
        if identity not in seen:
            seen.add(identity)
            emit(
                "discovery",
                serial=serial,
                route=route,
                repeater=device.address if parsed.is_repeated else None,
                rssi=advertisement.rssi,
            )
        if not parsed.is_repeated:
            probes[serial] = device

    emit("scan_started", seconds=args.scan_seconds)
    async with BleakScanner(detection_callback=discovered):
        await asyncio.sleep(args.scan_seconds)
    emit(
        "scan_finished",
        direct_probes=sorted(probes),
        repeaters={
            address: {
                "product": meatnet.nodes[address].product_type.name,
                "serial": meatnet.nodes[address].serial_number,
                "rssi": meatnet.nodes[address].rssi,
                "relayed_probes": sorted(
                    f"{serial:08X}" for serial in meatnet.nodes[address].relayed_probes
                ),
            }
            for address in sorted(repeaters)
        },
    )
    return probes, repeaters


def select_repeater(args, meatnet: MeatNet, repeaters: dict[str, BLEDevice], serial):
    """Choose the requested repeater, or the strongest one relaying the requested probe."""
    if args.repeater != "auto":
        address = args.repeater.upper()
        return address if address in repeaters else None
    candidates = [
        address
        for address in repeaters
        if serial is None or int(serial, 16) in meatnet.nodes[address].relayed_probes
    ]
    return max(
        candidates,
        key=lambda address: meatnet.nodes[address].rssi or -200,
        default=None,
    )


async def capture(args: argparse.Namespace, emit) -> int:
    """Discover a probe or repeater and record raw and decoded connected statuses."""
    meatnet = MeatNet()
    probes, repeaters = await scan(args, emit, meatnet)
    if args.list:
        return 0
    serial = args.serial.upper().removeprefix("0X").zfill(8) if args.serial else None

    if args.repeater:
        address = select_repeater(args, meatnet, repeaters, serial)
        if address is None:
            emit(
                "selection_failed",
                requested_repeater=args.repeater,
                requested_serial=serial,
                message="Use --repeater with a repeater address listed above, or drop it to connect to a probe directly.",
            )
            return 1
        return await _capture_repeater(args, emit, meatnet, repeaters[address], serial)

    if serial is None and len(probes) == 1:
        serial = next(iter(probes))
    if serial not in probes:
        emit(
            "selection_failed",
            requested_serial=serial,
            message="Use --serial with a direct probe listed above, or --repeater. Take the probe out of its charger and keep it near this computer.",
        )
        return 1
    return await _capture_probe(args, emit, meatnet, probes[serial], serial)


async def _capture_probe(args, emit, meatnet, device: BLEDevice, serial: str) -> int:
    probe = meatnet.probes[int(serial, 16)]
    recorder = Recorder(meatnet, emit, monotonic())
    recorder.updated(probe)

    def observe(callback, sender, data):
        callback(sender, data)
        # Identical valid packets refresh the public timestamp too.
        if not recorder.updated(probe):
            recorder.malformed += 1
            emit(
                "malformed_status",
                raw_hex=bytes(data).hex(),
                error="Status was ignored by the library",
            )
            return
        raw = bytes(data)
        recorder.status(
            probe,
            raw_hex=raw.hex(),
            packet_bytes=len(raw),
            food_safe_raw_hex=raw[FOOD_SAFE_BYTES].hex()
            if len(raw) >= FOOD_SAFE_BYTES.stop
            else None,
        )

    return await _run(
        args,
        emit,
        recorder,
        device,
        lambda client: ProbeSession(
            meatnet,
            int(serial, 16),
            RecordingClient(client, DEVICE_STATUS_CHARACTERISTIC, observe),
        ),
        label={"serial": serial},
        valid=lambda: not recorder.malformed,
    )


async def _capture_repeater(args, emit, meatnet, device: BLEDevice, serial) -> int:
    recorder = Recorder(meatnet, emit, monotonic())
    for probe in meatnet.probes.values():
        recorder.updated(probe)

    def observe(callback, sender, data):
        # UART notifications can split or combine messages, so record them as they arrive. The
        # Probe Status (0x45) frames inside hold the raw food safe bytes.
        emit("uart", raw_hex=bytes(data).hex())
        callback(sender, data)
        for probe in list(meatnet.probes.values()):
            if serial is not None and probe.serial_number_string != serial:
                continue
            if recorder.updated(probe):
                recorder.status(probe, repeater=device.address)

    return await _run(
        args,
        emit,
        recorder,
        device,
        lambda client: NodeSession(
            meatnet,
            device.address,
            RecordingClient(client, UART_TX_CHARACTERISTIC, observe),
        ),
        label={"repeater": device.address, "serial": serial},
        valid=lambda: True,
    )


async def _run(args, emit, recorder, device, make_session, *, label, valid) -> int:
    """Connect, run the session for the capture duration, and always release the connection."""
    session = None
    disconnected = asyncio.Event()
    intentional_disconnect = False
    unexpected_disconnect = False

    def on_disconnect(_client):
        nonlocal unexpected_disconnect
        if session is not None:
            session.disconnected()
        unexpected_disconnect |= not intentional_disconnect
        emit("disconnected", intentional=intentional_disconnect)
        disconnected.set()

    try:
        emit("connecting", **label)
        async with BleakClient(
            device, disconnected_callback=on_disconnect, timeout=30
        ) as client:
            emit("connected", **label)
            session = make_session(client)
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
        recorder.summary(unexpected_disconnect)
    return (
        0
        if recorder.received
        and valid()
        and intentional_disconnect
        and not unexpected_disconnect
        else 1
    )


def main() -> int:
    """Write a flushed report, including errors and interruptions."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--serial", help="probe serial printed on the probe (hex)")
    parser.add_argument(
        "--repeater",
        nargs="?",
        const="auto",
        metavar="ADDRESS",
        help="connect to a MeatNet repeater instead of a probe; picks the strongest without an address",
    )
    parser.add_argument(
        "--note", help="label for this capture, such as what was set up in the app"
    )
    parser.add_argument("--seconds", type=positive_seconds, default=60)
    parser.add_argument("--scan-seconds", type=positive_seconds, default=10)
    parser.add_argument(
        "--list",
        action="store_true",
        help="list probes and repeaters without connecting",
    )
    default = Path(tempfile.gettempdir()) / datetime.now(UTC).strftime(
        "combustion-capture-%Y%m%dT%H%M%S%fZ.jsonl"
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
            note=args.note,
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
