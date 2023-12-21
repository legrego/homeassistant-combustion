"""Bluetooth Advertising Data."""

from enum import Enum
from typing import NamedTuple, Optional

from .battery_status_virtual_sensors import BatteryStatusVirtualSensors
from .hop_count import HopCount
from .mode_id import ModeId
from .probe_temperatures import ProbeTemperatures


class CombustionProductType(Enum):
    """Combustion Product Type."""

    UNKNOWN = 0x00
    PROBE = 0x01
    MEAT_NET_NODE = 0x02


class AdvertisingData(NamedTuple):
    """Bluetooth Advertising Data."""

    type: CombustionProductType
    serial_number: int
    temperatures: ProbeTemperatures
    mode_id: ModeId
    battery_status_virtual_sensors: BatteryStatusVirtualSensors
    hop_count: HopCount
    bit_string: str

    @staticmethod
    def from_data(data: bytes) -> Optional['AdvertisingData']:
        """Create instance from raw advertising data."""
        from bitstring import Bits

        if data is None or len(data) < 20:
            return None

        # Vendor ID
        vendor_id = int.from_bytes(data[0:2], byteorder='big')
        if vendor_id != 0x09C7:
            return None

        # Product type
        type_byte = data[2]
        product_type = CombustionProductType(type_byte)

        # Serial number
        serial_number = int.from_bytes(data[3:7], byteorder='little')

        # Temperatures
        temperatures = ProbeTemperatures.from_raw_data(data[7:20])

        # ModeId
        mode_id = ModeId.from_byte(data[20]) if len(data) >= 21 else ModeId.default_values()

        # Battery Status and Virtual Sensors
        battery_status_virtual_sensors = BatteryStatusVirtualSensors.from_byte(data[21]) if len(data) >= 22 else BatteryStatusVirtualSensors.default_values()

        # Hop Count
        hop_count = HopCount.from_network_info_byte(data[22]) if len(data) >= 23 else HopCount.default_values()

        # Bit String
        bit_string = Bits(data).bin

        return AdvertisingData(type=product_type, serial_number=serial_number, temperatures=temperatures, mode_id=mode_id, battery_status_virtual_sensors=battery_status_virtual_sensors, hop_count=hop_count, bit_string=bit_string)

    @staticmethod
    def from_bleak_data(data: bytes) -> Optional['AdvertisingData']:
        """Create instance from raw Bleak advertising data."""

        vendor_id = 0x09C7.to_bytes(2, 'big')
        return AdvertisingData.from_data(vendor_id + data)
