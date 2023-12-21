from ..utilities.crc16ccitt import crc16ccitt
from .message_type import MessageType


class Response:
    HEADER_LENGTH = 7

    def __init__(self, success, payload_length):
        self.success = success
        self.payload_length = payload_length

    @classmethod
    def from_data(cls, data):
        responses = []
        number_bytes_read = 0

        while number_bytes_read < len(data):
            bytes_to_decode = data[number_bytes_read:]
            response = cls.response_from_data(bytes_to_decode)
            if response:
                responses.append(response)
                number_bytes_read += response.payload_length + cls.HEADER_LENGTH
            else:
                break

        return responses

    @staticmethod
    def response_from_data(data):
        # Sync bytes
        sync_bytes = data[:2]
        sync_string = ''.join(format(byte, '02x') for byte in sync_bytes)
        if sync_string != 'cafe':
            print("Response::from_data(): Missing sync bytes in response")
            return None

        # Message type
        type_byte = data[4]
        message_type = int.from_bytes(data[4])

        # Success/Fail
        success = bool(data[5])

        # Payload Length
        payload_length = data[6]

        # CRC - Implement your own CRC16-CCITT calculation
        crc = int.from_bytes(data[2:4], byteorder='little')
        crc_data_length = 3 + payload_length
        crc_data = data[4:4+crc_data_length]
        calculated_crc = crc16ccitt(crc_data)

        if crc != calculated_crc:
            print("Response::from_data(): Invalid CRC")
            return None

        response_length = payload_length + Response.HEADER_LENGTH
        if len(data) < response_length:
            return None

        # Process based on message_type
        if message_type == MessageType.LOG:
            return LogResponse.from_raw(data, success, int(payload_length))
        elif message_type == MessageType.SET_ID:
            return SetIDResponse(success, int(payload_length))
        elif message_type == MessageType.SET_COLOR:
            return SetColorResponse(success, int(payload_length))
        elif message_type == MessageType.SESSION_INFO:
            return SessionInfoResponse.from_raw(data, success, int(payload_length))
        elif message_type == MessageType.SET_PREDICTION:
            return SetPredictionResponse(success, int(payload_length))
        elif message_type == MessageType.READ_OVER_TEMPERATURE:
            return ReadOverTemperatureResponse(data, success, int(payload_length))

        return None
