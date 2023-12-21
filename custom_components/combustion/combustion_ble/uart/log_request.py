import struct

from .message_type import MessageType
from .request import Request


class LogRequest(Request):
    def __init__(self, min_sequence: int, max_sequence: int):
        # Packing the min and max sequence numbers into binary format
        min_payload = struct.pack('I', min_sequence)  # 'I' is for unsigned int
        max_payload = struct.pack('I', max_sequence)

        # Combining the payloads
        combined_payload = min_payload + max_payload

        # Calling the superclass initializer with the combined payload and a type
        super().__init__(payload=combined_payload, message_type=MessageType.LOG)
