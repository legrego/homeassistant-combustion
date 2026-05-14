"""Manage discovered predictive probes."""

import time

from homeassistant.core import callback

from custom_components.combustion.bluetooth_listener import BluetoothListener
from custom_components.combustion.combustion_ble.combustion_probe_data import (
    CombustionProbeData,
)
from custom_components.combustion.const import LOGGER

_LOGGER = LOGGER.getChild('probe_manager')

# Minimum interval between listener notifications for the same probe.
# Combustion probes advertise multiple times per second; without throttling,
# every advertisement triggers state updates for every entity, which floods
# the websocket and overwhelms the dashboard.
_MIN_NOTIFY_INTERVAL_SECONDS = 1.0


class ProbeManager:
    """Manage discovered predictive probes."""

    def __init__(self, bt_listener: BluetoothListener) -> None:
        """Initialize."""
        self.bluetooth_listener = bt_listener
        self.create_sensors_callback = None
        self.data: dict[str, CombustionProbeData] = {}
        self._listeners = []
        self._last_notify: dict[str, float] = {}

    def init_sensor_platform(self, create_sensors_callback):
        """Initialize sensor platform."""
        self.create_sensors_callback = create_sensors_callback

    def init_binary_sensor_platform(self, create_sensors_callback):
        """Initialize binary sensor platform."""
        self.create_binary_sensors_callback = create_sensors_callback

    def async_init(self):
        """Async initialization."""
        self.bluetooth_listener.add_update_listener(self.create_update_callback())

    def create_update_callback(self):
        """Create callback for handling updates."""
        @callback
        def update(probe_data: CombustionProbeData):
            """Handle updated data from predictive probe."""
            if probe_data.serial_number not in self.data:
                _LOGGER.debug("Adding sensors for new device [%s]", probe_data.serial_number)
                self.create_sensors_callback(self, probe_data)
                self.create_binary_sensors_callback(self, probe_data)

            # Always keep the latest data — entity state reads will see fresh values.
            self.data[probe_data.serial_number] = probe_data

            # Rate-limit listener notifications per probe. Combustion probes
            # advertise several times per second; entities only need to be
            # re-rendered ~1Hz for cooking purposes.
            now = time.monotonic()
            last = self._last_notify.get(probe_data.serial_number, 0.0)
            if now - last < _MIN_NOTIFY_INTERVAL_SECONDS:
                return
            self._last_notify[probe_data.serial_number] = now

            _LOGGER.debug("Notifying listeners of new data for [%s]", probe_data.serial_number)
            for listener in self._listeners:
                listener()

        return update

    def add_update_listener(self, listener):
        """Add listener to be notified of probe updates."""
        self._listeners.append(listener)

    def probe_data(self, serial_number: str) -> CombustionProbeData | None:
        """Probe data for provided serial number, or None if not yet seen."""
        return self.data.get(serial_number)
