import asyncio
from datetime import datetime
from uuid import UUID

from ..ble_data.advertising_data import AdvertisingData, CombustionProductType
from ..ble_data.battery_status_virtual_sensors import BatteryStatus
from ..ble_data.hop_count import HopCount
from ..ble_data.mode_id import ProbeColor, ProbeID, ProbeMode
from ..ble_data.probe_status import ProbeStatus
from ..ble_data.probe_temperatures import ProbeTemperatures
from ..ble_data.virtual_sensors import VirtualSensors
from ..device_manager import DeviceManager
from ..instant_read_filter import InstantReadFilter
from ..logged_probe_data_count import LoggedProbeDataPoint
from ..prediction.prediction_info import PredictionInfo
from ..prediction.prediction_manager import PredictionManager
from ..probe_temperature_log import ProbeTemperatureLog, SessionInformation
from ..uart.session_info import SessionInformation
from .device import Device


class VirtualTemperatures:
    def __init__(self, core_temperature, surface_temperature, ambient_temperature):
        self.core_temperature = core_temperature
        self.surface_temperature = surface_temperature
        self.ambient_temperature = ambient_temperature

class Probe(Device):
    INSTANT_READ_STALE_TIMEOUT = 5.0
    #  Number of seconds to ignore other lower-priority (higher hop count) sources of information for Instant Read
    INSTANT_READ_LOCK_TIMEOUT = 1.0

    # Number of seconds to ignore other lower-priority (higher hop count) sources of information for Normal Mode
    NORMAL_MODE_LOCK_TIMEOUT = 1.0

    # Number of seconds after which status notifications should be considered stale.
    STATUS_NOTIFICATION_STALE_TIMEOUT = 16.0

    # Overheating thresholds (in degrees C) for T1 and T2
    OVERHEATING_T1_T2_THRESHOLD = 105.0
    # Overheating thresholds (in degrees C) for T3
    OVERHEATING_T3_THRESHOLD = 115.0
    # Overheating thresholds (in degrees C) for T4
    OVERHEATING_T4_THRESHOLD = 125.0
    # Overheating thresholds (in degrees C) for T5-T8
    OVERHEATING_T5_T8_THRESHOLD = 300.0

    def __init__(self, advertising: AdvertisingData, is_connectable=None, rssi=None, identifier: UUID =None):
        super().__init__(unique_identifier=str(advertising.serial_number),
                         ble_identifier=identifier,
                         rssi=rssi)
        self.serial_number = advertising.serial_number
        self.serial_number_string = f"{self.serial_number:08X}"

        self.id = advertising.mode_id.id
        self.color = advertising.mode_id.color
        self.current_temperatures = None
        self.instant_read_celsius = None
        self.instant_read_fahrenheit = None
        self.instant_read_temperature = None
        self.min_sequence_number = None
        self.max_sequence_number = None
        self.percent_of_logs_synced = None
        self.battery_status = BatteryStatus.OK
        self.virtual_sensors = None
        self.prediction_info: PredictionInfo = None
        self.virtual_temperatures = None
        self.temperature_logs: list[ProbeTemperatureLog] = []
        self.overheating = False
        self.overheating_sensors = []
        self.last_status_notification_time = datetime.now()
        self.status_notifications_stale = False
        self.session_information: SessionInformation = None
        self.last_instant_read = None
        self.last_instant_read_hop_count = None
        self.last_normal_mode = None
        self.last_normal_mode_hop_count = None
        self.prediction_manager = PredictionManager()
        self.instant_read_filter = InstantReadFilter()
        self.session_request_task = None

        self.prediction_manager.add_update_listener(self.publish_prediction_info)

        # Update the probe with advertising data
        self.update_with_advertising(advertising, is_connectable, rssi, identifier)

        # Start timer to re-request session information every 3 minutes
        self.start_session_request_timer()

    async def session_request_timer(self):
        while True:
            await asyncio.sleep(180)  # Wait for 180 seconds
            await self.request_session_information()

    def start_session_request_timer(self):
        if self.session_request_task is None or self.session_request_task.done():
            self.session_request_task = asyncio.create_task(self.session_request_timer())

    def stop_session_request_timer(self):
        if self.session_request_task and not self.session_request_task.done():
            self.session_request_task.cancel()

    def publish_prediction_info(self, prediction_info: PredictionInfo):
        self.prediction_info = prediction_info

    def update_connection_state(self, state):
        if state == self.ConnectionState.DISCONNECTED:
            self.session_information = None
        super().update_connection_state(state)

    def update_device_stale(self):
        """Updates the device's stale status. Clears instant read temperatures if they are stale
        and updates whether status notifications are stale.
        """
        if self.last_instant_read:
            time_since_last_instant_read = (datetime.now() - self.last_instant_read).total_seconds()
            if time_since_last_instant_read > self.INSTANT_READ_STALE_TIMEOUT:
                self.instant_read_celsius = None
                self.instant_read_fahrenheit = None
                self.instant_read_temperature = None

        self.update_status_notifications_stale()
        super().update_device_stale()

    def update_with_advertising(self, advertising: AdvertisingData, is_connectable, rssi, ble_identifier: UUID):
        # Update probe with advertising data
        if rssi:
            self.rssi = rssi
        if is_connectable is not None:
            self.is_connectable = self.is_connectable
        if ble_identifier is not None:
            self.ble_identifier = ble_identifier

        # Only update rest of data if not connected to probe.
        # Otherwise, rely on status notifications to update data
        if self.connection_state != self.ConnectionState.CONNECTED:
            if advertising.mode_id.mode == ProbeMode.Normal:
                # If we should update normal mode, do so, but since this is Advertising info
                # and does not contain Prediction information, DO NOT lock it out. We want to
                # ensure the Prediction info gets updated over a Status notification if one
                # comes in.
                if self.should_update_normal_mode(advertising.hop_count):
                    # Update ID, Color, Battery status
                    self.update_id_color_battery(advertising.mode_id.id, advertising.mode_id.color, advertising.battery_status_virtual_sensors.battery_status)

                    # Update temperatures, virtual sensors, and check for overheating
                    self.update_temperatures(advertising.temperatures, advertising.battery_status_virtual_sensors.virtual_sensors)

                    self.last_update_time = datetime.now()
            elif advertising.mode_id.mode == ProbeMode.instantRead:
                #  Update Instant Read temperature, providing hop count information to prioritize it.
                hop_count = None
                if advertising.type != CombustionProductType.PROBE:
                    hop_count = advertising.hop_count

                if self.update_instant_read(advertising.temperatures.values[0], advertising.mode_id.id, advertising.mode_id.color, advertising.battery_status_virtual_sensors.battery_status, hop_count):
                    self.last_update_time = datetime.now()
        pass

    def update_id_color_battery(self, probe_id: ProbeID, probe_color: ProbeColor, probe_battery_status: BatteryStatus):
        self.id = probe_id
        self.color = probe_color
        self.battery_status = probe_battery_status

    def update_temperatures(self, temperatures: ProbeTemperatures, virtual_sensors: VirtualSensors):
        self.current_temperatures = temperatures
        self.virtual_sensors = virtual_sensors

        core = virtual_sensors.virtual_core.temperature_from(temperatures)
        surface = virtual_sensors.virtual_surface.temperature_from(temperatures)
        ambient = virtual_sensors.virtual_ambient.temperature_from(temperatures)

        self.virtual_temperatures = VirtualTemperatures(core, surface, ambient)

        self.check_overheating()

    def check_overheating(self):
        if not self.current_temperatures:
            return

        any_over_temp = False
        overheating_sensor_list: list[int] = []

        # Check T1-T2
        for i in range(0, 2):
            if self.current_temperatures.values[i] >= self.OVERHEATING_T1_T2_THRESHOLD:
                any_over_temp = True
                overheating_sensor_list.append(i)

        # Check T3
        if self.current_temperatures.values[2] >= self.OVERHEATING_T3_THRESHOLD:
            any_over_temp = True
            overheating_sensor_list.append(2)

        # Check T4
        if self.current_temperatures.values[3] >= self.OVERHEATING_T4_THRESHOLD:
            any_over_temp = True
            overheating_sensor_list.append(3)

        # Check T5-T8
        for i in range(4, 8):
            if self.current_temperatures.values[i] >= self.OVERHEATING_T5_T8_THRESHOLD:
                any_over_temp = True
                overheating_sensor_list.append(i)

        self.overheating = any_over_temp
        self.overheating_sensors = overheating_sensor_list

    def update_probe_status(self, device_status: ProbeStatus, hop_count: HopCount=None):
        # Ignore status messages that have a sequence count lower than any previously received status messages
        if self.is_old_status_update(device_status):
            return
        updated = False
        if device_status.mode_id.mode == ProbeMode.normal:
            if self.should_update_normal_mode(hop_count):
                self.update_id_color_battery(device_status.mode_id.id, device_status.mode_id.color, device_status.battery_status_virtual_sensors.battery_status)

                self.min_sequence_number = device_status.min_sequence_number
                self.max_sequence_number = device_status.max_sequence_number

                self.prediction_manager.update_prediction_status(device_status.prediction_status, device_status.max_sequence_number)

                self.update_temperatures(device_status.temperatures, device_status.max_sequence_number)

                self.add_data_to_log(LoggedProbeDataPoint.from_device_status(device_status))

                self.last_normal_mode = datetime.now()
                self.last_normal_mode_hop_count = hop_count

                updated = True
        elif device_status.mode_id.mode == ProbeMode.instantRead:
            updated = self.update_instant_read(device_status.temperatures.values[0], probe_id=device_status.mode_id.id, probe_color=device_status.mode_id.color, probe_battery_status=device_status.battery_status_virtual_sensors.battery_status, hop_count=hop_count)
            if updated:
                self.min_sequence_number = device_status.min_sequence_number
                self.max_sequence_number = device_status.max_sequence_number

        self.request_missing_data()

        if updated:
            current = self._get_current_temperature_log()
            if current:
                self.update_log_percent()

                missing_range = current.missing_range(device_status.min_sequence_number, device_status.max_sequence_number)
                if missing_range:
                    DeviceManager.shared.request_logs_from(self, min_sequence=missing_range[0], max_sequence=missing_range[1])

        self.last_status_notification_time = datetime.now()
        self.update_status_notifications_stale()
        self.last_update_time = datetime.now()

    def update_instant_read(self, instant_read_value: float, probe_id: ProbeID, probe_color: ProbeColor, probe_battery_status: BatteryStatus, hop_count: HopCount) -> bool:
        if self.should_update_instant_read(hop_count):
            self.last_instant_read = datetime.now()
            self.last_instant_read_hop_count = hop_count
            self.instant_read_filter.add_reading(instant_read_value)
            self.instant_read_temperature = instant_read_value
            self.instant_read_celsius = self.instant_read_filter.values[0]
            self.instant_read_fahrenheit = self.instant_read_filter.values[1]

            self.update_id_color_battery(probe_id, probe_color, probe_battery_status)

            return True
        else:
            return False

    def update_with_session_information(self, session_information: SessionInformation):
        self.session_information = session_information

    def update_log_percent(self) -> None:
        current_log = self._get_current_temperature_log()
        max_sequence_number = self.max_sequence_number
        min_sequence_number = self.min_sequence_number
        if max_sequence_number is None or min_sequence_number is None or current_log is None:
            return

        number_logs_from_probe = current_log.logs_in_range([min_sequence_number, max_sequence_number])
        number_logs_on_probe = int(max_sequence_number - min_sequence_number + 1)

        if number_logs_from_probe == number_logs_on_probe:
            self.percent_of_logs_synced = 100
        else:
            self.percent_of_logs_synced = int(float(number_logs_from_probe) / float(number_logs_on_probe) * 100)

    def is_old_status_update(self, device_status: ProbeStatus) -> bool:
        current_temp_log = self._get_current_temperature_log()
        if current_temp_log:
            max = current_temp_log.data_points[-1]
            return device_status.max_sequence_number < max.sequence_num
        return False

    def _get_current_temperature_log(self) -> ProbeTemperatureLog | None:
        return next((log for log in self.temperature_logs if log.session_information.session_id == self.session_information.session_id), None)

    def add_data_to_log(self, data_point: LoggedProbeDataPoint) -> None:
        current = self._get_current_temperature_log()
        if current:
            current.append_data_point(data_point=data_point)
        elif self.session_information:
            log = ProbeTemperatureLog(self.session_information)
            log.append_data_point(data_point=data_point)
            self.temperature_logs.append(log)

    def process_log_response(self, log_response):
        # Process log response
        pass

    def update_status_notifications_stale(self):
        """Updates the status of whether the status notifications are stale.
        This is based on the time elapsed since the last status notification.
        """
        time_since_last_notification = (datetime.now() - self.last_status_notification_time).total_seconds()
        self.status_notifications_stale = time_since_last_notification > self.STATUS_NOTIFICATION_STALE_TIMEOUT

    def request_missing_data(self) -> None:
        if self.session_information is None:
            DeviceManager.shared.read_session_info(self)

        if self.firmware_version is None:
            DeviceManager.shared.read_firmware_version(self)

        if self.hardware_revision is None:
            DeviceManager.shared.read_hardware_version(self)

        if self.manufacturing_lot is None or self.sku is None:
            DeviceManager.shared.read_model_info_for_probe(self)


    # Methods related to DFU functionalities
    def run_software_upgrade(self, dfu_file):
        # Placeholder for running software upgrade
        pass

    def dfu_state_did_change(self, state):
        pass

    def dfu_error_did_occur(self, error, message):
        pass

    def dfu_progress_did_change(self, part, total_parts, progress):
        pass

    def log_with_level(self, level, message):
        # Placeholder for logging
        pass

    def should_update_normal_mode(self, hop_count: HopCount) -> bool:
        if not HopCount:
            return True
        time_since_last_normal_mode = (datetime.now() - self.last_normal_mode).total_seconds()
        if time_since_last_normal_mode > self.NORMAL_MODE_LOCK_TIMEOUT:
            return True

        if self.last_normal_mode_hop_count is None:
            return False

        if hop_count.value <= self.last_normal_mode_hop_count.value:
            return True

        return False

    def should_update_instant_read(self, hop_count: HopCount) -> bool:
        # If hopCount is nil, this is direct from a Probe and we should always update.
        if not hop_count:
            return True

        # If we haven't received Instant Read data for more than the lockout period, we should always update.
        time_since_last_instant_read = (datetime.now() - self.lastInstantRead).total_seconds()
        if time_since_last_instant_read > self.INSTANT_READ_LOCK_TIMEOUT:
            return True

        # If we're in the lockout period and the last hop count was nil (i.e. direct from a Probe),
        # we should NOT update.
        if self.last_instant_read_hop_count is None:
            return False

        # Compare hop counts and see if we should update.
        if hop_count.value <= self.last_instant_read_hop_count.value:
            # This hop count is equal or better priority than the last, so update.
            return True
        else:
            # This hop is lower priority than the last, so do not update.
            return False

    def request_session_information(self):
        DeviceManager.shared.read_session_info(self)

# Implement the PredictionManagerDelegate interface
class PredictionManagerDelegate:
    def publish_prediction_info(self, info):
        # Implementation for updating prediction info
        pass
