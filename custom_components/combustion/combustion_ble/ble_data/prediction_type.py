from enum import Enum


class PredictionType(Enum):
    NONE = 0x00
    REMOVAL = 0x01
    RESTING = 0x02
    RESERVED = 0x03

    MASK = 0x3

    def to_string(self):
        if self == PredictionType.NONE:
            return "None"
        elif self == PredictionType.REMOVAL:
            return "Removal"
        elif self == PredictionType.RESTING:
            return "Resting"
        elif self == PredictionType.RESERVED:
            return "Reserved"

        return "Unknown"
