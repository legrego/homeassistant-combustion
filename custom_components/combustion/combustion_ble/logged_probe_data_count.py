from .ble_data.probe_status import ProbeStatus
from .uart.meatnet.node_read_logs_response import NodeReadLogsResponse


class LoggedProbeDataPoint:
    _sequence_num = 0  # Class variable for generating fake data

    def __init__(self, sequence_num = None, temperatures = None, virtual_core = None, virtual_surface = None, virtual_ambient = None,
                 prediction_state = None, prediction_mode = None, prediction_type = None, prediction_set_point_temperature = None,
                 prediction_value_seconds = None, estimated_core_temperature = None):
        self.sequence_num = sequence_num
        self.temperatures = temperatures
        self.virtual_core = virtual_core
        self.virtual_surface = virtual_surface
        self.virtual_ambient = virtual_ambient
        self.prediction_state = prediction_state
        self.prediction_mode = prediction_mode
        self.prediction_type = prediction_type
        self.prediction_set_point_temperature = prediction_set_point_temperature
        self.prediction_value_seconds = prediction_value_seconds
        self.estimated_core_temperature = estimated_core_temperature

    @classmethod
    def from_device_status(cls, device_status: ProbeStatus):
        return cls(
            sequence_number = device_status.max_sequence_number,
            temperatures = device_status.temperatures,
            virtual_core = device_status.battery_status_virtual_sensors.virtual_sensors.virtual_core,
            virtual_surface = device_status.battery_status_virtual_sensors.virtual_sensors.virtual_surface,
            virtual_ambient = device_status.battery_status_virtual_sensors.virtual_sensors.virtual_ambient,
            prediction_state = device_status.prediction_status.prediction_state,
            prediction_mode = device_status.prediction_status.prediction_mode,
            prediction_type = device_status.prediction_status.prediction_type,
            prediction_set_point_temperature = device_status.prediction_status.prediction_set_point_temperature,
            prediction_value_seconds = device_status.prediction_status.prediction_value_seconds,
            estimated_core_temperature = device_status.prediction_status.estimated_core_temperature,
        )

    @classmethod
    def from_log_response(cls, log_response):
        # Define conversion from log_response to LoggedProbeDataPoint
        pass

    @classmethod
    def from_node_read_logs_response(cls, logs_response: NodeReadLogsResponse):
        return cls(
            sequence_number = logs_response.sequence_number,
            temperatures = logs_response.temperatures,
            virtual_core= logs_response.prediction_log.virtual_sensors.virtual_core,
            virtual_surface= logs_response.prediction_log.virtual_sensors.virtual_surface,
            virtual_ambient= logs_response.prediction_log.virtual_sensors.virtual_ambient,
            prediction_state= logs_response.prediction_log.prediction_state,
            prediction_mode= logs_response.prediction_log.prediction_mode,
            prediction_type= logs_response.prediction_log.prediction_type,
            prediction_set_point_temperature= logs_response.prediction_log.prediction_set_point_temperature,
            prediction_value_seconds= logs_response.prediction_log.prediction_value_seconds,
            estimated_core_temperature= logs_response.prediction_log.estimated_core_temperature)

    def __eq__(self, other):
        if isinstance(other, LoggedProbeDataPoint):
            return self.sequence_num == other.sequence_num
        return False

    def __hash__(self):
        return hash(self.sequence_num)
