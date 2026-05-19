"""Entry point: run the socket server and the BLE link together."""
import asyncio
import os

from buddy_bridge.ble_link import BleLink
from buddy_bridge.protocol import encode_status
from buddy_bridge.socket_server import serve
from buddy_bridge.state import SessionRegistry

# Cross-process contract: this path MUST match the SOCK_PATH in
# hooks/buddy-hook.py — both files use the same socket.
SOCK_PATH = os.path.expanduser("~/.claude-buddy/bridge.sock")

# Strong references to in-flight send tasks so the GC cannot collect them
# before they complete.
_pending: set[asyncio.Task] = set()


async def main() -> None:
    reg = SessionRegistry()
    link = BleLink()

    def push() -> None:
        snap = reg.snapshot()
        payload = encode_status(snap["running"], snap["waiting"],
                                snap["total"], snap["msg"])
        t = asyncio.create_task(link.send(payload))
        _pending.add(t)
        t.add_done_callback(_pending.discard)

    server = await serve(SOCK_PATH, reg, on_change=push)
    print(f"[bridge] listening on {SOCK_PATH}")
    async with server:
        await asyncio.gather(server.serve_forever(), link.run_forever())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
