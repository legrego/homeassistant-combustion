"""Diagnostics for the Combustion integration.

Everything here is chosen rather than redacted from a full dump: serial numbers and Bluetooth
addresses identify a user's devices, and cloud payloads also carry account identifiers. Devices
are numbered instead, the same way in each section.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from combustion_ble import Gauge, Probe
from combustion_cloud import DeviceIdentity, DeviceStatus
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .cloud import CloudManager
from .const import CONF_ENABLE_PREDICTIONS
from .coordinator import CombustionConfigEntry, CombustionCoordinator
from .device import CombustionDevice, ProbeDevice


class _Labels:
    """Numbers devices in the order they are first mentioned."""

    def __init__(self) -> None:
        self._labels: dict[str, str] = {}

    def __call__(self, identifier: str) -> str:
        return self._labels.setdefault(identifier, f"device {len(self._labels) + 1}")


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CombustionConfigEntry
) -> dict[str, Any]:
    """Return what the integration knows, without anything that identifies the user."""
    coordinator = entry.runtime_data
    labels = _Labels()
    return {
        "options": {
            CONF_ENABLE_PREDICTIONS: entry.options.get(CONF_ENABLE_PREDICTIONS, False)
        },
        "devices": {
            labels(device.identifier): _device(device)
            for device in coordinator.devices.values()
        },
        "bluetooth": _bluetooth(coordinator, labels),
        "cloud": (
            None if coordinator.cloud is None else _cloud(coordinator.cloud, labels)
        ),
    }


def _bluetooth(coordinator: CombustionCoordinator, labels: _Labels) -> dict[str, Any]:
    repeater = coordinator.repeater
    return {
        "repeaters_heard": len(coordinator.meatnet.nodes),
        "repeater_connected": repeater is not None and repeater.session is not None,
        "direct_connections": sum(
            monitor.session is not None for monitor in coordinator.predictions.values()
        ),
        "devices": {
            labels(device.identifier): _bluetooth_device(device.bluetooth)
            for device in coordinator.devices.values()
            if device.bluetooth is not None
        },
    }


def _device(device: CombustionDevice) -> dict[str, Any]:
    """Describe what entities show."""
    result: dict[str, Any] = {
        "model": device.model,
        "available": device.available,
        "low_battery": device.low_battery,
    }
    if isinstance(device, ProbeDevice):
        prediction = device.prediction
        food_safety = device.food_safety
        result |= {
            "source": device.source,
            "bluetooth_age": device.bluetooth_age,
            "bluetooth_readings_age": device.bluetooth_readings_age,
            "cloud_age": device.cloud_age,
            "readings": asdict(device.readings),
            "prediction": None if prediction is None else asdict(prediction),
            "food_safety": None if food_safety is None else asdict(food_safety),
        }
    return result


def _bluetooth_device(heard: Probe | Gauge) -> dict[str, Any]:
    result: dict[str, Any] = {"available": heard.available, "rssi": heard.rssi}
    if isinstance(heard, Probe):
        result |= {
            "hop_count": heard.source_hop_count,
            "prediction_reported": heard.prediction is not None,
            "food_safety_reported": heard.food_safe is not None,
        }
    return result


def _cloud(cloud: CloudManager, labels: _Labels) -> dict[str, Any]:
    now = dt_util.utcnow()
    snapshot = cloud.snapshot
    return {
        "connected": cloud.connected,
        "last_error": cloud.last_error,
        "snapshot_age": _age(cloud.snapshot_at, now),
        "linked_devices": None if snapshot is None else len(snapshot.account.devices),
        "skipped_associations": (
            None if snapshot is None else snapshot.account.skipped_associations
        ),
        "unreadable_devices": {
            _cloud_label(identity, labels): type(error).__name__
            for identity, error in (snapshot.errors if snapshot else {}).items()
        },
        "devices": {
            _cloud_label(identity, labels): _cloud_device(status, now)
            for identity, status in cloud.store.devices.items()
        },
    }


def _cloud_label(identity: DeviceIdentity, labels: _Labels) -> str:
    serial = identity.probe_serial
    return labels(identity.serial_number if serial is None else f"{serial:x}")


def _cloud_device(status: DeviceStatus, now: datetime) -> dict[str, Any]:
    food_safe_data = status.food_safe_data
    food_safe_status = status.food_safe_status
    return {
        "product_type": status.identity.product_type,
        # Large or negative ages point at the clock of whichever device uploads readings.
        "sample_age": _age(status.sampled_at, now),
        "published_age": _age(status.published_at, now),
        "sample_period_ms": status.sample_period_ms,
        "temperatures": list(status.temperatures),
        "temperature": status.temperature,
        "virtual_sensors": dict(status.virtual_sensors),
        "battery_status": status.battery_status,
        "firmware_version": status.firmware_version,
        "hardware_revision": status.hardware_revision,
        "prediction": status.prediction.as_dict(),
        "food_safety": None
        if food_safe_data is None
        else {
            "mode": food_safe_data.mode,
            "product_type": food_safe_data.product_type,
            "serving": food_safe_data.serving,
            "program_set": food_safe_data.program_set,
        },
        "food_safety_state": (
            None if food_safe_status is None else food_safe_status.state
        ),
        # Names only, to notice when the undocumented service changes.
        "fields": sorted(status.raw),
    }


def _age(at: datetime | None, now: datetime) -> float | None:
    return None if at is None else round((now - at).total_seconds(), 1)
