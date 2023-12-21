from .prediction_mode import PredictionMode
from .prediction_state import PredictionState
from .prediction_type import PredictionType
from .virtual_sensors import VirtualSensors


class PredictionLog:
    def __init__(self, virtual_sensors: VirtualSensors, prediction_state: PredictionState, prediction_mode: PredictionMode, prediction_type: PredictionType, prediction_set_point_temperature: float, prediction_value_seconds: int, estimated_core_temperature: float):
        self.virtual_sensors = virtual_sensors
        self.prediction_state = prediction_state
        self.prediction_mode = prediction_mode
        self.prediction_type = prediction_type
        self.prediction_set_point_temperature = prediction_set_point_temperature
        self.prediction_value_seconds = prediction_value_seconds
        self.estimated_core_temperature = estimated_core_temperature

    @staticmethod
    def from_raw(data):
        # Assuming VirtualSensors, PredictionState, PredictionMode, PredictionType have corresponding methods or constructors
        virtual_sensors = VirtualSensors.from_byte(data[0])

        raw_prediction = (data[1] & 0x07) << 1 | (data[0] & 0x80) >> 7
        prediction_state = PredictionState(raw_prediction) if raw_prediction in PredictionState._member_map_ else PredictionState.UNKNOWN

        raw_mode = (data[1] & PredictionMode.MASK.value) >> 3
        prediction_mode = PredictionMode(raw_mode) if raw_mode in PredictionMode._member_map_ else PredictionMode.NONE

        raw_type = (data[1] & PredictionType.MASK.value) >> 5
        prediction_type = PredictionType(raw_type) if raw_type in PredictionType._member_map_ else PredictionType.NONE

        raw_set_point = (data[3] & 0x01) << 9 | data[2] << 1 | (data[1] & 0x80) >> 7
        prediction_set_point_temperature = float(raw_set_point) * 0.1

        prediction_value_seconds = (data[5] & 0x03) << 15 | data[4] << 7 | (data[3] & 0xFE) >> 1

        raw_core = (data[6] & 0x1F) << 6 | (data[5] & 0xFC) >> 2
        estimated_core_temperature = (float(raw_core) * 0.1) - 20.0

        return PredictionLog(virtual_sensors, prediction_state, prediction_mode, prediction_type, prediction_set_point_temperature, prediction_value_seconds, estimated_core_temperature)

