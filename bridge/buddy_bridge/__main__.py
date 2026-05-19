"""Entry point: run the socket server and the BLE link together."""
import asyncio
import os

from buddy_bridge.ble_link import BleLink
from buddy_bridge.protocol import encode_status
from buddy_bridge.socket_server import serve
from buddy_bridge.state import SessionRegistry

SOCK_PATH = os.path.expanduser("~/.claude-buddy/bridge.sock")


async def main() -> None:
    reg = SessionRegistry()
    link = BleLink()

    def push() -> None:
        snap = reg.snapshot()
        payload = encode_status(snap["running"], snap["waiting"],
                                snap["total"], snap["msg"])
        asyncio.create_task(link.send(payload))

    server = await serve(SOCK_PATH, reg, on_change=push)
    print(f"[bridge] listening on {SOCK_PATH}")
    async with server:
        await asyncio.gather(server.serve_forever(), link.run_forever())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
