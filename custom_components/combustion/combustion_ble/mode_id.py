"""ModeId portion of advertisement payload."""
from enum import Enum, unique


@unique
class ProbeID(Enum):
    """Probe ID."""

    ID1 = 0x00
    ID2 = 0x01
    ID3 = 0x02
    ID4 = 0x03
    ID5 = 0x04
    ID6 = 0x05
    ID7 = 0x06
    ID8 = 0x07

@unique
class ProbeColor(Enum):
    """Probe Color."""

    color1 = 0x00
    color2 = 0x01
    color3 = 0x02
    color4 = 0x03
    color5 = 0x04
    color6 = 0x05
    color7 = 0x06
    color8 = 0x07

@unique
class ProbeMode(Enum):
    """Probe Mode."""

    normal = 0x00
    instantRead = 0x01
    reserved = 0x02
    error = 0x03

class ModeId:
    """ModeId portion of advertisement payload."""

    PROBE_ID_MASK = 0x7
    PROBE_ID_SHIFT = 5
    PROBE_COLOR_MASK = 0x7
    PROBE_COLOR_SHIFT = 2
    PROBE_MODE_MASK = 0x3

    def __init__(self, id: ProbeID, color: ProbeColor, mode: ProbeMode):
        """Initialize."""
        self.id = id
        self.color = color
        self.mode = mode

    @classmethod
    def from_byte(cls, byte) -> 'ModeId':
        """Create instance from byte."""
        raw_probe_id = (byte >> cls.PROBE_ID_SHIFT) & cls.PROBE_ID_MASK
        id = ProbeID(raw_probe_id)

        raw_probe_color = (byte >> cls.PROBE_COLOR_SHIFT) & cls.PROBE_COLOR_MASK
        color = ProbeColor(raw_probe_color)

        raw_mode = byte & cls.PROBE_MODE_MASK
        mode = ProbeMode(raw_mode)

        return cls(id, color, mode)

    @staticmethod
    def default_values():
        """Generate default values."""
        return ModeId(ProbeID.ID1, ProbeColor.color1, ProbeMode.normal)
