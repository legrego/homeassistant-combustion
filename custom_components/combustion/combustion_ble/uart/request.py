import struct
from typing import Union

from ..utilities.crc16ccitt import crc16ccitt
from .message_type import MessageType


class Request:
    HEADER_SIZE = 6

    def __init__(self, payload: Union[bytes, bytearray], message_type: MessageType):
        self.data = bytearray()

        # Sync Bytes
        self.data.extend([0xCA, 0xFE])

        # Prepare data for CRC calculation
        crc_data = bytearray()
        crc_data.append(message_type)
        crc_data.append(len(payload))
        crc_data.extend(payload)

        # Calculate CRC
        crc_value = crc16ccitt(crc_data)

        # Append CRC to data
        self.data.extend(struct.pack('>H', crc_value))

        # Append message type, payload length, and payload
        self.data.extend(crc_data)

