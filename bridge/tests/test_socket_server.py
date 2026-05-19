import asyncio
import json
import os

import pytest

from buddy_bridge.state import SessionRegistry
from buddy_bridge.socket_server import apply_event, serve


def test_apply_event_start():
    reg = SessionRegistry()
    apply_event(reg, {"type": "start", "session": "s1"})
    assert reg.snapshot()["total"] == 1


def test_apply_event_state():
    reg = SessionRegistry()
    apply_event(reg, {"type": "state", "session": "s1", "state": "running"})
    assert reg.snapshot()["running"] == 1


def test_apply_event_end():
    reg = SessionRegistry()
    apply_event(reg, {"type": "start", "session": "s1"})
    apply_event(reg, {"type": "end", "session": "s1"})
    assert reg.snapshot()["total"] == 0


def test_apply_event_ignores_malformed():
    reg = SessionRegistry()
    apply_event(reg, {"nonsense": True})  # must not raise
    apply_event(reg, {"type": "state", "session": "s1"})  # missing state
    assert reg.snapshot()["total"] == 0


@pytest.mark.asyncio
async def test_serve_receives_event_over_socket(tmp_path):
    import tempfile
    # AF_UNIX path limit is 104 bytes on macOS; use a short /tmp path.
    sock_path = tempfile.mktemp(prefix="buddy_", suffix=".sock", dir="/tmp")
    reg = SessionRegistry()
    changes = []

    server = await serve(sock_path, reg, on_change=lambda: changes.append(1))
    try:
        reader, writer = await asyncio.open_unix_connection(sock_path)
        writer.write(json.dumps({"type": "start", "session": "s1"}).encode() + b"\n")
        await writer.drain()
        writer.close()
        await asyncio.sleep(0.1)
        assert reg.snapshot()["total"] == 1
        assert len(changes) == 1
    finally:
        server.close()
        await server.wait_closed()
        if os.path.exists(sock_path):
            os.unlink(sock_path)
