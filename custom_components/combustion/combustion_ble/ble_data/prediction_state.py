from enum import Enum


class PredictionState(Enum):
    PROBE_NOT_INSERTED = 0x00
    PROBE_INSERTED = 0x01
    COOKING = 0x02
    PREDICTING = 0x03
    REMOVAL_PREDICTION_DONE = 0x04
    RESERVED_STATE_5 = 0x05
    RESERVED_STATE_6 = 0x06
    RESERVED_STATE_7 = 0x07
    RESERVED_STATE_8 = 0x08
    RESERVED_STATE_9 = 0x09
    RESERVED_STATE_10 = 0x0A
    RESERVED_STATE_11 = 0x0B
    RESERVED_STATE_12 = 0x0C
    RESERVED_STATE_13 = 0x0D
    RESERVED_STATE_14 = 0x0E
    UNKNOWN = 0x0F

    MASK = 0xF

    def to_string(self):
        if self == PredictionState.PROBE_NOT_INSERTED:
            return "Probe Not Inserted"
        elif self == PredictionState.PROBE_INSERTED:
            return "Probe Inserted"
        elif self == PredictionState.COOKING:
            return "Cooking"
        elif self == PredictionState.PREDICTING:
            return "Predicting"
        elif self == PredictionState.REMOVAL_PREDICTION_DONE:
            return "Removal Prediction Done"
        elif self.value >= PredictionState.RESERVED_STATE_5.value and self.value <= PredictionState.RESERVED_STATE_14.value:
            return f"Reserved State {self.value - 4}"
        elif self == PredictionState.UNKNOWN:
            return "Unknown"

        return "Unknown"
