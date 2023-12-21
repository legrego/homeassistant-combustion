from uuid import UUID

from ..ble_data.advertising_data import AdvertisingData
from ..dfu_manager import DFUDeviceType
from .device import Device
from .probe import Probe


class MeatNetNode(Device):
    def __init__(self, advertising: AdvertisingData, is_connectable: bool, rssi: int, identifier: UUID):
        super().__init__(uniqueIdentifier=str(identifier), bleIdentifier=identifier, RSSI=rssi)
        self.serial_number_string = None
        self.probes: dict[int, Probe] = {}
        self.dfu_type = DFUDeviceType.UNKNOWN
        self.updateWithAdvertising(advertising, is_connectable, rssi)

    def updateWithAdvertising(self, advertising: AdvertisingData, is_connectable: bool, rssi: int):
        self.rssi = rssi
        self.is_connectable = is_connectable

    def updateNetworkedProbe(self, probe: Probe):
        if probe is not None:
            self.probes[probe.serial_number] = probe

    def has_connection_to_probe(self, serial_number: int):
        return serial_number in self.probes

    def update_with_model_info(self, model_info: str):
        super().update_with_model_info(model_info)
        if "Timer" in model_info:
            self.dfu_type = 'display'
        elif "Charger" in model_info:
            self.dfu_type = 'charger'
