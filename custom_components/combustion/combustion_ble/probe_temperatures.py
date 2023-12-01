"""Probe temperature data."""

class ProbeTemperatures:
    """Temperature values for a single probe."""

    def __init__(self, values: list[float]):
        """Initialize."""
        self.values = values

    @staticmethod
    def from_reversed(bytes_: list[int]) -> 'ProbeTemperatures':
        """Create instance from reversed bytes."""
        raw_temps = []
        raw_temps.insert(0, (bytes_[0]  & 0xFF) << 5  | (bytes_[1]  & 0xF8) >> 3 )
        raw_temps.insert(0, (bytes_[1]  & 0x07) << 10 | (bytes_[2]  & 0xFF) << 2 | (bytes_[3]  & 0xC0) >> 6)
        raw_temps.insert(0, (bytes_[3]  & 0x3F) <<  7 | (bytes_[4]  & 0xFE) >> 1 )
        raw_temps.insert(0, (bytes_[4]  & 0x01) << 12 | (bytes_[5]  & 0xFF) << 4 | (bytes_[6]  & 0xF0) >> 4)
        raw_temps.insert(0, (bytes_[6]  & 0x0F) <<  9 | (bytes_[7]  & 0xFF) << 1 | (bytes_[8]  & 0x80) >> 7 )
        raw_temps.insert(0, (bytes_[8]  & 0x7F) <<  6 | (bytes_[9]  & 0xFC) >> 2 )
        raw_temps.insert(0, (bytes_[9]  & 0x03) << 11 | (bytes_[10] & 0xFF) << 3 | (bytes_[11] & 0xE0) >> 5)
        raw_temps.insert(0, (bytes_[11] & 0x1F) <<  8 | (bytes_[12] & 0xFF) >> 0)

        temperatures = [float(temp) * 0.05 - 20.0 for temp in raw_temps]
        return ProbeTemperatures(values=temperatures)

    @staticmethod
    def from_raw_data(data: bytes) -> 'ProbeTemperatures':
        """Create instance from raw data."""
        bytes_ = list(data)
        bytes_.reverse()  # Reversing the byte order
        return ProbeTemperatures.from_reversed(bytes_)

