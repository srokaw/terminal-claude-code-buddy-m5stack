"""BLE central: connects to the M5Stack buddy and writes status messages."""
import asyncio

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

# Nordic UART Service — matches the firmware.
NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # central writes here
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # device notifies here
DEVICE_NAME = "Claude-Buddy"


class BleLink:
    """Maintains a connection to the buddy device and writes status lines."""

    def __init__(self, device_name: str = DEVICE_NAME,
                 on_device_message=None) -> None:
        self._device_name = device_name
        self._client: BleakClient | None = None
        self._last_payload: bytes | None = None
        self._on_device_message = on_device_message
        self._rx_buffer = b""

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    async def connect(self) -> bool:
        """Scan for and connect to the device. Returns True on success."""
        try:
            device = await BleakScanner.find_device_by_name(
                self._device_name, timeout=15.0)
            if device is None:
                return False
            client = BleakClient(device)
            await client.connect()
            await client.start_notify(NUS_TX, self._handle_notify)
            self._client = client
            if self._last_payload is not None:
                try:
                    await self._client.write_gatt_char(NUS_RX,
                                                       self._last_payload,
                                                       response=False)
                except (BleakError, EOFError, asyncio.TimeoutError):
                    pass
        except BleakError:
            return False
        return True

    def _handle_notify(self, _char, data: bytes) -> None:
        if self._on_device_message is None:
            return
        self._rx_buffer += data
        while b"\n" in self._rx_buffer:
            line, self._rx_buffer = self._rx_buffer.split(b"\n", 1)
            text = line.decode("utf-8", errors="ignore").strip()
            if text:
                self._on_device_message(text)

    async def send(self, payload: bytes) -> None:
        """Write a status payload. Caches it for re-send after reconnect."""
        self._last_payload = payload
        if self.connected:
            try:
                await self._client.write_gatt_char(NUS_RX, payload,
                                                   response=False)
            except (BleakError, EOFError, asyncio.TimeoutError):
                pass

    async def run_forever(self) -> None:
        """Connect, and reconnect with a fixed 5s retry whenever the link drops."""
        while True:
            if not self.connected:
                # Disconnect and release the old client before reconnecting to
                # avoid leaking the previous BleakClient connection.
                if self._client is not None:
                    try:
                        await self._client.disconnect()
                    except Exception:
                        pass
                    self._client = None
                ok = await self.connect()
                if not ok:
                    await asyncio.sleep(5.0)
                    continue
            await asyncio.sleep(2.0)
