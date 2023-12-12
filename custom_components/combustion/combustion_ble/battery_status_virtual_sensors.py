"""Representation of the battery status & virtual sensors portion of the advertisement payload."""
from enum import Enum


class BatteryStatus(Enum):
    """Battery status enum."""

    OK = 0x00
    LOW = 0x01

    MASK = 0x1

class VirtualCoreSensor(Enum):
    """Virtual Core Sensor."""

    T1 = 0x00
    T2 = 0x01
    T3 = 0x02
    T4 = 0x03
    T5 = 0x04
    T6 = 0x05

    MASK = 0x7

    def sensor_number(self):
        """Display value (1-based) for this sensor."""
        return int(self.value) + 1

    def temperature_from(self, temperatures):
        """Get temperature for virtual sensor."""
        return temperatures[int(self.value)]

class VirtualSurfaceSensor(Enum):
    """Virtual Surface Sensor."""

    T4 = 0x00
    T5 = 0x01
    T6 = 0x02
    T7 = 0x03

    MASK = 0x3

    def sensor_number(self):
        """Display value (1-based) for this sensor."""
        return int(self.value) + 4

    def temperature_from(self, temperatures):
        """Get temperature for virtual sensor."""
        surface_sensor_number = int(self.value) + 3
        return temperatures[surface_sensor_number]

class VirtualAmbientSensor(Enum):
    """Virtual Ambient Sensor."""

    T5 = 0x00
    T6 = 0x01
    T7 = 0x02
    T8 = 0x03

    MASK = 0x3

    def sensor_number(self):
        """Display value (1-based) for this sensor."""
        return int(self.value) + 5

    def temperature_from(self, temperatures):
        """Get temperature for virtual sensor."""
        ambient_sensor_number = int(self.value) + 4
        return temperatures[ambient_sensor_number]

class VirtualSensors:
    """Collection of all virtual sensors."""

    def __init__(self, virtual_core: VirtualCoreSensor, virtual_surface: VirtualSurfaceSensor, virtual_ambient: VirtualAmbientSensor):
        """Initialize."""
        self.virtual_core = virtual_core
        self.virtual_surface = virtual_surface
        self.virtual_ambient = virtual_ambient

    @staticmethod
    def from_byte(byte):
        """Create instances from byte."""
        raw_virtual_core = byte & VirtualCoreSensor.MASK.value
        try:
            virtual_core = VirtualCoreSensor(raw_virtual_core)
        except ValueError:
            virtual_core = VirtualCoreSensor.T1

        raw_virtual_surface = (byte >> 3) & VirtualSurfaceSensor.MASK.value
        try:
            virtual_surface = VirtualSurfaceSensor(raw_virtual_surface)
        except ValueError:
            virtual_surface = VirtualSurfaceSensor.T4

        raw_virtual_ambient = (byte >> 5) & VirtualAmbientSensor.MASK.value
        try:
            virtual_ambient = VirtualAmbientSensor(raw_virtual_ambient)
        except ValueError:
            virtual_ambient = VirtualAmbientSensor.T5

        return VirtualSensors(virtual_core, virtual_surface, virtual_ambient)

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

