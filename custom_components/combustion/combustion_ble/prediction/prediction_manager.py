import asyncio
from collections.abc import Callable

from ..ble_data.prediction_state import PredictionState
from ..ble_data.prediction_status import PredictionStatus
from .prediction_info import PredictionInfo


class PredictionManager:
    PREDICTION_STALE_TIMEOUT = 15  # seconds
    MAX_PREDICTION_TIME = 60 * 60 * 4  # seconds
    PREDICTION_TIME_UPDATE_COUNT = 3
    LOW_RESOLUTION_CUTOFF_SECONDS = 60 * 5  # seconds
    LOW_RESOLUTION_PRECISION_SECONDS = 15  # seconds
    LINEARIZATION_UPDATE_RATE_MS = 200.0  # milliseconds
    PREDICTION_STATUS_RATE_MS = 5000.0  # milliseconds

    def __init__(self):
        self.previous_prediction_info: PredictionInfo = None
        self.previous_sequence_number = None
        self.linearization_target_seconds = 0
        self.linearization_timer_update_value = 0
        self.current_linearization_ms = 0
        self.running_linearization = False
        self.stale_timer_task: asyncio.Task = None
        self.linearization_timer_task: asyncio.Task = None
        self.listeners: list[Callable[[PredictionInfo], None]] = []

    def add_update_listener(self, listener: Callable[[PredictionInfo], None]):
        self.listeners.append(listener)

    async def update_prediction_status(self, prediction_status: PredictionStatus, sequence_number: int):
        if self.previous_sequence_number is not None:
            if (self.previous_sequence_number == sequence_number and
                prediction_status.prediction_set_point_temperature == self.previous_prediction_info.prediction_set_point_temperature):
                return

        self.clear_linearization_timer()

        prediction_info = self.info_from_status(prediction_status, sequence_number)
        self.previous_sequence_number = sequence_number
        self.publish_prediction_info(prediction_info)

        if self.stale_timer_task is not None:
            self.stale_timer_task.cancel()

        self.stale_timer_task = asyncio.create_task(self.start_stale_timer())

    async def start_stale_timer(self):
        await asyncio.sleep(self.PREDICTION_STALE_TIMEOUT)
        self.clear_linearization_timer()

    def info_from_status(self, prediction_status: PredictionStatus, sequence_number: int):
        if prediction_status is None:
            return None

        seconds_remaining = self.seconds_remaining(prediction_status, sequence_number)
        return PredictionInfo(
            prediction_state=prediction_status.prediction_state,
            prediction_mode=prediction_status.prediction_mode,
            prediction_type=prediction_status.prediction_type,
            prediction_set_point_temperature=prediction_status.prediction_set_point_temperature,
            estimated_core_temperature=prediction_status.estimated_core_temperature,
            seconds_remaining=seconds_remaining,
            percent_through_cook=self.percent_through_cook(prediction_status)
        )

    def seconds_remaining(self, prediction_status: PredictionStatus, sequence_number: int):
        if prediction_status.prediction_state != PredictionState.PREDICTING:
            return None

        if prediction_status.prediction_value_seconds > self.MAX_PREDICTION_TIME:
            return None

        previous_seconds_remaining = self.previous_prediction_info.seconds_remaining if self.previous_prediction_info else None

        if prediction_status.prediction_value_seconds > self.LOW_RESOLUTION_CUTOFF_SECONDS:
            self.running_linearization = False

            if previous_seconds_remaining is None or (sequence_number % self.PREDICTION_TIME_UPDATE_COUNT) == 0:
                remainder = prediction_status.prediction_value_seconds % self.LOW_RESOLUTION_PRECISION_SECONDS
                if remainder > (self.LOW_RESOLUTION_PRECISION_SECONDS / 2):
                    return prediction_status.prediction_value_seconds + (self.LOW_RESOLUTION_PRECISION_SECONDS - remainder)
                else:
                    return prediction_status.prediction_value_seconds - remainder
            else:
                return previous_seconds_remaining
        else:
            prediction_update_rate_seconds = int(self.PREDICTION_STATUS_RATE_MS / 1000.0)
            if prediction_status.prediction_value_seconds < prediction_update_rate_seconds:
                self.linearization_target_seconds = 0
            else:
                self.linearization_target_seconds = prediction_status.prediction_value_seconds - prediction_update_rate_seconds

            if not self.running_linearization:
                self.current_linearization_ms = float(prediction_status.prediction_value_seconds) * 1000.0
                self.linearization_timer_update_value = self.LINEARIZATION_UPDATE_RATE_MS
            else:
                interval_count = self.PREDICTION_STATUS_RATE_MS / self.LINEARIZATION_UPDATE_RATE_MS
                self.linearization_timer_update_value = (self.current_linearization_ms - (self.linearization_target_seconds * 1000.0)) / interval_count

            if self.linearization_timer_task is not None:
                self.linearization_timer_task.cancel()

            self.linearization_timer_task = asyncio.create_task(self.update_prediction_seconds())
            self.running_linearization = True

            return int(self.current_linearization_ms / 1000.0)

    async def update_prediction_seconds(self):
        while self.running_linearization:
            await asyncio.sleep(self.LINEARIZATION_UPDATE_RATE_MS / 1000)
            if self.previous_prediction_info is None:
                break

            self.current_linearization_ms -= self.linearization_timer_update_value
            self.current_linearization_ms = max(0.0, self.current_linearization_ms)

            seconds_remaining = int(self.current_linearization_ms / 1000.0)
            info = PredictionInfo(
                predictionState=self.previous_prediction_info.prediction_state,
                predictionMode=self.previous_prediction_info.prediction_mode,
                predictionType=self.previous_prediction_info.prediction_type,
                predictionSetPointTemperature=self.previous_prediction_info.prediction_set_point_temperature,
                estimatedCoreTemperature=self.previous_prediction_info.estimated_core_temperature,
                secondsRemaining=seconds_remaining,
                percentThroughCook=self.previous_prediction_info.percent_through_cook
            )
            self.publish_prediction_info(info)

    def percent_through_cook(self, prediction_status: PredictionStatus):
        start = prediction_status.heat_start_temperature
        end = prediction_status.prediction_set_point_temperature
        core = prediction_status.estimated_core_temperature

        if core > end:
            return 100
        if start > core:
            return 0
        if end == start:
            return 100

        return int(((core - start) / (end - start)) * 100.0)

    def publish_prediction_info(self, prediction_info: PredictionInfo):
        self.previous_prediction_info = prediction_info
        for listener in self.listeners:
            listener(prediction_info)

    def clear_linearization_timer(self):
        if self.linearization_timer_task is not None:
            self.linearization_timer_task.cancel()
            self.linearization_timer_task = None
