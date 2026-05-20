"""Entry point: socket server + BLE link + permission broker."""
import asyncio
import os

from buddy_bridge.ble_link import BleLink
from buddy_bridge.permissions import PermissionBroker
from buddy_bridge.protocol import (
    decode_device_message, encode_ask_cancel, encode_ask_request,
    encode_auto_fired, encode_prompt, encode_prompt_cancel, encode_status)
from buddy_bridge.socket_server import serve
from buddy_bridge.state import SessionRegistry

# SOCK_PATH is a cross-process contract — must match hooks/buddy-hook.py
# and hooks/buddy-permission-hook.py.
SOCK_PATH = os.path.expanduser("~/.claude-buddy/bridge.sock")


async def main() -> None:
    reg = SessionRegistry()
    _pending: set[asyncio.Task] = set()

    # Broker must be constructed before on_device_message (references it), but
    # send_prompt/send_cancel/send_auto_fired reference link — define them as
    # closures after link is created.  We forward-declare broker here and wire
    # the callbacks in the same block as link construction below.

    # Define message / disconnect callbacks first so they can be passed to BleLink.
    broker_ref: list = []  # single-element list used as a mutable cell

    def on_device_message(text: str) -> None:
        broker = broker_ref[0]
        msg = decode_device_message(text)
        if msg is None:
            return
        if msg["cmd"] == "permission":
            broker.resolve(msg["id"], msg["decision"])
        elif msg["cmd"] == "auto":
            broker.set_auto_approve(msg["state"])
        elif msg["cmd"] == "prompt_busy":
            broker.cancel(msg["id"])  # firmware busy -> hook yields to native
        elif msg["cmd"] == "ask_answer":
            broker.resolve_ask(msg["id"], msg["answers"])

    def on_disconnect() -> None:
        broker = broker_ref[0]
        broker.set_auto_approve(False)  # safe default until device re-confirms

    link = BleLink(on_device_message=on_device_message, on_disconnect=on_disconnect)

    def spawn(coro) -> None:
        t = asyncio.create_task(coro)
        _pending.add(t)
        t.add_done_callback(_pending.discard)

    def send_prompt(pid: str, tool: str, detail: str, change) -> None:
        spawn(link.send(encode_prompt(pid, tool, detail, change)))

    def send_cancel(pid: str) -> None:
        spawn(link.send(encode_prompt_cancel(pid)))

    def send_auto_fired(tool: str) -> None:
        spawn(link.send(encode_auto_fired(tool)))

    def send_ask(aid: str, multi_select: bool, questions: list) -> None:
        spawn(link.send(encode_ask_request(aid, multi_select, questions)))

    def send_ask_cancel(aid: str) -> None:
        spawn(link.send(encode_ask_cancel(aid)))

    broker = PermissionBroker(send_prompt=send_prompt, send_cancel=send_cancel,
                              send_auto_fired=send_auto_fired,
                              send_ask=send_ask, send_ask_cancel=send_ask_cancel)
    broker_ref.append(broker)

    def push() -> None:
        snap = reg.snapshot()
        spawn(link.send(encode_status(snap["running"], snap["waiting"],
                                      snap["total"], snap["msg"]),
                        replayable=True))

    server = await serve(SOCK_PATH, reg, on_change=push, broker=broker)
    print(f"[bridge] listening on {SOCK_PATH}")
    async with server:
        await asyncio.gather(server.serve_forever(), link.run_forever())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
