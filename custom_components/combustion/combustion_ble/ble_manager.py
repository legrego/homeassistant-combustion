import asyncio

from bleak import (
    BleakClient,
    BleakGATTCharacteristic,
    BleakGATTServiceCollection,
    BleakScanner,
)
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from .ble_data.advertising_data import AdvertisingData
from .const import (
    BT_MANUFACTURER_ID,
    DEVICE_STATUS_CHARACTERISTIC,
    FW_VERSION_CHARACTERISTIC,
    HW_VERSION_CHARACTERISTIC,
    MODEL_NUMBER_CHARACTERISTIC,
    SERIAL_NUMBER_CHARACTERISTIC,
    UART_RX_CHARACTERISTIC,
    UART_TX_CHARACTERISTIC,
)
from .uart.meatnet.node_request import NodeRequest
from .uart.request import Request


class BleManagerDelegate:
    # Define delegate methods as asynchronous functions
    async def did_connect_to(self, identifier: str):
        pass

    async def did_fail_to_connect_to(self, identifier: str):
        pass

    async def did_disconnect_from(self, identifier: str):
        pass

    async def handle_bootloader_advertising(self, advertising_name: str, rssi: int, identifier: str):
        pass

    async def update_device_with_advertising(self, advertising: AdvertisingData, is_connectable: bool, rssi: int, identifier: str):
        pass

    async def update_device_with_status(self, identifier: str, status: str):
        pass

    async def handle_uart_data(self, identifier: str, data: bytes):
        pass

    async def update_device_fw_version(self, identifier: str, fw_version: str):
        pass

    async def update_device_hw_revision(self, identifier: str, hw_revision: str):
        pass

    async def update_device_serial_number(self, identifier: str, serial_number: str):
        pass

    async def update_device_model_info(self, identifier: str, model_info: str):
        pass

class BleManager:
    shared: 'BleManager' = None

    def __init__(self):
        self.clients: dict[str, BleakClient] = {}
        self.peripherals: dict[str, BLEDevice] = {}
        self.scanner: BleakScanner = None
        self.delegate: BleManagerDelegate = None
        self.device_status_characteristics: dict[str, BleakGATTCharacteristic] = {}
        self.uart_characteristics: dict[str, BleakGATTCharacteristic] = {}
        self.fw_revision_characteristics: dict[str, BleakGATTCharacteristic] = {}
        self.hw_revision_characteristics: dict[str, BleakGATTCharacteristic] = {}
        self.serial_number_characteristics: dict[str, BleakGATTCharacteristic] = {}
        self.model_number_characteristics: dict[str, BleakGATTCharacteristic] = {}

    async def init_bluetooth(self, scanner: BleakScanner, delegate: BleManagerDelegate):
        self.scanner = scanner
        self.delegate = delegate
        self.scanner.register_detection_callback(self.detection_callback)
        await self.scanner.start()

    async def detection_callback(self, device: BLEDevice, advertisement_data: AdvertisementData):

        if BT_MANUFACTURER_ID not in advertisement_data.manufacturer_data:
            return

        advertising_data = AdvertisingData.from_bleak_data(advertisement_data.manufacturer_data[BT_MANUFACTURER_ID])

        await self.delegate.update_device_with_advertising(
            advertising=advertising_data,
            is_connectable=True, # TODO: support non-connectable devices
            rssi=device.rssi,
            identifier=device.address
        )

    async def connect(self, identifier: str):
        client = BleakClient(identifier, disconnected_callback=self.disconnected_callback)
        try:
            await client.connect()
            self.clients[identifier] = client
            await self.delegate.did_connect_to(identifier)
            asyncio.ensure_future(client.get_services()).add_done_callback(self.create_service_discovery_callback(identifier))
        except Exception:
            await self.delegate.did_fail_to_connect_to(identifier)

    def disconnected_callback(self, client: BleakClient):
        self.delegate.did_disconnect_from(client.address)

    async def disconnect(self, identifier: str):
        if identifier in self.clients:
            client = self.clients[identifier]
            await client.disconnect()
            await self.delegate.did_disconnect_from(identifier)
            del self.clients[identifier]

    async def send_request(self, identifier: str, request: Request | NodeRequest):
        connection_peripheral = self.get_connected_peripheral(identifier)
        if not connection_peripheral:
            return

        uart_char = self.uart_characteristics.get(identifier)
        client = self.clients.get(identifier)
        if client and uart_char:
            client.write_gatt_char(uart_char, request.data, response=False)

    async def read_firmware_revision(self, identifier: str) -> None:
        connection_peripheral = self.get_connected_peripheral(identifier)
        if connection_peripheral:
            uart_char = self.fw_revision_characteristics.get(identifier)
            client = self.clients.get(identifier)
            if client and uart_char:
                data = await client.read_gatt_char(uart_char)
                fw_version = data.decode(encoding='utf-8')
                self.delegate.update_device_fw_version(identifier, fw_version)

    async def read_hardware_revision(self, identifier: str) -> None:
        connection_peripheral = self.get_connected_peripheral(identifier)
        if connection_peripheral:
            uart_char = self.hw_revision_characteristics.get(identifier)
            client = self.clients.get(identifier)
            if client and uart_char:
                data = await client.read_gatt_char(uart_char)
                hw_revision = data.decode(encoding='utf-8')
                self.delegate.update_device_hw_revision(identifier, hw_revision)

    async def read_serial_number(self, identifier: str) -> None:
        connection_peripheral = self.get_connected_peripheral(identifier)
        if connection_peripheral:
            uart_char = self.serial_number_characteristics.get(identifier)
            client = self.clients.get(identifier)
            if client and uart_char:
                data = await client.read_gatt_char(uart_char)
                serial_number = data.decode(encoding='utf-8')
                self.delegate.update_device_serial_number(identifier, serial_number)

    async def read_model_number(self, identifier: str) -> None:
        connection_peripheral = self.get_connected_peripheral(identifier)
        if connection_peripheral:
            uart_char = self.model_number_characteristics.get(identifier)
            client = self.clients.get(identifier)
            if client and uart_char:
                data = await client.read_gatt_char(uart_char)
                model_number = data.decode(encoding='utf-8')
                self.delegate.update_device_model_info(identifier, model_number)



    def get_connected_peripheral(self, identifier: str) -> BleakClient | None:
        # uuid = UUID(hex=identifier)
        # device_peripherals = [self.peripherals[p] for p in self.peripherals if self.peripherals.get(p).address == identifier]
        # if not device_peripherals:
        #     # print("Failed to find peripherals")
        #     return None

        connected_clients = [self.clients[c] for c in self.clients if self.clients[c].address == identifier and self.clients[c].is_connected]
        if not connected_clients:
            print("Failed to find peripherals")
            return None

        return connected_clients[0]

    def create_service_discovery_callback(self, identifier: str):
        def uart_tx_notify_callback(char: BleakGATTCharacteristic, data: bytearray):
            if char.uuid == UART_TX_CHARACTERISTIC:
                status_char = self.device_status_characteristics[identifier]
                client = self.get_connected_peripheral(identifier)
                if status_char and client:
                    client.start_notify(status_char, uart_tx_notify_callback)

                    self.send_request(identifier, request=SessionInfoRequest())


        def did_discover_services(task: asyncio.Task[BleakGATTServiceCollection]):
            for service in task.result().services.items():
                for characteristic in service[1].characteristics:
                    if characteristic.uuid == UART_RX_CHARACTERISTIC:
                        self.uart_characteristics[identifier] = characteristic
                    elif characteristic.uuid == DEVICE_STATUS_CHARACTERISTIC:
                        self.device_status_characteristics[identifier] = characteristic
                    elif characteristic.uuid == FW_VERSION_CHARACTERISTIC or characteristic.uuid == HW_VERSION_CHARACTERISTIC or characteristic.uuid == SERIAL_NUMBER_CHARACTERISTIC or characteristic.uuid == MODEL_NUMBER_CHARACTERISTIC:
                        if characteristic.uuid == FW_VERSION_CHARACTERISTIC:
                            self.fw_revision_characteristics[identifier] = characteristic
                            asyncio.ensure_future(self.read_firmware_revision(identifier))
                        elif characteristic.uuid == HW_VERSION_CHARACTERISTIC:
                            self.hw_revision_characteristics[identifier] = characteristic
                            asyncio.ensure_future(self.read_hardware_revision(identifier))
                        elif characteristic.uuid == SERIAL_NUMBER_CHARACTERISTIC:
                            self.serial_number_characteristics[identifier] = characteristic
                            asyncio.ensure_future(self.read_serial_number(identifier))
                        elif characteristic.uuid == MODEL_NUMBER_CHARACTERISTIC:
                            self.model_number_characteristics[identifier] = characteristic
                            asyncio.ensure_future(self.read_model_number(identifier))
                    elif characteristic.uuid == UART_TX_CHARACTERISTIC:
                        if characteristic.descriptors:
                            client = self.get_connected_peripheral(identifier)
                            if client:
                                client.start_notify(characteristic, uart_tx_notify_callback)

        return did_discover_services




# Instantiate the BleManager singleton
BleManager.shared = BleManager()
