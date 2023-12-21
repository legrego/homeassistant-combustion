from .prediction_mode import PredictionMode
from .prediction_state import PredictionState
from .prediction_type import PredictionType


class PredictionStatus:
    def __init__(self, prediction_state: PredictionState, prediction_mode: PredictionMode, prediction_type: PredictionType, prediction_set_point_temperature: float, heat_start_temperature: float, prediction_value_seconds: float, estimated_core_temperature: float):
        self.prediction_state = prediction_state
        self.prediction_mode = prediction_mode
        self.prediction_type = prediction_type
        self.prediction_set_point_temperature = prediction_set_point_temperature
        self.heat_start_temperature = heat_start_temperature
        self.prediction_value_seconds = prediction_value_seconds
        self.estimated_core_temperature = estimated_core_temperature

    def to_dict(self):
        return {
            'prediction_state': self.prediction_state.value,
            'prediction_mode': self.prediction_mode.value,
            'prediction_type': self.prediction_type.value,
            'prediction_set_point_temperature': self.prediction_set_point_temperature,
            'heat_start_temperature': self.heat_start_temperature,
            'prediction_value_seconds': self.prediction_value_seconds,
            'estimated_core_temperature': self.estimated_core_temperature
        }

    @staticmethod
    def from_bytes(bytes):
        raw_prediction_state = bytes[0] & PredictionState.MASK.value
        prediction_state = PredictionState(raw_prediction_state) if raw_prediction_state in PredictionState._member_map_ else PredictionState.UNKNOWN

        raw_prediction_mode = (bytes[0] >> 4) & PredictionMode.MASK.value
        prediction_mode = PredictionMode(raw_prediction_mode) if raw_prediction_mode in PredictionMode._member_map_ else PredictionMode.NONE

        raw_prediction_type = (bytes[0] >> 6) & PredictionType.MASK.value
        prediction_type = PredictionType(raw_prediction_type) if raw_prediction_type in PredictionType._member_map_ else PredictionType.NONE

        raw_set_point = (bytes[2] & 0x03) << 8 | bytes[1]
        set_point = float(raw_set_point) * 0.1

        raw_heat_start = (bytes[3] & 0x0F) << 6 | (bytes[2] & 0xFC) >> 2
        heat_start = float(raw_heat_start) * 0.1

        seconds = (bytes[5] & 0x1F) << 12 | bytes[4] << 4 | (bytes[3] & 0xF0) >> 4

        raw_core = bytes[6] << 3 | (bytes[5] & 0xE0) >> 5
        estimated_core = (float(raw_core) * 0.1) - 20.0

        return PredictionStatus(prediction_state, prediction_mode, prediction_type, set_point, heat_start, seconds, estimated_core)
