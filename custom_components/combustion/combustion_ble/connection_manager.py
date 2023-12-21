import asyncio
from datetime import datetime

from .device_manager import DeviceManager
from .devices.probe import Probe


class ConnectionManager:
    def __init__(self):
        self.meat_net_enabled = False
        self.dfu_mode_enabled = False
        self.connection_timers = {}
        self.last_status_update: dict[str, datetime] = {}
        self.PROBE_STATUS_STALE_TIMEOUT = 10.0

    async def received_probe_advertising(self, probe: Probe):
        if probe is None:
            return

        probe_status_stale = True

        if probe.serial_number_string in self.last_status_update:
            last_update_time = self.last_status_update[probe.serial_number_string]
            probe_status_stale = (datetime.now() - last_update_time).total_seconds() > self.PROBE_STATUS_STALE_TIMEOUT

        if self.dfu_mode_enabled:
            await probe.connect()
        elif self.meat_net_enabled and probe_status_stale and probe.connection_state != 'connected' and probe.serial_number_string not in self.connection_timers:
            self.connection_timers[probe.serial_number_string] = asyncio.create_task(self.connect_probe_after_delay(probe))

    async def connect_probe_after_delay(self, probe: Probe):
        await asyncio.sleep(3)
        updated_probe = self.get_probe_with_serial(probe.serial_number_string)
        if updated_probe:
            await updated_probe.connect()
        del self.connection_timers[probe.serial_number_string]

    async def received_probe_advertising_from_node(self, probe: Probe, node):
        if self.meat_net_enabled:
            await node.connect()

    async def received_status_for(self, probe: Probe, direct_connection: bool):
        self.last_status_update[probe.serial_number_string] = datetime.now()

        if not direct_connection and self.meat_net_enabled and not self.dfu_mode_enabled:
            updated_probe = self.get_probe_with_serial(probe.serial_number_string)
            if updated_probe and updated_probe.connection_state == 'connected':
                await updated_probe.disconnect()

    def get_probe_with_serial(self, serial: str) -> Probe | None:
        probes = DeviceManager.shared.get_probes()
        return next((probe for probe in probes if probe.serial_number_string == serial), None)

