"""Parser for Combustion BLE advertisements."""
from __future__ import annotations

from bluetooth_data_tools import short_address
from bluetooth_sensor_state_data import BluetoothData
from home_assistant_bluetooth import BluetoothServiceInfo
from sensor_state_data import BinarySensorDeviceClass, SensorLibrary, description

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

class CombustionBluetoothDeviceData(BluetoothData):
    """Date update for ThermoPro Bluetooth devices."""

    def __init__(self) -> None:
        """Initialize the class."""
        super().__init__()

    def _start_update(self, service_info: BluetoothServiceInfo) -> None:
        """Update from BLE advertisement data."""
        LOGGER.debug("Parsing combustion BLE advertisement data: %s", service_info.name)

        vendor_id = 0x09C7
        vendor_id_bytes = vendor_id.to_bytes(2, 'big')
        data = service_info.manufacturer_data[BT_MANUFACTURER_ID]

        advertising_data = AdvertisingData.from_data(vendor_id_bytes + data)

        product_type = advertising_data.type.value
        serial_number = advertising_data.serial_number
        serial_number_hex = hex(serial_number)[2:]
        temperature_data = advertising_data.temperatures.values
        mode_id = advertising_data.mode_id
        battery_status_and_virtual_sensors = advertising_data.battery_status_virtual_sensors

        # For now, only support MeatNet Repeater Nodes (type: 2)
        # Ignore direct connections from the probes (type: 1)
        if product_type != 2:
            return

        meatnet_device_id = service_info.address

        is_repeater = serial_number == INVALID_PROBE_SERIAL_NUMBER
        device_description ='Meatnet Repeater' if is_repeater else 'Probe'
        device_id = meatnet_device_id if is_repeater else serial_number_hex

        device_title = f'{device_description} {short_address(service_info.address) if is_repeater else serial_number_hex}'

        self.set_device_type(device_description, device_id)
        self.set_title(device_title)
        self.set_device_name(device_title, device_id)
        self.set_precision(2)
        self.set_device_manufacturer("Combustion", device_id)
        changed_manufacturer_data = self.changed_manufacturer_data(service_info)

        is_instant_read = mode_id.mode == ProbeMode.instantRead

        if is_instant_read:
            ## Ignore instant readings.
            ## The mode seems to flap between instant and normal,
            ## and the instant reads probably aren't too useful for Home Assistant users.
            return
            self._instant_read_filter.add_reading(temperature_data[0])
            temperature_data[0] = self._instant_read_filter.values[0]

        if not changed_manufacturer_data or len(changed_manufacturer_data) > 1:
            # If len(changed_manufacturer_data) > 1 it means we switched
            # ble adapters so we do not know which data is the latest
            # and we need to wait for the next update.
            LOGGER.debug("Skipping update due to possible BLE adater change.")
            return

        last_id = list(changed_manufacturer_data)[-1]
        data = (
            int(last_id).to_bytes(2, byteorder="little")
            + changed_manufacturer_data[last_id]
        )

        ## TODO: understand what this means...
        if len(data) != 24:
            LOGGER.warn("Not proceeding because %s is not equal to the magic number 24", len(data))
            return

        if is_repeater:
            # Repeaters do not have their own temperature data.
            return

        probe_id = mode_id.id.value + 1

        # Update battery sensor
        self.update_predefined_binary_sensor(
            BinarySensorDeviceClass.BATTERY,
            key='probe_battery',
            device_id=device_id,
            native_value=battery_status_and_virtual_sensors.battery_status == BatteryStatus.OK
        )

        # Update mode sensor
        meatnet_mode_key = f'probe_{probe_id}_mode'
        probe_mode_key = f'probe_{serial_number_hex}_mode'
        match mode_id.mode:
            case ProbeMode.normal:
                mode = 'normal'
            case ProbeMode.instantRead:
                mode = 'instant_read'
            case ProbeMode.error:
                mode = 'error'
            case ProbeMode.reserved:
                mode = 'reserved'
            case _:
                mode = 'unknown'
        self.update_predefined_sensor(MODE_SENSOR_DESCRIPTION, mode, key=meatnet_mode_key, device_id=meatnet_device_id)
        self.update_predefined_sensor(MODE_SENSOR_DESCRIPTION, mode, key=probe_mode_key, device_id=device_id)

        if not is_instant_read:
            ## Virtual sensors (core, ambient, surface) are registered to the meatnet here, as opposed to the individual probe devices.
            ## This is merely a design decision of our home assistant component. There is no consequence to changing this.

            # Update Core Sensor
            core_key = f'probe_{probe_id}_core'
            core_temperature = battery_status_and_virtual_sensors.virtual_sensors.virtual_core.temperature_from(temperature_data)
            self.update_predefined_sensor(SensorLibrary.TEMPERATURE__CELSIUS, core_temperature, key=core_key, device_id=meatnet_device_id)

            # Update Ambient Sensor
            ambient_key = f'probe_{probe_id}_ambient'
            ambient_temperature = battery_status_and_virtual_sensors.virtual_sensors.virtual_ambient.temperature_from(temperature_data)
            self.update_predefined_sensor(SensorLibrary.TEMPERATURE__CELSIUS, ambient_temperature, key=ambient_key, device_id=meatnet_device_id)

            # Update Surface Sensor
            surface_key = f'probe_{probe_id}_surface'
            surface_temperature = battery_status_and_virtual_sensors.virtual_sensors.virtual_surface.temperature_from(temperature_data)
            self.update_predefined_sensor(SensorLibrary.TEMPERATURE__CELSIUS, surface_temperature, key=surface_key, device_id=meatnet_device_id)

        # Update individual thermistor sensors
        for i in range(len(temperature_data)):
            thermistor = i + 1
            key = f'probe_{serial_number_hex}_temp_{thermistor}'
            temp = temperature_data[i]

            self.update_predefined_sensor(SensorLibrary.TEMPERATURE__CELSIUS, temp, key=key, device_id=device_id)
