"""Hop Count."""
from enum import Enum


class HopCount(Enum):
    """Hop Count."""

    HOP1 = 0x00
    HOP2 = 0x01
    HOP3 = 0x02
    HOP4 = 0x03

    HOP_COUNT_MASK = 0x3
    HOP_COUNT_SHIFT = 6

    @staticmethod
    def from_network_info_byte(network_info_byte):
        """Generate hop count from network info byte."""
        raw_hop_count = (network_info_byte >> HopCount.HOP_COUNT_SHIFT.value) & HopCount.HOP_COUNT_MASK.value
        return HopCount(raw_hop_count) if raw_hop_count in HopCount._value2member_map_ else HopCount.HOP1

    @staticmethod
    def default_values():
        """Generate default values."""
        return HopCount.HOP1
