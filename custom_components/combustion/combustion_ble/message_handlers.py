import asyncio
from datetime import datetime


# Structs to store when BLE message was sent and the completion handler for message
class MessageSentHandler:
    def __init__(self, time_sent: datetime, success_handler, read_over_temperature_completion_handler) -> None:
        self.time_sent = time_sent
        self.success_handler = success_handler
        self.read_over_temperature_completion_handler = read_over_temperature_completion_handler

class MessageHandlers:
    MESSAGE_TIMEOUT_SECONDS = 3

    def __init__(self):
        self.set_id_completion_handlers = {}
        self.set_color_completion_handlers = {}
        self.set_prediction_completion_handlers = {}
        self.read_over_temperature_completion_handlers = {}
        self.set_node_prediction_completion_handlers = {}

    async def check_for_timeout(self):
        while True:
            await asyncio.sleep(1)  # Check every second
            current_time = datetime.now()
            self._check_for_message_timeout(self.set_id_completion_handlers, current_time)
            self._check_for_message_timeout(self.set_color_completion_handlers, current_time)
            self._check_for_message_timeout(self.set_prediction_completion_handlers, current_time)
            self._check_for_message_timeout(self.read_over_temperature_completion_handlers, current_time)
            self._check_for_message_timeout(self.set_node_prediction_completion_handlers, current_time)

    def _check_for_message_timeout(self, handlers, current_time):
        keys_to_remove = []
        for key, value in handlers.items():
            if (current_time - value['time_sent']).total_seconds() > self.MESSAGE_TIMEOUT_SECONDS:
                if value['success_handler']:
                    value['success_handler'](False)
                if value['read_over_temperature_handler']:
                    value['read_over_temperature_handler'](False, False)
                keys_to_remove.append(key)

        for key in keys_to_remove:
            del handlers[key]

    def add_set_id_completion_handler(self, device_identifier, completion_handler):
        self.set_id_completion_handlers[device_identifier] = {
            'time_sent': datetime.now(),
            'success_handler': completion_handler,
            'read_over_temperature_handler': None
        }

    def call_set_id_completion_handler(self, identifier, success):
        handler = self.set_id_completion_handlers.get(identifier)
        if handler and handler['success_handler']:
            handler['success_handler'](success)
        self.set_id_completion_handlers.pop(identifier, None)

    def add_set_color_completion_handler(self, device_identifier, completion_handler):
        self.set_color_completion_handlers[device_identifier] = {
            'time_sent': datetime.now(),
            'success_handler': completion_handler,
            'read_over_temperature_handler': None
        }

    def call_set_color_completion_handler(self, identifier, success):
        handler = self.set_color_completion_handlers.get(identifier)
        if handler and handler['success_handler']:
            handler['success_handler'](success)
        self.set_color_completion_handlers.pop(identifier, None)

    def add_set_prediction_completion_handler(self, device_identifier, completion_handler):
        self.set_prediction_completion_handlers[device_identifier] = {
            'time_sent': datetime.now(),
            'success_handler': completion_handler,
            'read_over_temperature_handler': None
        }

    def call_set_prediction_completion_handler(self, identifier, success):
        handler = self.set_prediction_completion_handlers.get(identifier)
        if handler and handler['success_handler']:
            handler['success_handler'](success)
        self.set_prediction_completion_handlers.pop(identifier, None)

    def add_read_over_temperature_completion_handler(self, device_identifier, completion_handler):
        self.read_over_temperature_completion_handlers[device_identifier] = {
            'time_sent': datetime.now(),
            'success_handler': None,
            'read_over_temperature_handler': completion_handler
        }

    def call_read_over_temperature_completion_handler(self, identifier, success, over_temperature):
        handler = self.read_over_temperature_completion_handlers.get(identifier)
        if handler and handler['read_over_temperature_handler']:
            handler['read_over_temperature_handler'](success, over_temperature)
        self.read_over_temperature_completion_handlers.pop(identifier, None)

    def add_node_set_prediction_completion_handler(self, device_identifier, completion_handler):
        self.set_node_prediction_completion_handlers[device_identifier] = {
            'time_sent': datetime.now(),
            'success_handler': completion_handler,
            'read_over_temperature_handler': None
        }

    def call_node_set_prediction_completion_handler(self, identifier, success):
        handler = self.set_node_prediction_completion_handlers.get(identifier)
        if handler and handler['success_handler']:
            handler['success_handler'](success)
        self.set_node_prediction_completion_handlers.pop(identifier, None)

