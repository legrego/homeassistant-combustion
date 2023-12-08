"""Bluetooth test utilities."""
from typing import Any
from unittest.mock import patch

from bleak.backends.scanner import AdvertisementData, BLEDevice
from homeassistant.components.bluetooth.models import BluetoothServiceInfoBleak

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
