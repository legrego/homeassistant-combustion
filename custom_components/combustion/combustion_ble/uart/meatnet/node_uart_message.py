from .node_request import NodeRequest
from .node_response import NodeResponse


class NodeUARTMessage:
    @staticmethod
    def from_data(data):
        messages = []

        number_bytes_read = 0

        while number_bytes_read < len(data):
            bytes_to_decode = data[number_bytes_read:]

            response = NodeResponse.response_from_data(bytes_to_decode)
            if response:
                messages.append(response)
                number_bytes_read += (response.payload_length + NodeResponse.HEADER_LENGTH)

            else:
                request = NodeRequest.request_from_data(bytes_to_decode)
                if request:
                    messages.append(request)
                    number_bytes_read += (request.payload_length + NodeRequest.HEADER_LENGTH)
                else:
                    # Found invalid response or request, break out of while loop
                    break

        return messages

