from datetime import datetime
from typing import Any

from .exceptions import DFUNotImplementedError


class DFUDeviceType:
    THERMOMETER = 'thermometer'
    DISPLAY = 'display'
    CHARGER = 'charger'
    UNKNOWN = 'unknown'

class DFUManager:
    shared = None

    class DFU:
        def __init__(self, unique_identifier: str, firmware: Any, started_at: datetime):
            self.unique_identifier = unique_identifier
            self.firmware = firmware
            self.started_at = started_at

    def __init__(self):
        self.running_dfus = {}  # Dictionary of currently active DFUs
        self.default_firmware = {}  # Default firmware for each device type
        DFUManager.shared = self

    @staticmethod
    def set_default_dfu_for_type(dfu_file, dfu_type):
        raise DFUNotImplementedError()

    def unique_identifier_from(self, advertising_name):
        raise DFUNotImplementedError()

    @staticmethod
    def bootloader_type_from(advertising_name: str):
        if Constants.THERMOMETER_DFU_NAME in advertising_name:
            return DFUDeviceType.THERMOMETER
        if Constants.DISPLAY_DFU_NAME in advertising_name:
            return DFUDeviceType.DISPLAY
        if Constants.CHARGER_DFU_NAME in advertising_name:
            return DFUDeviceType.CHARGER
        return DFUDeviceType.UNKNOWN

class Constants:
    THERMOMETER_DFU_NAME = "Thermom_DFU_"
    DISPLAY_DFU_NAME = "Display_DFU_"
    CHARGER_DFU_NAME = "Charger_DFU_"
    RETRY_TIME_DELAY = 10  # seconds
