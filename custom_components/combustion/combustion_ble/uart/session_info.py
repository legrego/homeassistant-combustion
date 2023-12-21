"""Session Info"""
from .message_type import MessageType
from .request import Request
from .response import Response


class SessionInformation:
    def __init__(self, session_id: int, sample_period: int) -> None:
        self.session_id = session_id
        self.sample_period = sample_period

class SessionInfoRequest(Request):
    def __init__(self):
        super().__init__(payload = b'', message_type = MessageType.SESSION_INFO)


class SessionInfoResponse(Response):
    PAYLOAD_LENGTH = 6

    def __init__(self, data: bytes, success: bool, payload_length: int):
        sequence_byte_index = Response.HEADER_LENGTH
        session_id_raw = data[sequence_byte_index:sequence_byte_index + 4]
        session_id = int.from_bytes(session_id_raw, 'little')

        sample_period_raw = data[sequence_byte_index + 4:sequence_byte_index + 6]
        sample_period = int.from_bytes(sample_period_raw, 'little')

        self.info = SessionInformation(session_id, sample_period)
        super().__init__(success, payload_length)

    @staticmethod
    def from_raw(data: bytes, success: bool, payload_length: int):
        if payload_length < SessionInfoResponse.PAYLOAD_LENGTH:
            return None

        return SessionInfoResponse(data, success, payload_length)
