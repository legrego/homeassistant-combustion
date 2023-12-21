from datetime import datetime
from uuid import UUID

from ..device_manager import DeviceManager
from ..exceptions import DFUNotImplementedError


class Device:
    MIN_RSSI = -128
    STALE_TIMEOUT = 15.0

    class ConnectionState:
        DISCONNECTED = 'disconnected'
        CONNECTING = 'connecting'
        CONNECTED = 'connected'
        FAILED = 'failed'

    class DFUErrorMessage:
        def __init__(self, error, message):
            self.error = error
            self.message = message

    class DFUUploadProgress:
        def __init__(self, part, total_parts, progress):
            self.part = part
            self.total_parts = total_parts
            self.progress = progress

    def __init__(self, unique_identifier: str, ble_identifier: UUID =None, rssi=None):
        self.unique_identifier = unique_identifier
        self.ble_identifier = ble_identifier.bytes if ble_identifier else None
        self.rssi = rssi if rssi is not None else self.MIN_RSSI
        self.firmware_version = None
        self.hardware_revision = None
        self.sku = None
        self.manufacturing_lot = None
        self.connection_state = Device.ConnectionState.DISCONNECTED
        self.is_connectable = False
        self.maintaining_connection = False
        self.stale = False
        self.dfu_state = None
        self.dfu_error = None
        self.dfu_upload_progress = None
        self.last_update_time = datetime.now()
        # Placeholder for DFU service controller
        self.dfu_service_controller = None

    def update_connection_state(self, state: ConnectionState):
        self.connection_state = state

        if self.connection_state == Device.ConnectionState.DISCONNECTED:
            self.firmware_version = None

        if self.maintaining_connection and \
           (self.connection_state == Device.ConnectionState.DISCONNECTED or
            self.connection_state == Device.ConnectionState.FAILED):
            self.connect()

    def update_device_stale(self):
        self.stale = (datetime.now() - self.last_update_time).total_seconds() > self.STALE_TIMEOUT
        if self.stale:
            self.is_connectable = False

    def is_dfu_running(self) -> bool:
        if not self.dfu_state:
            return False

        raise DFUNotImplementedError()


    def dfu_complete():
        raise DFUNotImplementedError()


    def update_with_model_info(self, model_info: str):
        # Parse the SKU and lot number, which are delimited by a ':'
        parts = model_info.split(':')
        if len(parts) == 2:
            self.sku = parts[0]
            self.manufacturing_lot = parts[1]

    async def connect(self):
        self.maintaining_connection = True

        if self.connection_state != Device.ConnectionState.CONNECTED:
            await DeviceManager.shared.connect_to_device(self)

    async def disconnect(self):
        self.maintaining_connection = False
        await DeviceManager.shared.disconnect_from_device(self)

    def run_software_upgrade(self, dfu_file):
        raise DFUNotImplementedError()

    def dfu_state_did_change(self, state):
        raise DFUNotImplementedError()

    def dfu_error_did_occur(self, error, message):
        raise DFUNotImplementedError()

    def dfu_progress_did_change(self, part, total_parts, progress):
        raise DFUNotImplementedError()

    def log_with_level(self, level, message):
        raise DFUNotImplementedError()

    # Hashable implementation
    def __hash__(self):
        return hash(self.unique_identifier)

    def __eq__(self, other: 'Device'):
        return self.unique_identifier == other.unique_identifier if other else False
