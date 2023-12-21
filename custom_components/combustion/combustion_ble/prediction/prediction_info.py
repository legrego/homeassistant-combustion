"""Prediction Info."""
from ..ble_data.prediction_mode import PredictionMode
from ..ble_data.prediction_state import PredictionState
from ..ble_data.prediction_type import PredictionType


class PredictionInfo:
    """Prediction Info."""

    def __init__(self, prediction_state: PredictionState, prediction_mode: PredictionMode, prediction_type: PredictionType, prediction_set_point_temperature: float, estimated_core_temperature: float, seconds_remaining: int =None, percent_through_cook: int =0):
        """Initialize."""
        self.prediction_state = prediction_state
        self.prediction_mode = prediction_mode
        self.prediction_type = prediction_type
        self.prediction_set_point_temperature = prediction_set_point_temperature
        self.estimated_core_temperature = estimated_core_temperature
        self.seconds_remaining = seconds_remaining
        self.percent_through_cook = percent_through_cook
