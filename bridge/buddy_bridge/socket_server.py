"""Unix-domain-socket server receiving hook events from Claude Code."""
import asyncio
import json
import os
from typing import Callable

from buddy_bridge.state import SessionRegistry


def apply_event(reg: SessionRegistry, event: dict) -> None:
    """Apply one hook event to the registry. Never raises on bad input."""
    etype = event.get("type")
    session = event.get("session")
    if not session:
        return
    if etype == "start":
        reg.start(session)
    elif etype == "end":
        reg.end(session)
    elif etype == "state":
        state = event.get("state")
        if state in ("idle", "running", "waiting"):
            reg.set_state(session, state)


async def serve(sock_path: str, reg: SessionRegistry,
                on_change: Callable[[], None],
                broker=None) -> asyncio.AbstractServer:
    """Start the Unix-socket server. `broker` enables permission requests."""
    if os.path.exists(sock_path):
        os.unlink(sock_path)
    os.makedirs(os.path.dirname(sock_path), exist_ok=True)

    async def handle(reader: asyncio.StreamReader,
                     writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                if not line.strip():
                    continue
                try:
                    event = json.loads(line.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue
                etype = event.get("type")
                if etype == "permission_request" and broker is not None:
                    decision = await broker.request(
                        event.get("id", ""), event.get("tool", ""),
                        event.get("detail", ""), event.get("change"))
                    writer.write(
                        (json.dumps({"decision": decision}) + "\n").encode())
                    await writer.drain()
                elif etype == "prompt_cancel" and broker is not None:
                    broker.cancel(event.get("id", ""))
                else:
                    apply_event(reg, event)
                    on_change()
        finally:
            writer.close()

    return await asyncio.start_unix_server(handle, path=sock_path)
