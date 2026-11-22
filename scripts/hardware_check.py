#!/usr/bin/env python3
"""Check the integration's connections against a real probe and MeatNet repeater.

Run from the repository root with its installed dependencies, with a probe out of its charger and
a repeater (Booster, Display, Engine, or Giant Grill Gauge) near this computer:
    poetry run python scripts/hardware_check.py
    poetry run python scripts/hardware_check.py --serial 10001CCC --target 60

This drives the integration's own connection monitors (custom_components/combustion/prediction.py)
outside Home Assistant, with a small stand-in for the two Home Assistant services they use. It
checks, in order:

1. Repeater connection: the repeater's own revisions, a probe's revisions read through it, and
   setting and cancelling the probe's target through it, confirmed by relayed status. Then a target
   for a probe that doesn't exist, to see how the repeater reports failure.
2. Direct probe connection: the probe's revisions, and setting and cancelling its target,
   confirmed by the probe's own status.
3. Brief revision connections, as made with predictions disabled: to the repeater (reading its own
   and the probe's revisions), then to the probe, each disconnecting when done.

THIS CHANGES THE PROBE'S COOKING TARGET. It restores the target the probe had when the check
started. Use ``--skip`` to leave out phases.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import sys
import tempfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from time import monotonic
from unittest.mock import patch

from bleak import BleakScanner
from bleak.backends.device import BLEDevice

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from combustion_ble import (  # noqa: E402
    COMBUSTION_MANUFACTURER_ID,
    MeatNet,
    PredictionCommandRejectedError,
    PredictionMode,
    Probe,
    ProbeAdvertisement,
    parse_advertisement,
)

from custom_components.combustion import prediction  # noqa: E402
from custom_components.combustion.prediction import (  # noqa: E402
    PredictionMonitor,
    RepeaterMonitor,
    RevisionMonitor,
)

PHASES = ("repeater", "direct", "brief")
NONEXISTENT_PROBE = 0x0BADF00D

CONNECT_DEADLINE = 60
"""Seconds to wait for a monitor's session, including bleak-retry-connector's retries."""
RELAYED_STATUS_DEADLINE = 30
"""Seconds to wait for a status through a repeater, which relays about every 5 seconds."""
DIRECT_STATUS_DEADLINE = 15
READ_DEADLINE = 75
"""Seconds to wait for a queued revision read: the integration allows 30 per request."""


class FakeHass:
    """The parts of Home Assistant the connection monitors use."""

    is_stopping = False

    def async_create_background_task(self, target, name, eager_start=True):
        """Run a monitor's connection task."""
        return asyncio.get_running_loop().create_task(target, name=name)


class Check:
    """Record pass/fail results to the terminal and a JSONL report."""

    def __init__(self, report) -> None:
        """Initialize."""
        self.report = report
        self.failures: list[str] = []
        self.passes = 0

    def emit(self, event: str, **fields) -> None:
        """Write a report row."""
        row = {"time": datetime.now(UTC).isoformat(), "event": event, **fields}
        self.report.write(json.dumps(row, default=str) + "\n")
        self.report.flush()

    def info(self, message: str, **fields) -> None:
        """Print and record progress."""
        print(f"       {message}", flush=True)
        self.emit("info", message=message, **fields)

    def result(self, name: str, ok: bool, detail: str = "", **fields) -> bool:
        """Print and record one check's result."""
        print(
            f"{'PASS' if ok else 'FAIL'}   {name}{': ' + detail if detail else ''}",
            flush=True,
        )
        self.emit("check", name=name, ok=ok, detail=detail, **fields)
        if ok:
            self.passes += 1
        else:
            self.failures.append(name)
        return ok


async def wait_until(predicate: Callable[[], bool], timeout: float) -> float | None:
    """Poll ``predicate``. Return seconds taken, or None on timeout."""
    started = monotonic()
    while monotonic() - started < timeout:
        if predicate():
            return monotonic() - started
        await asyncio.sleep(0.2)
    return monotonic() - started if predicate() else None


def describe(probe: Probe) -> str:
    """Summarize a probe's reported prediction."""
    p = probe.prediction
    if p is None:
        return "no status"
    return f"mode={p.mode.name} state={p.state.name} set_point={p.set_point_temperature:.1f}"


def has_target(probe: Probe, celsius: float) -> bool:
    """Whether the probe reports a removal target of ``celsius``."""
    p = probe.prediction
    return (
        p is not None
        and p.mode is PredictionMode.TIME_TO_REMOVAL
        and abs(p.set_point_temperature - celsius) < 0.05
    )


def has_no_target(probe: Probe) -> bool:
    """Whether the probe reports no prediction mode."""
    return probe.prediction is not None and probe.prediction.mode is PredictionMode.NONE


def original_target(probe: Probe) -> float | None:
    """Return the removal target to restore, if the probe had one."""
    p = probe.prediction
    if p is not None and p.mode is not PredictionMode.NONE:
        return p.set_point_temperature
    return None


def capture_original(check: Check, probe: Probe, restore: dict) -> None:
    """Remember the probe's target before the first command, to put it back afterwards."""
    if not restore["captured"]:
        restore["captured"] = True
        restore["target"] = original_target(probe)
        check.info(f"Probe's target before the check: {describe(probe)}")


async def restore_original(
    check: Check,
    label: str,
    restore: dict,
    send: Callable[[float | None], Awaitable[None]],
) -> None:
    """Put back the target the probe had, after a phase cancelled it."""
    if restore["target"] is None:
        return
    err, _ = await timed(send(restore["target"]))
    restore["pending"] = err is not None
    check.result(f"{label}: restore original target", err is None, str(err or ""))


async def queued_read(monitor, name: str, read) -> tuple[bool | None, float]:
    """Queue a revision read on a running monitor and wait for its result."""
    future: asyncio.Future[bool | None] = asyncio.get_running_loop().create_future()

    def done(succeeded):
        if not future.done():
            future.set_result(succeeded)

    started = monotonic()
    if not monitor.async_request_read(name, read, done):
        return None, 0.0
    try:
        async with asyncio.timeout(READ_DEADLINE):
            result = await future
    except TimeoutError:
        result = None
    return result, monotonic() - started


async def timed(command: Awaitable[None]) -> tuple[BaseException | None, float]:
    """Run a command. Return its error, if any, and how long it took."""
    started = monotonic()
    try:
        await command
    except Exception as err:  # noqa: BLE001 - reported, not handled
        return err, monotonic() - started
    return None, monotonic() - started


async def connect(check: Check, monitor, address: str, label: str) -> bool:
    """Start a monitor's session and wait for it to subscribe."""
    started = monotonic()
    if not monitor.async_connect(address):
        return check.result(f"{label}: connect", False, "no attempt started")
    took = await wait_until(lambda: monitor._session is not None, CONNECT_DEADLINE)
    if took is None:
        return check.result(
            f"{label}: connect", False, f"no session within {CONNECT_DEADLINE}s"
        )
    return check.result(f"{label}: connect", True, f"{monotonic() - started:.1f}s")


async def set_and_cancel(
    check: Check,
    label: str,
    probe: Probe,
    target: float,
    send: Callable[[float | None], Awaitable[None]],
    deadline: float,
) -> None:
    """Set a target, confirm it in status, cancel it, and confirm that too."""
    err, took = await timed(send(target))
    if check.result(
        f"{label}: set target {target:.1f} °C acknowledged",
        err is None,
        f"{took:.1f}s" if err is None else f"{type(err).__name__}: {err}",
    ):
        seen = await wait_until(lambda: has_target(probe, target), deadline)
        check.result(
            f"{label}: status reports the target",
            seen is not None,
            f"after {seen:.1f}s, {describe(probe)}"
            if seen is not None
            else describe(probe),
        )
    err, took = await timed(send(None))
    if check.result(
        f"{label}: cancel acknowledged",
        err is None,
        f"{took:.1f}s" if err is None else f"{type(err).__name__}: {err}",
    ):
        seen = await wait_until(lambda: has_no_target(probe), deadline)
        check.result(
            f"{label}: status reports no target",
            seen is not None,
            f"after {seen:.1f}s, {describe(probe)}"
            if seen is not None
            else describe(probe),
        )


def revisions(device) -> str:
    """Summarize a device's revisions."""
    return f"firmware={device.firmware_version} hardware={device.hardware_revision}"


async def check_repeater(
    check: Check,
    hass,
    meatnet: MeatNet,
    address: str,
    probe: Probe,
    args,
    restore: dict,
) -> None:
    """Phase 1: everything through a repeater connection."""
    print("\n== Repeater connection ==", flush=True)
    node = meatnet.nodes[address]
    monitor = RepeaterMonitor(hass, meatnet)
    try:
        if not await connect(check, monitor, address, "repeater"):
            return
        ok, took = await queued_read(
            monitor, "repeater", lambda session: session.read_device_information()
        )
        check.result(
            "repeater: own revisions",
            ok is True and node.firmware_version is not None,
            f"{revisions(node)} in {took:.1f}s",
        )

        seen = await wait_until(
            lambda: (
                monitor.session is not None
                and monitor.session._is_reporting(probe.serial_number)
            ),
            RELAYED_STATUS_DEADLINE,
        )
        if not check.result(
            "repeater: relays the probe's status",
            seen is not None,
            f"after {seen:.1f}s, {describe(probe)}" if seen is not None else "",
        ):
            return
        capture_original(check, probe, restore)

        probe.firmware_version = probe.hardware_revision = None
        ok, took = await queued_read(
            monitor,
            probe.serial_number_string,
            lambda session: session.read_probe_information(probe.serial_number),
        )
        check.result(
            "repeater: probe revisions through the repeater",
            ok is True and probe.firmware_version is not None,
            f"{revisions(probe)} in {took:.1f}s",
        )

        await set_and_cancel(
            check,
            "repeater",
            probe,
            args.target,
            lambda celsius: monitor.async_set_prediction(probe.serial_number, celsius),
            RELAYED_STATUS_DEADLINE,
        )
        await restore_original(
            check,
            "repeater",
            restore,
            lambda celsius: monitor.async_set_prediction(probe.serial_number, celsius),
        )

        if "reject" not in args.skip:
            err, took = await timed(
                monitor.async_set_prediction(NONEXISTENT_PROBE, args.target)
            )
            check.info(
                f"Target for nonexistent probe {NONEXISTENT_PROBE:08X}: "
                f"{type(err).__name__ if err else 'acknowledged'} after {took:.1f}s"
            )
            check.result(
                "repeater: nonexistent probe fails",
                isinstance(err, PredictionCommandRejectedError | TimeoutError),
                f"{type(err).__name__ if err else 'no error'} after {took:.1f}s",
            )
            check.result(
                "repeater: session survives the failure",
                monitor.session is not None,
            )
    finally:
        released = await monitor.async_stop()
        check.result("repeater: released", released)


async def check_direct(
    check: Check,
    hass,
    meatnet: MeatNet,
    address: str,
    probe: Probe,
    args,
    restore: dict,
) -> None:
    """Phase 2: everything through a direct probe connection."""
    print("\n== Direct probe connection ==", flush=True)
    monitor = PredictionMonitor(hass, meatnet, probe)
    try:
        if not await connect(check, monitor, address, "direct"):
            return
        probe.firmware_version = probe.hardware_revision = None
        ok, took = await queued_read(
            monitor,
            probe.serial_number_string,
            lambda session: session.read_device_information(),
        )
        check.result(
            "direct: probe revisions",
            ok is True and probe.firmware_version is not None,
            f"{revisions(probe)} in {took:.1f}s",
        )
        seen = await wait_until(
            lambda: probe.prediction is not None, DIRECT_STATUS_DEADLINE
        )
        if not check.result(
            "direct: status received",
            seen is not None,
            f"after {seen:.1f}s, {describe(probe)}" if seen is not None else "",
        ):
            return
        capture_original(check, probe, restore)
        await set_and_cancel(
            check,
            "direct",
            probe,
            args.target,
            monitor.async_set_prediction,
            DIRECT_STATUS_DEADLINE,
        )
        await restore_original(check, "direct", restore, monitor.async_set_prediction)
        check.result(
            "direct: session still running after commands", monitor.session is not None
        )
    finally:
        released = await monitor.async_stop()
        check.result("direct: released", released)


async def brief(
    check: Check, monitor: RevisionMonitor, label: str, address, probe, reads
) -> None:
    """Run one brief revision connection and confirm it reads and disconnects."""
    results: dict[str, bool | None] = {}
    finished = asyncio.Event()

    def done_for(name):
        def done(succeeded):
            results[name] = succeeded
            if len(results) == len(reads):
                finished.set()

        return done

    monitor._retry_at = 0  # The pause between brief connections isn't under test.
    started = monotonic()
    started_attempt = monitor.async_read(
        address,
        probe,
        {name: (read, done_for(name)) for name, read in reads.items()},
    )
    if not check.result(f"{label}: attempt started", started_attempt):
        return
    try:
        async with asyncio.timeout(CONNECT_DEADLINE + READ_DEADLINE):
            await finished.wait()
    except TimeoutError:
        pass
    for name in reads:
        check.result(f"{label}: read {name}", results.get(name) is True)
    gone = await wait_until(lambda: monitor._task is None, 30)
    check.result(
        f"{label}: disconnected afterwards",
        gone is not None and monitor._unreleased is None,
        f"connection held {monotonic() - started:.1f}s in total",
    )


async def check_brief(
    check: Check, hass, meatnet: MeatNet, node_address, probe_address, probe: Probe
) -> None:
    """Phase 3: brief connections, as with predictions disabled."""
    print("\n== Brief revision connections ==", flush=True)
    monitor = RevisionMonitor(hass, meatnet)
    try:
        if node_address is not None:
            await brief(
                check,
                monitor,
                "brief via repeater",
                node_address,
                None,
                {
                    "repeater": lambda session: session.read_device_information(),
                    probe.serial_number_string: lambda session: (
                        session.read_probe_information(probe.serial_number)
                    ),
                },
            )
        if probe_address is not None:
            await brief(
                check,
                monitor,
                "brief direct",
                probe_address,
                probe,
                {
                    probe.serial_number_string: lambda session: (
                        session.read_device_information()
                    )
                },
            )
    finally:
        check.result("brief: released", await monitor.async_stop())


async def scan(check: Check, seconds: float):
    """Discover probes and repeaters."""
    meatnet = MeatNet(stale_timeout=3600)  # Nothing expires mid-check.
    devices: dict[str, BLEDevice] = {}
    direct: dict[int, str] = {}

    def discovered(device, advertisement):
        payload = advertisement.manufacturer_data.get(COMBUSTION_MANUFACTURER_ID)
        if payload is None:
            return
        devices[device.address] = device
        meatnet.handle_advertisement(device.address, payload, advertisement.rssi)
        parsed = parse_advertisement(payload)
        if (
            isinstance(parsed, ProbeAdvertisement)
            and parsed.has_probe
            and not parsed.is_repeated
        ):
            direct[parsed.serial_number] = device.address

    print(f"Scanning for {seconds:.0f}s...", flush=True)
    async with BleakScanner(detection_callback=discovered):
        await asyncio.sleep(seconds)
    for serial_number, probe in meatnet.probes.items():
        check.info(
            f"Probe {probe.serial_number_string}: "
            f"{'heard directly' if serial_number in direct else 'relayed only'}"
        )
    for address, node in meatnet.nodes.items():
        check.info(
            f"Repeater {node.product_type.name} {node.serial_number} at {address}, "
            f"rssi {node.rssi}, relaying "
            f"{sorted(f'{serial:08X}' for serial in node.relayed_probes)}"
        )
    return meatnet, devices, direct


async def run(args, check: Check) -> None:
    """Run the selected phases."""
    meatnet, devices, direct = await scan(check, args.scan_seconds)
    if args.serial:
        serial_number = int(args.serial, 16)
    elif len(meatnet.probes) == 1:
        serial_number = next(iter(meatnet.probes))
    elif len(direct) == 1:
        # The probe near this computer, which both phases can reach.
        serial_number = next(iter(direct))
    else:
        check.result("select probe", False, "found none or several; pass --serial")
        return
    probe = meatnet.probes.get(serial_number)
    if not check.result("probe found", probe is not None, f"{serial_number:08X}"):
        return
    probe_address = direct.get(serial_number)
    relaying = [
        node
        for node in meatnet.nodes.values()
        if args.repeater is None or node.address == args.repeater
        if serial_number in node.relayed_probes
    ]
    if not relaying:
        # A scan can miss a repeater's relayed advertisements; its connection reports every probe
        # on its network anyway, which the repeater phase confirms.
        relaying = [
            node
            for node in meatnet.nodes.values()
            if args.repeater is None or node.address == args.repeater
        ]
    node = max(relaying, key=lambda node: node.rssi or -200, default=None)
    node_address = node.address if node is not None else None
    check.result(
        "repeater found",
        node_address is not None or "repeater" in args.skip,
        str(node_address),
    )
    check.result(
        "probe heard directly",
        probe_address is not None or "direct" in args.skip,
        str(probe_address),
    )

    hass = FakeHass()
    restore: dict = {"captured": False, "target": None, "pending": False}
    with patch.object(
        prediction.bluetooth,
        "async_ble_device_from_address",
        lambda _hass, address, connectable: devices.get(address),
    ):
        if "repeater" not in args.skip and node_address is not None:
            await check_repeater(
                check, hass, meatnet, node_address, probe, args, restore
            )
        if "direct" not in args.skip and probe_address is not None:
            await check_direct(
                check, hass, meatnet, probe_address, probe, args, restore
            )
        if "brief" not in args.skip:
            await check_brief(
                check,
                hass,
                meatnet,
                node_address if "repeater" not in args.skip else None,
                probe_address if "direct" not in args.skip else None,
                probe,
            )
    if restore["pending"]:
        check.result(
            "original target restored",
            False,
            f"set {restore['target']:.1f} °C again in the Combustion app",
        )


def celsius(value: str) -> float:
    """Parse a removal target the probe accepts."""
    result = float(value)
    if not math.isfinite(result) or not 0 < result <= 102.3:
        raise argparse.ArgumentTypeError("must be between 0 and 102.3")
    return result


def main() -> int:
    """Run the check and print a summary."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--serial", help="probe serial printed on the probe (hex)")
    parser.add_argument("--repeater", metavar="ADDRESS", help="repeater to use")
    parser.add_argument(
        "--target", type=celsius, default=63.0, help="test target in Celsius"
    )
    parser.add_argument("--scan-seconds", type=float, default=10)
    parser.add_argument(
        "--skip",
        action="append",
        default=[],
        choices=[*PHASES, "reject"],
        help="leave out a phase, or the nonexistent-probe check",
    )
    parser.add_argument("--verbose", action="store_true", help="show debug logs")
    default = Path(tempfile.gettempdir()) / datetime.now(UTC).strftime(
        "combustion-hardware-check-%Y%m%dT%H%M%SZ.jsonl"
    )
    parser.add_argument("--output", type=Path, default=default)
    args = parser.parse_args()
    if args.serial:
        args.serial = args.serial.upper().removeprefix("0X")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    with args.output.open("x", encoding="utf-8") as report:
        check = Check(report)
        check.emit(
            "environment",
            combustion_ble=version("combustion_ble"),
            bleak=version("bleak"),
            target=args.target,
            skip=args.skip,
        )
        try:
            asyncio.run(run(args, check))
        except KeyboardInterrupt:
            check.result("interrupted", False)
        except Exception as err:  # noqa: BLE001 - reported in the summary
            check.result("unexpected error", False, f"{type(err).__name__}: {err}")
        print(
            f"\n{check.passes} passed, {len(check.failures)} failed"
            + (f": {', '.join(check.failures)}" if check.failures else ""),
            flush=True,
        )
        print(f"Report: {args.output.resolve()}", flush=True)
    return 1 if check.failures else 0


if __name__ == "__main__":
    sys.exit(main())
