"""Parser for Combustion BLE advertisements."""
from __future__ import annotations

from home_assistant_bluetooth import BluetoothServiceInfoBleak
from sensor_state_data import description

from custom_components.combustion.const import BT_MANUFACTURER_ID, LOGGER

from .advertising_data import AdvertisingData
from .battery_status_virtual_sensors import BatteryStatus
from .mode_id import ProbeMode

MODE_SENSOR_DESCRIPTION = description.BaseSensorDescription(
    device_class="enum",
    native_unit_of_measurement=None,
)

# Serial Number value indicating 'No Probe'
INVALID_PROBE_SERIAL_NUMBER = 0

class CombustionProbeData:
    """Data for Combustion Probes."""

    def __init__(self, advertising_data: AdvertisingData, rssi: int, address: str) -> None:
        """Initialize the class."""
        self.advertising_data = advertising_data
        self._rssi = rssi
        self._address = address

    @property
    def valid(self) -> bool:
        """Determine if the probe data is valid.

        Probe data from a Meatnet repeater will sometimes arrive with an invalid serial number.
        This indicates the repeater is not connected to an actual probe.
        """
        return self.advertising_data.serial_number != INVALID_PROBE_SERIAL_NUMBER

    @property
    def address(self) -> str:
        """The address of the device which sent the advertising payload.

        IMPORTANT: This might not be the actual probe where the measurement happened. The address might be from a Meatnet repeater.
        """
        return self._address

    @property
    def device_type(self) -> str:
        """Type of device which sent the advertising payload.

        IMPORTANT: This might not be the actual probe where the measurement happened. The type might be from a Meatnet repeater.
        """

        return self.advertising_data.type.name


    @property
    def rssi(self) -> str:
        """Signal strength."""
        return self._rssi

    @property
    def serial_number(self) -> str | None:
        """Serial number of the predictive probe."""
        if self.advertising_data.serial_number == INVALID_PROBE_SERIAL_NUMBER:
            return None
        return hex(self.advertising_data.serial_number)[2:]

    @property
    def probe_id(self) -> int:
        """Probe ID from the Meatnet."""
        return self.advertising_data.mode_id.id.value + 1

    @property
    def mode(self) -> ProbeMode:
        """Probe Mode (instant, normal, etc.)."""
        return self.advertising_data.mode_id.mode

    @property
    def battery_ok(self) -> bool:
        """Battery state."""
        return self.advertising_data.battery_status_virtual_sensors.battery_status == BatteryStatus.OK

    @property
    def temperature_data(self) -> list[float]:
        """Temperature data, ordered by thermistor.

        First entry is the tip, last entry is the handle.
        """
        return self.advertising_data.temperatures.values

    @property
    def core_sensor(self) -> tuple[int, float]:
        """Core sensor tuple (probe id, temperature)."""
        temps = self.temperature_data
        virtual_sensors = self.advertising_data.battery_status_virtual_sensors.virtual_sensors
        temperature = virtual_sensors.virtual_core.temperature_from(temps)
        sensor_number = virtual_sensors.virtual_core.sensor_number()
        return (sensor_number, temperature)

    @property
    def ambient_sensor(self) -> tuple[int, float]:
        """Ambient sensor tuple (probe id, temperature)."""
        temps = self.temperature_data
        virtual_sensors = self.advertising_data.battery_status_virtual_sensors.virtual_sensors
        temperature = virtual_sensors.virtual_ambient.temperature_from(temps)
        sensor_number = virtual_sensors.virtual_ambient.sensor_number()

        return (sensor_number, temperature)

    @property
    def surface_sensor(self) -> tuple[int, float]:
        """Surface sensor tuple (probe id, temperature)."""
        temps = self.temperature_data
        virtual_sensors = self.advertising_data.battery_status_virtual_sensors.virtual_sensors
        temperature = virtual_sensors.virtual_surface.temperature_from(temps)
        sensor_number = virtual_sensors.virtual_surface.sensor_number()

        return (sensor_number, temperature)

    def to_dict(self) -> dict:
        """Convert CombustionProbeData instance to a dictionary."""
        data = {
            'valid': self.valid,
            'address': self.address,
            'rssi': self.rssi,
            'serial_number': self.serial_number,
            'probe_id': self.probe_id,
            'mode': self.mode,
            'battery_ok': self.battery_ok,
            'temperature_data': self.temperature_data,
            'core_sensor': self.core_sensor,
            'ambient_sensor': self.ambient_sensor,
            'surface_sensor': self.surface_sensor
        }
        return data


    @staticmethod
    def from_advertisement(service_info: BluetoothServiceInfoBleak):
        """Create instance from BT advertisement data."""
        LOGGER.debug("Parsing combustion BLE advertisement data: %s", service_info.as_dict())

        vendor_id = 0x09C7.to_bytes(2, 'big')
        data = vendor_id + service_info.manufacturer_data[BT_MANUFACTURER_ID]
        advertising_data = AdvertisingData.from_data(data)

        return CombustionProbeData(advertising_data, service_info.rssi, service_info.address)

