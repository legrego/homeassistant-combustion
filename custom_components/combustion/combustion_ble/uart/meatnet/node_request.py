import random
import struct

from ...utilities.crc16ccitt import crc16ccitt
from .node_message_type import NodeMessageType


class NodeRequest:
    HEADER_LENGTH = 10

    def __init__(self, outgoing_payload, message_type, request_id=None):
        self.data = bytearray()
        self.payload_length = len(outgoing_payload)

        # Sync Bytes { 0xCA, 0xFE }
        self.data += bytearray([0xCA, 0xFE])

        # Message type
        self.data.append(message_type.value)

        # Request ID
        self.request_id = request_id if request_id else random.randint(1, 0xFFFFFFFF)
        self.data += struct.pack('>I', self.request_id)

        # Payload length
        self.data.append(len(outgoing_payload))

        # Payload
        self.data += outgoing_payload

        # Calculate CRC
        crc_value = crc16ccitt(self.data[2:])
        self.data = self.data[:2] + struct.pack('>H', crc_value) + self.data[2:]

    @classmethod
    def request_from_data(cls, data):
        if data[:2] != b'\xCA\xFE':
            print("Missing sync bytes in request")
            return None

        message_type_raw = data[4]
        message_type = NodeMessageType(message_type_raw)

        # Assuming NodeMessageType is already defined
        if message_type is None:
            print("Unknown message type in request")
            return None

        # Request ID
        request_id = struct.unpack('>I', data[5:9])[0]

        # Payload Length
        payload_length = data[9]

        # CRC Check
        crc = struct.unpack('>H', data[2:4])[0]
        calculated_crc = crc16ccitt(data[4:10 + payload_length])

        if crc != calculated_crc:
            print("Invalid CRC")
            return None

        # TODO: Handle different messages types

        return cls(data[10:], message_type, request_id)

