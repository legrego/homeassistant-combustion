import struct

from .node_message_type import NodeMessageType
from .node_request import NodeRequest


class NodeReadLogsRequest(NodeRequest):
    def __init__(self, serial_number, min_sequence, max_sequence):
        # Create payload
        payload = bytearray()
        payload += struct.pack('>I', serial_number)
        payload += struct.pack('>I', min_sequence)
        payload += struct.pack('>I', max_sequence)

        super().__init__(outgoing_payload=payload, message_type=NodeMessageType.LOG.value)

