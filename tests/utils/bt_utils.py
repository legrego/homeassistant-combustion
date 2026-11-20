"""Bluetooth test utilities."""

from __future__ import annotations

from collections.abc import Sequence

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_get_advertisement_callback,
)
from homeassistant.core import HomeAssistant

COMBUSTION_MANUFACTURER_ID = 2503
PROBE_ADDRESS = "CC:CC:CC:CC:CC:01"
NODE_ADDRESS = "CC:CC:CC:CC:CC:02"
GAUGE_ADDRESS = "CC:CC:CC:CC:CC:03"
SOURCE = "B8:27:EB:EA:98:17"

PROBE_SERIAL = 0x10001CCC
ROOM_TEMPERATURES = (20.0, 21.1, 22.2, 23.3, 24.4, 25.5, 26.6, 27.7)


def _encode_temperatures(values: Sequence[float]) -> bytes:
    raw = 0
    for index, value in enumerate(values):
        raw |= (round((value + 20.0) / 0.05) & 0x1FFF) << (13 * index)
    return raw.to_bytes(13, "little")


def probe_payload(
    *,
    serial_number: int = PROBE_SERIAL,
    temperatures: Sequence[float] = ROOM_TEMPERATURES,
    instant_read: float | None = None,
    probe_id: int = 1,
    low_battery: bool = False,
    core: int = 1,
    surface: int = 5,
    ambient: int = 7,
    hop: int | None = None,
) -> bytes:
    """Build probe advertising data.

    ``core``, ``surface``, and ``ambient`` are thermistor numbers (1-8). Passing ``hop`` (0 means
    one hop) makes this repeated data from a MeatNet node. Passing ``instant_read`` makes this an
    instant read advertisement.
    """
    data = bytearray([0x01 if hop is None else 0x02])
    data += serial_number.to_bytes(4, "little")
    if instant_read is None:
        data += _encode_temperatures(temperatures)
        mode = 0
    else:
        data += _encode_temperatures([instant_read] + [-20.0] * 7)
        mode = 1
    data.append(((probe_id - 1) << 5) | mode)
    virtual_sensors = (core - 1) | ((surface - 4) << 3) | ((ambient - 5) << 5)
    data.append((virtual_sensors << 1) | int(low_battery))
    data.append(0 if hop is None else hop << 6)
    data.append(0)  # overheating sensors
    data.append(0)  # preferences
    return bytes(data)


def gauge_payload(
    *,
    serial_number: str = "GG00000001",
    temperature: float | None = 150.0,
    low_battery: bool = False,
) -> bytes:
    """Build Giant Grill Gauge advertising data. ``temperature=None`` means no sensor is present."""
    data = bytearray([0x03])
    data += serial_number.encode("ascii").ljust(10, b"\x00")
    present = temperature is not None
    raw = round((temperature + 20.0) / 0.1) if present else 0
    data += raw.to_bytes(2, "little")
    data.append(int(present) | (int(low_battery) << 2))
    data.append(100)  # battery percentage
    data += bytes(4)  # high and low alarms
    data.append(0)  # preferences
    return bytes(data)


def node_payload(product_type: int = 0x05, serial_number: str = "CR10000001") -> bytes:
    """Build Display (4), Booster (5), or Engine (6) device information advertising data."""
    return (
        bytes([product_type])
        + serial_number.encode("ascii").ljust(10, b"\x00")
        + bytes(12)
    )


def service_info(
    payload: bytes,
    *,
    address: str = PROBE_ADDRESS,
    rssi: int = -61,
    connectable: bool = True,
    time: float = 0.0,
) -> BluetoothServiceInfoBleak:
    """Build a BluetoothServiceInfoBleak for a Combustion advertisement."""
    manufacturer_data = {COMBUSTION_MANUFACTURER_ID: payload}
    return BluetoothServiceInfoBleak(
        name="",
        address=address,
        rssi=rssi,
        manufacturer_data=manufacturer_data,
        service_data={},
        service_uuids=[],
        source=SOURCE,
        device=BLEDevice(address=address, name=None, details=None),
        advertisement=AdvertisementData(
            local_name=None,
            manufacturer_data=manufacturer_data,
            service_data={},
            service_uuids=[],
            tx_power=None,
            rssi=rssi,
            platform_data=(),
        ),
        connectable=connectable,
        time=time,
        tx_power=None,
    )


def inject_bt_advertisement(
    hass: HomeAssistant, info: BluetoothServiceInfoBleak
) -> None:
    """Deliver an advertisement through Home Assistant's Bluetooth manager."""
    async_get_advertisement_callback(hass)(info)
