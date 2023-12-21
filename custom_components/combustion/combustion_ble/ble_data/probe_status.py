from .battery_status_virtual_sensors import BatteryStatusVirtualSensors
from .mode_id import ModeId
from .prediction_status import PredictionStatus
from .probe_temperatures import ProbeTemperatures


class ProbeStatus:
    def __init__(self, min_sequence_number: int, max_sequence_number: int, temperatures: ProbeTemperatures, mode_id: ModeId, battery_status_virtual_sensors: BatteryStatusVirtualSensors, prediction_status: PredictionStatus):
        self.min_sequence_number = min_sequence_number
        self.max_sequence_number = max_sequence_number
        self.temperatures = temperatures
        self.mode_id = mode_id
        self.battery_status_virtual_sensors = battery_status_virtual_sensors
        self.prediction_status = prediction_status

    @classmethod
    def from_data(cls, data):
        if len(data) < 30:
            return None

        min_sequence_number = int.from_bytes(data[0:4], 'big')
        max_sequence_number = int.from_bytes(data[4:8], 'big')

        # Assuming ProbeTemperatures, ModeId, BatteryStatusVirtualSensors have corresponding from_raw_data or from_byte methods
        temperatures = ProbeTemperatures.from_raw_data(data[8:21])
        mode_id = ModeId.from_byte(data[21])
        battery_status_virtual_sensors = BatteryStatusVirtualSensors.from_byte(data[22])

        prediction_status_bytes = data[23:30]
        prediction_status = PredictionStatus.from_bytes(prediction_status_bytes)

        return cls(min_sequence_number, max_sequence_number, temperatures, mode_id, battery_status_virtual_sensors, prediction_status)

