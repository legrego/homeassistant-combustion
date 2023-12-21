import struct

from ...utilities.crc16ccitt import crc16ccitt
from .node_message_type import NodeMessageType
from .node_read_logs_response import NodeReadLogsResponse


class NodeResponse:
    HEADER_LENGTH = 15
    RESPONSE_TYPE_FLAG = 0x80

    def __init__(self, success, request_id, response_id, payload_length):
        self.success = success
        self.request_id = request_id
        self.response_id = response_id
        self.payload_length = payload_length

    @staticmethod
    def response_from_data(data):
        # Sync bytes
        sync_bytes = data[0:2]
        sync_string = ''.join(format(x, '02x') for x in sync_bytes)
        if sync_string != 'cafe':
            print("NodeResponse::from_data(): Missing sync bytes in response")
            return None

        # Message type
        type_byte = data[4]

        # Verify that this is a Response by checking the response type flag
        if type_byte & NodeResponse.RESPONSE_TYPE_FLAG != NodeResponse.RESPONSE_TYPE_FLAG:
            # If that 'response type' bit isn't set, this is probably a Request.
            return None

        message_type = NodeMessageType(type_byte & ~NodeResponse.RESPONSE_TYPE_FLAG)
        if message_type is None:
            print("NodeResponse::from_data(): Unknown message type in response")
            return None

        # Request ID
        request_id = struct.unpack('>I', data[5:9])[0]

        # Response ID
        response_id = struct.unpack('>I', data[9:13])[0]

        # Success/Fail
        success = bool(data[13])

        # Payload Length
        payload_length = data[14]

        # CRC
        crc = struct.unpack('>H', data[2:4])[0]
        crc_data = data[4:15 + payload_length]
        calculated_crc = crc16ccitt(crc_data)

        if crc != calculated_crc:
            print("NodeResponse::from_data(): Invalid CRC")
            return None

        response_length = payload_length + NodeResponse.HEADER_LENGTH
        if len(data) < response_length:
            print("Bad number of bytes")
            return None

        if message_type == NodeMessageType.LOG:
            return NodeReadLogsResponse.from_raw(data, success, request_id, response_id, int(payload_length))
        else:
            print(f"Unhandled node response type: {message_type}")

        # Handle different types of NodeResponse based on messageType
        # ... (similar switch-case logic for different message types)

        return NodeResponse(success, request_id, response_id, payload_length)
