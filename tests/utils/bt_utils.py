"""Bluetooth test utilities."""
from typing import Any
from unittest.mock import patch

from bitstring import Bits
from bleak.backends.scanner import AdvertisementData, BLEDevice
from homeassistant.components.bluetooth import async_get_advertisement_callback
from homeassistant.components.bluetooth.models import BluetoothServiceInfoBleak
from homeassistant.core import HomeAssistant

from custom_components.combustion.combustion_ble.advertising_data import (
    CombustionProductType,
)
from custom_components.combustion.combustion_ble.mode_id import ProbeMode

ADVERTISEMENT_DATA_DEFAULTS = {
    "local_name": "",
    "manufacturer_data": {},
    "service_data": {},
    "service_uuids": [],
    "rssi": -127,
    "platform_data": ((),),
    "tx_power": -127,
}

BLE_DEVICE_DEFAULTS = {
    "name": None,
    "rssi": -127,
    "details": None,
}

def patch_async_ble_device_from_address(return_value: BluetoothServiceInfoBleak | None):
    """Patch async ble device from address to return a given value."""
    return patch(
        "homeassistant.components.bluetooth.async_ble_device_from_address",
        return_value=return_value,
    )

def generate_ble_device(
    address: str | None = None,
    name: str | None = None,
    details: Any | None = None,
    rssi: int | None = None,
    **kwargs: Any,
) -> BLEDevice:
    """Generate a BLEDevice with defaults."""
    new = kwargs.copy()
    if address is not None:
        new["address"] = address
    if name is not None:
        new["name"] = name
    if details is not None:
        new["details"] = details
    if rssi is not None:
        new["rssi"] = rssi
    for key, value in BLE_DEVICE_DEFAULTS.items():
        new.setdefault(key, value)
    return BLEDevice(**new)

def generate_advertisement_data(**kwargs: Any) -> AdvertisementData:
    """Generate advertisement data with defaults."""
    new = kwargs.copy()
    for key, value in ADVERTISEMENT_DATA_DEFAULTS.items():
        new.setdefault(key, value)
    return AdvertisementData(**new)


COMBUSTION_SERVICE_INFO = BluetoothServiceInfoBleak(
    name="cc:cc:cc:cc:cc:cc",
    address="cc:cc:cc:cc:cc:cc",
    device=generate_ble_device(
        address="cc:cc:cc:cc:cc:cc",
        name="Combustion",
    ),
    rssi=-61,
    manufacturer_data={2503: b'\x01t\x1b\x00\x10T\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01\xc0\x00\x00'},
    service_data={
    },
    service_uuids=[
        '0000fe59-0000-1000-8000-00805f9b34fb',
        '00000100-caab-3792-3d44-97ae51c1407a'
    ],
    source='B8:27:EB:EA:98:17',
    advertisement=generate_advertisement_data(
        manufacturer_data={2503: b'\x01t\x1b\x00\x10T\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01\xc0\x00\x00'},
        service_uuids=['0000fe59-0000-1000-8000-00805f9b34fb', '00000100-caab-3792-3d44-97ae51c1407a'],
    ),
    connectable=True,
    time=0,
)

def inject_bt_advertisement(hass: HomeAssistant, service_info: BluetoothServiceInfoBleak):
    """Inject a BT advertisement into HASS."""
    async_get_advertisement_callback(hass)(service_info)

def create_advertisement(combustion_bits):
    """Create a BT advertisement."""
    adv = generate_advertisement_data(
        manufacturer_data={2503: combustion_bits},
        service_uuids=['0000fe59-0000-1000-8000-00805f9b34fb', '00000100-caab-3792-3d44-97ae51c1407a'],
    )

    return BluetoothServiceInfoBleak(
        name="cc:cc:cc:cc:cc:cc",
        address="cc:cc:cc:cc:cc:cc",
        device=generate_ble_device(
            address="cc:cc:cc:cc:cc:cc",
            name="Combustion",
        ),
        rssi=-61,
        manufacturer_data=adv.manufacturer_data,
        service_data={
        },
        service_uuids=[
            '0000fe59-0000-1000-8000-00805f9b34fb',
            '00000100-caab-3792-3d44-97ae51c1407a'
        ],
        source='B8:27:EB:EA:98:17',
        advertisement=adv,
        connectable=True,
        time=0,
    )

def create_combustion_bits(
        probe_id: int = 1,
        mode: int = ProbeMode.normal.value,
        device_type: str = CombustionProductType.PROBE.name,
        serial_number: str = '10001ccc',
        temperature_data: list[float] = None,
        core_sensor_id: int = 1,
        ambient_sensor_id: int = 7,
        surface_sensor_id: int = 5,
        battery_ok: bool = True
    ):
    """Create a bit representation for use in a BT advertisement."""
    device_type = CombustionProductType[device_type].value.to_bytes(1)
    serial_number =  Bits(hex=f'0x{serial_number}')

    if not temperature_data:
        temperature_data = [20.0, 21.1, 22.2, 23.3, 24.4, 25.5, 26.6, 27.7]

    raw_temps = [(int((temp + 20.0) / 0.05) & 0x1FFF) for temp in temperature_data]
    # Now pack these 13-bit values into bytes
    bytes_ = bytearray(13)  # Initialize a byte array of 13 bytes (104 bits)

    bytes_[0]  = ((raw_temps[7] >> 5) & 0xFF)
    bytes_[1]  = ((raw_temps[7] & 0x1F) << 3) | ((raw_temps[6] >> 10) & 0x07)
    bytes_[2]  = ((raw_temps[6] >> 2) & 0xFF)
    bytes_[3]  = ((raw_temps[6] & 0x03) << 6) | ((raw_temps[5] >> 7) & 0x3F)
    bytes_[4]  = ((raw_temps[5] & 0x7F) << 1) | ((raw_temps[4] >> 12) & 0x01)
    bytes_[5]  = ((raw_temps[4] >> 4) & 0xFF)
    bytes_[6]  = ((raw_temps[4] & 0x0F) << 4) | ((raw_temps[3] >> 9) & 0x0F)
    bytes_[7]  = ((raw_temps[3] >> 1) & 0xFF)
    bytes_[8]  = ((raw_temps[3] & 0x01) << 7) | ((raw_temps[2] >> 6) & 0x7F)
    bytes_[9]  = ((raw_temps[2] & 0x3F) << 2) | ((raw_temps[1] >> 11) & 0x03)
    bytes_[10] = ((raw_temps[1] >> 3) & 0xFF)
    bytes_[11] = ((raw_temps[1] & 0x07) << 5) | ((raw_temps[0] >> 8) & 0x1F)
    bytes_[12] = (raw_temps[0] & 0xFF)

    bytes_.reverse()

    temperatures = Bits(bytes(bytes_))

    # Mode id
    id_value = probe_id - 1
    color_value = 0
    mode_value = mode
    # Combine these values into a byte
    mode_id  = Bits(((id_value << 5) | (color_value << 2) | mode_value).to_bytes())

    # Virtual Sensors
    core_value = core_sensor_id - 1
    surface_value = surface_sensor_id - 1
    ambient_value = ambient_sensor_id - 1

    # Combine these values into a byte
    virtual_byte = (core_value & 0x7) \
            | ((surface_value & 0x3) << 3) \
            | ((ambient_value & 0x3) << 5)

    # Battery Status
    status_value = 1 if battery_ok else 0

    battery_virtual_byte = Bits(((status_value & 0x1) | (virtual_byte << 1)).to_bytes())

    network_info_byte = Bits(int.to_bytes(0))

    return  (device_type + serial_number + temperatures + mode_id + battery_virtual_byte + network_info_byte).tobytes()



# -420 -> 7400


# attempt
# 00011001011010001100101110000110010111000011001011010001100110110000110011010000011001101100001100111001
# 00101101110000110110010110111000100011001001011001100001001100110110100010000110110011011100100000011001


# actual
# 00101101110000110110010110111000100011001001011001100001001100110110100010000110110011011100100000011001
