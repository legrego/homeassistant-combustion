"""Decide when to read each device's firmware and hardware revisions, and through what.

Advertisements don't carry revisions, so reading them takes a connection. Devices are often only
online while they are in use, so each is read soon after it is first heard while online, then
again once a day while it stays online. A device that has been offline for longer is read as soon
as it is heard again.

Reads use a running prediction connection when one covers the device. Otherwise a brief connection
is made only for the read: to a MeatNet node that relays the probe, which reads every probe it
relays at once, or else to the probe directly. A route that fails, for example a repeater whose
connection slots are full, is passed over for the next one; once every route has failed, the
device waits before a fresh round. Brief connections don't compete with prediction connections,
so with predictions enabled they are only made to gauges, while a repeater session covers the
probes.
"""

from __future__ import annotations

from collections.abc import Coroutine, Mapping
from time import monotonic
from typing import Any

from combustion_ble import ConnectionPlan, Gauge, MeatNet, Node, NodeSession, Probe
from homeassistant.core import HomeAssistant, callback

from .prediction import (
    PredictionMonitor,
    Read,
    ReadDone,
    RepeaterMonitor,
    RevisionMonitor,
    Session,
)

type RevisionDevice = Probe | Gauge

REFRESH_INTERVAL = 24 * 60 * 60
"""Seconds between reads of an online device, whose revisions only change with a firmware update."""

RETRY_INTERVAL = 15 * 60
"""Seconds to wait after every route to a device has failed before trying it again."""

type Route = tuple[str, Node | None]
"""Address for a brief connection: a node, or the probe itself when there is no node."""


def _read_own(session: Session) -> Coroutine[Any, Any, None]:
    return session.read_device_information()


def _read_probe(serial_number: int) -> Read:
    def read(session: Session) -> Coroutine[Any, Any, None]:
        assert isinstance(session, NodeSession)
        return session.read_probe_information(serial_number)

    return read


def _name(device: RevisionDevice) -> str:
    if isinstance(device, Probe):
        return device.serial_number_string
    return device.serial_number


class RevisionScheduler:
    """Read each device's revisions when it is due, through the best available connection."""

    def __init__(self, hass: HomeAssistant, meatnet: MeatNet) -> None:
        """Initialize."""
        self._meatnet = meatnet
        self._monitor = RevisionMonitor(hass, meatnet)
        self._results: dict[RevisionDevice, tuple[float, bool]] = {}
        """When each device was last read, and whether the read succeeded."""
        self._pending: set[RevisionDevice] = set()
        """Devices with a read queued or running."""
        self._failed_routes: dict[RevisionDevice, set[str]] = {}
        """Addresses whose brief read of a device failed during the current round."""
        self._stopped = False

    def _due(self, device: RevisionDevice, now: float) -> bool:
        if not device.available or device in self._pending:
            return False
        if (result := self._results.get(device)) is None:
            return True
        read_at, succeeded = result
        return now - read_at >= (REFRESH_INTERVAL if succeeded else RETRY_INTERVAL)

    @callback
    def async_tick(
        self,
        *,
        repeater: RepeaterMonitor | None,
        direct: Mapping[int, PredictionMonitor],
        plan: ConnectionPlan | None,
    ) -> None:
        """Start reads for devices that are due.

        ``repeater`` is ``None`` when predictions are disabled; ``plan`` is the current
        prediction connection plan.
        """
        if self._stopped:
            return
        self._monitor.async_retry_cleanup()
        now = monotonic()
        due: list[RevisionDevice] = [
            device
            for device in (
                *self._meatnet.probes.values(),
                *self._meatnet.gauges.values(),
            )
            if self._due(device, now)
        ]
        if not due:
            return
        uncovered = [
            device
            for device in due
            if not self._async_read_in_session(device, repeater, direct, plan)
        ]
        if repeater is not None:
            # Predictions are enabled. Until a repeater session runs, the planner may connect to
            # any node; after that, prediction connections cover every probe they can reach.
            if repeater.session is None:
                return
            uncovered = [device for device in uncovered if isinstance(device, Gauge)]
        if uncovered and self._monitor.can_connect:
            self._async_read_briefly(uncovered, now)

    @callback
    def _async_read_in_session(
        self,
        device: RevisionDevice,
        repeater: RepeaterMonitor | None,
        direct: Mapping[int, PredictionMonitor],
        plan: ConnectionPlan | None,
    ) -> bool:
        """Queue a read on a running prediction session. Return whether one covers the device."""
        if repeater is not None and repeater.session is not None:
            if (
                isinstance(device, Probe)
                and plan is not None
                and device.serial_number in plan.release
            ):
                return self._async_queue(
                    repeater, device, _read_probe(device.serial_number)
                )
            if isinstance(device, Gauge) and repeater.address == device.address:
                return self._async_queue(repeater, device, _read_own)
        if isinstance(device, Probe) and (monitor := direct.get(device.serial_number)):
            return self._async_queue(monitor, device, _read_own)
        return False

    @callback
    def _async_queue(
        self,
        monitor: RepeaterMonitor | PredictionMonitor,
        device: RevisionDevice,
        read: Read,
    ) -> bool:
        if monitor.async_request_read(_name(device), read, self._done(device)):
            return True
        self._pending.discard(device)
        return False

    def _done(self, device: RevisionDevice, route: str | None = None) -> ReadDone:
        """Mark a read pending, and return the callback that records its result.

        ``route`` is the address of a brief connection. When it fails and the device has another
        route, that route is tried next instead of waiting.
        """
        self._pending.add(device)

        @callback
        def done(succeeded: bool | None) -> None:
            self._pending.discard(device)
            if succeeded is None:
                return
            if succeeded:
                self._failed_routes.pop(device, None)
            elif route is not None:
                failed = self._failed_routes.setdefault(device, set())
                failed.add(route)
                if any(address not in failed for address, _ in self._routes(device)):
                    return
                # Every route failed: wait, then start a fresh round.
                del self._failed_routes[device]
            self._results[device] = (monotonic(), succeeded)

        return done

    @callback
    def _async_read_briefly(self, devices: list[RevisionDevice], now: float) -> None:
        """Connect only to read revisions, starting with the first device.

        A node's connection also reads the other due devices it can reach.
        """
        first = devices[0]
        failed = self._failed_routes.get(first, set())
        route = next(
            (route for route in self._routes(first) if route[0] not in failed), None
        )
        if route is None:
            self._failed_routes.pop(first, None)
            self._results[first] = (now, False)
            return
        address, node = route
        if node is None:
            assert isinstance(first, Probe)
            self._monitor.async_read(
                address, first, {_name(first): (_read_own, self._done(first, address))}
            )
            return
        due = set(devices)
        reads: dict[str, tuple[Read, ReadDone]] = {}
        for gauge in self._meatnet.gauges.values():
            if gauge.address == address and gauge in due:
                reads[_name(gauge)] = (_read_own, self._done(gauge, address))
        for serial_number in node.relayed_probes:
            probe = self._meatnet.probes.get(serial_number)
            if probe is not None and probe in due:
                reads[_name(probe)] = (
                    _read_probe(serial_number),
                    self._done(probe, address),
                )
        self._monitor.async_read(address, None, reads)

    def _routes(self, device: RevisionDevice) -> list[Route]:
        """List the brief connections that can read a device, best first.

        A gauge is read through its own node. A probe is read through the nodes relaying it,
        strongest signal first, then directly when it has been heard directly.
        """
        if isinstance(device, Gauge):
            node = self._meatnet.nodes.get(device.address)
            return [] if node is None else [(device.address, node)]
        nodes = sorted(
            (
                node
                for node in self._meatnet.nodes.values()
                if node.available and device.serial_number in node.relayed_probes
            ),
            key=lambda node: node.rssi if node.rssi is not None else -999,
            reverse=True,
        )
        routes: list[Route] = [(node.address, node) for node in nodes]
        if device.address is not None:
            routes.append((device.address, None))
        return routes

    async def async_stop(self) -> None:
        """Stop reading, and release any brief connection."""
        self._stopped = True
        await self._monitor.async_stop()
