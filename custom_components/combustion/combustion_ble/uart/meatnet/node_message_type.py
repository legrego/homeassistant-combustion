from enum import Enum


class NodeMessageType(Enum):
    SET_ID = 1
    SET_COLOR = 2
    SESSION_INFO = 3
    LOG = 4
    SET_PREDICTION = 5
    READ_OVER_TEMPERATURE = 6

    CONNECTED = 0x40
    DISCONNECTED = 0x41
    READ_NODE_LIST = 0x42
    READ_NETWORK_TOPOLOGY = 0x43
    READ_PROBE_LIST = 0x44
    PROBE_STATUS = 0x45
    PROBE_FIRMWARE_REVISION = 0x46
    PROBE_HARDWARE_REVISION = 0x47
    PROBE_MODEL_INFORMATION = 0x48
    HEARTBEAT = 0x49