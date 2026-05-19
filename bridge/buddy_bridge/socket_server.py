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
                on_change: Callable[[], None]) -> asyncio.AbstractServer:
    """Start the Unix-socket server. Returns the asyncio server object."""
    if os.path.exists(sock_path):
        os.unlink(sock_path)
    os.makedirs(os.path.dirname(sock_path), exist_ok=True)

    async def handle(reader: asyncio.StreamReader,
                     writer: asyncio.StreamWriter) -> None:
        try:
            data = await reader.read(4096)
            for line in data.splitlines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue
                apply_event(reg, event)
                on_change()
        finally:
            writer.close()

    return await asyncio.start_unix_server(handle, path=sock_path)
