import asyncio
from datetime import datetime
from uuid import UUID

from .ble_manager import BleManager, BleManagerDelegate
from .connection_manager import ConnectionManager
from .devices.device import Device
from .devices.meat_net_node import MeatNetNode
from .devices.probe import Probe
from .exceptions import DFUNotImplementedError
from .message_handlers import MessageHandlers, MessageSentHandler
from .uart.log_request import LogRequest
from .uart.meatnet.node_read_logs_request import NodeReadLogsRequest
from .uart.meatnet.node_read_session_info_request import NodeReadSessionInfoRequest
from .uart.session_info import SessionInfoRequest


class DeviceManager(BleManagerDelegate):
    MINIMUM_PREDICTION_SETPOINT_CELSIUS = 0.0
    MAXIMUM_PREDICTION_SETPOINT_CELSIUS = 100.0
    INVALID_PROBE_SERIAL_NUMBER = 0

    shared = None

    def __init__(self):
        self.devices: dict[str, Device] = {}
        self.connection_manager = ConnectionManager()
        self.message_handlers = MessageHandlers()
        DeviceManager.shared = self
        BleManager.shared.delegate = self
        asyncio.create_task(self.start_timers())

    async def start_timers(self):
        while True:
            self.update_device_stale_status()
            self.check_message_timeouts()
            await asyncio.sleep(1)

    def update_device_stale_status(self):
        # Update the stale status of devices
        for key, device in self.devices.items():
            device.update_device_stale()  # Placeholder for the device update method

    def check_message_timeouts(self, message_handlers: dict[str, MessageSentHandler]):
        # Check for message timeouts
        current_time = datetime.now()
        keys_to_remove: list[str] = []

        for key in message_handlers:
            message_handler = message_handlers[key]
            message_timeout_elapsed = (current_time - message_handler.time_sent).total_seconds() > MessageHandlers.MESSAGE_TIMEOUT_SECONDS
            if message_timeout_elapsed:
                if message_handler.success_handler:
                    message_handler.success_handler(False)
                if message_handler.read_over_temperature_completion_handler:
                    message_handler.read_over_temperature_completion_handler(False, False)

                keys_to_remove.append(key)

        # Remove keys that timed out
        for key in keys_to_remove:
            del message_handlers[key]


    def add_simulated_probe(self):
        # Placeholder for adding a simulated probe
        pass

    def init_bluetooth(self):
        # Placeholder for initializing Bluetooth
        pass

    def enable_meatnet(self):
        self.connection_manager.meat_net_enabled = True

    def enable_dfu_mode(self, enable):
        self.connection_manager.dfu_mode_enabled = enable

    def add_device(self, device: Device):
        self.devices[device.unique_identifier] = device

    def clear_device(self, device: Device):
        if device.unique_identifier in self.devices:
            del self.devices[device.unique_identifier]

    def get_probes(self):
        return [device for device in self.devices.values() if isinstance(device, Probe)]

    def get_meatnet_nodes(self):
        if self.connection_manager.meat_net_enabled:
            return [device for device in self.devices.values() if isinstance(device, MeatNetNode)]
        else:
            return []

    def get_nearest_probe(self):
        probes = self.get_probes()
        return max(probes, key=lambda probe: probe.rssi, default=None)

    def get_devices(self):
        return list(self.devices.values())

    def get_nearest_device(self):
        return max(self.get_devices(), key=lambda device: device.rssi, default=None)

    def get_best_node_for_probe(self, serial_number):
        # Placeholder for finding the best node for a probe
        pass

    def get_best_route_to_probe(self, serial_number) -> Device:
        found_device: Device = None
        probe = self.find_probe_by_serial_number(serial_number)
        if probe and probe.connection_state == Probe.ConnectionState.CONNECTED:
            found_device = probe
        else:
            found_device = self.get_best_node_for_probe(serial_number)

        return found_device

    def find_probe_by_serial_number(self, serial_number) -> Probe:
        device = self.devices.get(serial_number)
        if device and isinstance(device, Probe):
            return device

        return None

    async def connect_to_device(self, device: Device):
        # Placeholder for async BLE connection handling
        # if let _ = device as? SimulatedProbe, let bleIdentifier = device.bleIdentifier, let uuid = UUID(uuidString: bleIdentifier) {
        #     // If this device is a Simulated Probe, use a simulated connection.
        #     didConnectTo(identifier: uuid)
        # }
        #elif..
        if device.ble_identifier:
            # If this device has a BLE identifier (advertisements are directly detected rather than through MeatNet), attempt to connect to it.
            # TODO implement bluetooth manager connect
            await BleManager.shared.connect(device.ble_identifier)

        # TODO double-check this implementation is complete.

    async def disconnect_from_device(self, device: Device):

        # if let _ = device as? SimulatedProbe, let bleIdentifier = device.bleIdentifier, let uuid = UUID(uuidString: bleIdentifier) {
        #     // If this device is a Simulated Probe, use a simulated disconnect.
        #     didDisconnectFrom(identifier: uuid)
        # }
        # elif..
        if device.bleIdentifier:
            # If this device has a BLE identifier (advertisements are directly detected rather than through MeatNet),
            # attempt to disconnect from it.
            await BleManager.shared.disconnect(device.ble_identifier)

        # TODO double-check this implementation is complete.


    async def request_logs_from(self, device: Device, min_sequence: int, max_sequence: int):
        if isinstance(device, Probe):
            target_device = self.get_best_route_to_probe(device.serial_number)
            if isinstance(target_device, Probe) and target_device.ble_identifier:
                # Request logs directly from Probe
                request = LogRequest(min_sequence = min_sequence, max_sequence=max_sequence)
                await BleManager.shared.send_request(target_device.ble_identifier, request)
            elif isinstance(target_device, MeatNetNode) and target_device.ble_identifier:
                # If the best route is through a Node, send it that way.
                request = NodeReadLogsRequest(serial_number = device.serial_number, min_sequence = min_sequence, max_sequence = max_sequence)
                await BleManager.shared.send_request(identifier=target_device.ble_identifier, request=request)

    def set_probe_id(self, device, id, completion_handler):
        # Placeholder for setting probe ID
        raise NotImplementedError()
        pass

    def set_probe_color(self, device, color, completion_handler):
        # Placeholder for setting probe color
        raise NotImplementedError()
        pass

    def set_removal_prediction(self, device, removal_temperature_c, completion_handler):
        # Placeholder for setting removal prediction
        raise NotImplementedError()
        pass

    def cancel_prediction(self, device, completion_handler):
        # Placeholder for canceling prediction
        raise NotImplementedError()
        pass

    async def read_session_info(self, probe: Probe):
        # Placeholder for reading session info from a probe
        target_device = self.get_best_route_to_probe(probe.serial_number)
        if isinstance(target_device, Probe) and target_device.ble_identifier:
            # If the best route is directly to the Probe, send it that way.
            request = SessionInfoRequest()
            await BleManager.shared.send_request(identifier=target_device.ble_identifier, request=request)
        elif isinstance(target_device, MeatNetNode) and target_device.ble_identifier:
            request = NodeReadSessionInfoRequest(serial_number = probe.serial_number)
            await BleManager.shared.send_request(identifier=target_device.ble_identifier, request= request)

    def read_firmware_version(self, probe):
        # Placeholder for reading firmware version of a probe
        pass

    def read_hardware_version(self, probe):
        # Placeholder for reading hardware version of a probe
        pass

    def read_model_info_for_probe(self, probe):
        # Placeholder for reading model info of a probe
        pass

    def read_model_info_for_node(self, node):
        # Placeholder for reading model info of a MeatNetNode
        pass

    def read_over_temperature_flag(self, device, completion_handler):
        # Placeholder for reading over temperature flag of a device
        pass

    def restart_failed_upgrades_with(self, dfu_files):
        raise DFUNotImplementedError()

    def find_device_by_ble_identifier(self, identifier: UUID) -> Device | None:
        found_device: Device | None = None
        device = self.devices[identifier.hex]
        if device:
            # This was a MeatNet Node as it was stored by its BLE UUID.
            found_device = device
        else:
            # Search through Devices to see if any Probes have a matching BLE identifier.
            for device in self.devices.values():
                if device.ble_identifier and device.ble_identifier.hex == identifier.hex:
                    found_device = device
                    break
        return found_device

    # Delegate methods
    def did_connect_to(self, identifier):
        device = self.find_device_by_ble_identifier(identifier)
        if not device:
            return
        device.update_connection_state(Device.ConnectionState.CONNECTED)

    def did_fail_to_connect_to(self, identifier):
        # Placeholder for handling BLE failed connect callback
        pass

    def did_disconnect_from(self, identifier):
        # Placeholder for handling BLE disconnect callback
        pass

    def update_device_with_status(self, identifier, status):
        # Placeholder for handling status update from a device
        pass

    async def connect_to_device(self, device):
        # Placeholder for async BLE connection handling
        pass

    async def disconnect_from_device(self, device):
        # Placeholder for async BLE disconnection handling
        pass

    # ... Implement other methods with placeholders as needed
    # ...

# Instantiate the DeviceManager singleton
DeviceManager.shared = DeviceManager()
