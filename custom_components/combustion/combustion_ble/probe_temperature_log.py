import asyncio
from datetime import datetime, timedelta

from .uart.session_info import SessionInformation


class ProbeTemperatureLog:
    ACCUMULATOR_STABILIZATION_TIME = 0.2
    ACCUMULATOR_MAX = 500

    def __init__(self, session_info: SessionInformation):
        self.session_information = session_info
        self.data_points_dict = {}
        self.data_point_accumulator = set()
        self.accumulator_timer = None
        self.start_time = None

    @property
    def data_points(self):
        return list(self.data_points_dict.values())

    def missing_range(self, sequence_range_start, sequence_range_end):
        lower_bound = None
        for search in range(sequence_range_start, sequence_range_end + 1):
            if search not in self.data_points_dict:
                lower_bound = search
                break

        if lower_bound is not None:
            upper_bound = None
            for search in reversed(range(lower_bound + 1, sequence_range_end + 1)):
                if search not in self.data_points_dict:
                    upper_bound = search
                    break

            if upper_bound is not None:
                return lower_bound, upper_bound
            else:
                return lower_bound, sequence_range_end

        return None

    def logs_in_range(self, sequence_numbers):
        sequence_range = range(sequence_numbers[0], sequence_numbers[1] + 1)
        return sum(1 for num in sequence_range if num in self.data_points_dict)

    async def insert_accumulated_data_points(self):
        added = False
        for dp in self.data_point_accumulator:
            if dp.sequence_num not in self.data_points_dict:
                self.data_points_dict[dp.sequence_num] = dp
                added = True

        if added:
            self.data_points_dict = dict(sorted(self.data_points_dict.items()))

        self.data_point_accumulator.clear()

    async def insert_data_point(self, new_data_point):
        if new_data_point in self.data_point_accumulator:
            return

        self.data_point_accumulator.add(new_data_point)

        if self.accumulator_timer:
            self.accumulator_timer.cancel()

        if len(self.data_point_accumulator) > self.ACCUMULATOR_MAX:
            await self.insert_accumulated_data_points()
        else:
            self.accumulator_timer = asyncio.create_task(self.accumulator_timer_task())

    async def accumulator_timer_task(self):
        await asyncio.sleep(self.ACCUMULATOR_STABILIZATION_TIME)
        await self.insert_accumulated_data_points()

    async def append_data_point(self, data_point):
        if not self.data_points_dict or data_point.sequence_num == max(self.data_points_dict.keys()) + 1:
            self.data_points_dict[data_point.sequence_num] = data_point
            if not self.start_time:
                self.set_start_time(data_point)
        else:
            await self.insert_data_point(data_point)

    def set_start_time(self, data_point):
        current_time = datetime.now()
        second_diff = int(data_point.sequence_num) * int(self.session_information.sample_period) // 1000
        self.start_time = current_time - timedelta(seconds=second_diff)

    @property
    def id(self):
        return self.session_information.session_id
