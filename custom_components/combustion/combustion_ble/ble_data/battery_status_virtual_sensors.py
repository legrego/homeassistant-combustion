"""Representation of the battery status & virtual sensors portion of the advertisement payload."""
from enum import Enum

from .virtual_sensors import (
    VirtualAmbientSensor,
    VirtualCoreSensor,
    VirtualSensors,
    VirtualSurfaceSensor,
)


class BatteryStatus(Enum):
    """Battery status enum."""

    OK = 0x00
    LOW = 0x01

    MASK = 0x1

class BatteryStatusVirtualSensors:
    """Representation of the battery status & virtual sensors portion of the advertisement payload."""

    def __init__(self, battery_status: BatteryStatus, virtual_sensors: VirtualSensors):
        """Initialize."""
        self.battery_status = battery_status
        self.virtual_sensors = virtual_sensors

    @staticmethod
    def from_byte(byte):
        """Create instance from raw byte."""
        raw_status = byte & BatteryStatus.MASK.value
        battery = BatteryStatus(raw_status)
        virtual_sensors = VirtualSensors.from_byte(byte >> 1)
        return BatteryStatusVirtualSensors(battery, virtual_sensors)

    @staticmethod
    def default_values():
        """Generate default values."""
        return BatteryStatusVirtualSensors(BatteryStatus.OK, VirtualSensors(VirtualCoreSensor.T1, VirtualSurfaceSensor.T4, VirtualAmbientSensor.T5))

