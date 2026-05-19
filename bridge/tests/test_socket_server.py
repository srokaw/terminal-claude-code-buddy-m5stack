import asyncio
import json
import os

import pytest

from buddy_bridge.state import SessionRegistry
from buddy_bridge.socket_server import apply_event, serve
from buddy_bridge.permissions import PermissionBroker


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


@pytest.mark.asyncio
async def test_permission_request_gets_decision_response(tmp_path):
    import asyncio, json, os
    sock_path = "/tmp/buddy_perm_test.sock"
    reg = SessionRegistry()
    broker = PermissionBroker(send_prompt=lambda *a: None,
                              send_cancel=lambda pid: None)
    server = await serve(sock_path, reg, on_change=lambda: None, broker=broker)
    try:
        reader, writer = await asyncio.open_unix_connection(sock_path)
        writer.write(json.dumps({"type": "permission_request", "id": "p1",
                                 "session": "s1", "tool": "Bash",
                                 "detail": "ls", "change": None}).encode() + b"\n")
        await writer.drain()
        await asyncio.sleep(0.05)
        broker.resolve("p1", "allow")
        line = await asyncio.wait_for(reader.readline(), timeout=1.0)
        assert json.loads(line) == {"decision": "allow"}
        writer.close()
    finally:
        server.close()
        await server.wait_closed()
        if os.path.exists(sock_path):
            os.unlink(sock_path)


@pytest.mark.asyncio
async def test_permission_request_connection_drop_cleans_up(tmp_path):
    """If the hook disconnects before any decision, broker._pending must be empty."""
    import tempfile
    sock_path = tempfile.mktemp(prefix="buddy_", suffix=".sock", dir="/tmp")
    reg = SessionRegistry()
    broker = PermissionBroker(send_prompt=lambda *a: None,
                              send_cancel=lambda pid: None)
    server = await serve(sock_path, reg, on_change=lambda: None, broker=broker)
    try:
        reader, writer = await asyncio.open_unix_connection(sock_path)
        writer.write(json.dumps({"type": "permission_request", "id": "p99",
                                 "session": "s1", "tool": "Bash",
                                 "detail": "ls", "change": None}).encode() + b"\n")
        await writer.drain()
        await asyncio.sleep(0.05)
        # Close the connection without sending a decision.
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        await asyncio.sleep(0.15)
        assert broker._pending == {}, (
            f"Expected empty _pending after connection drop, got {broker._pending}")
    finally:
        server.close()
        await server.wait_closed()
        if os.path.exists(sock_path):
            os.unlink(sock_path)


@pytest.mark.asyncio
async def test_prompt_cancel_on_same_connection_resolves(tmp_path):
    """prompt_cancel sent on the SAME connection must resolve the pending request."""
    import tempfile
    sock_path = tempfile.mktemp(prefix="buddy_", suffix=".sock", dir="/tmp")
    reg = SessionRegistry()
    broker = PermissionBroker(send_prompt=lambda *a: None,
                              send_cancel=lambda pid: None)
    server = await serve(sock_path, reg, on_change=lambda: None, broker=broker)
    try:
        reader, writer = await asyncio.open_unix_connection(sock_path)
        pid = "pcancel1"
        writer.write(json.dumps({"type": "permission_request", "id": pid,
                                 "session": "s1", "tool": "Bash",
                                 "detail": "rm -rf /", "change": None}).encode() + b"\n")
        await writer.drain()
        await asyncio.sleep(0.05)
        # Send prompt_cancel on the same connection (keyboard won).
        writer.write(json.dumps({"type": "prompt_cancel", "id": pid}).encode() + b"\n")
        await writer.drain()
        # The bridge should respond with a decision (deny from cancel).
        line = await asyncio.wait_for(reader.readline(), timeout=1.0)
        resp = json.loads(line)
        assert resp.get("decision") in ("allow", "deny"), (
            f"Expected allow/deny decision, got {resp}")
        # Broker future must be resolved (no leak).
        await asyncio.sleep(0.1)
        assert broker._pending == {}, (
            f"Expected empty _pending after cancel, got {broker._pending}")
        writer.close()
    finally:
        server.close()
        await server.wait_closed()
        if os.path.exists(sock_path):
            os.unlink(sock_path)


@pytest.mark.asyncio
async def test_serve_survives_garbage_then_valid_event(tmp_path):
    """Server must stay alive after receiving non-JSON garbage; a subsequent
    valid event from a new connection must still be applied."""
    import tempfile
    sock_path = tempfile.mktemp(prefix="buddy_", suffix=".sock", dir="/tmp")
    reg = SessionRegistry()
    changes = []

    server = await serve(sock_path, reg, on_change=lambda: changes.append(1))
    try:
        # Send non-JSON garbage bytes in first connection.
        r1, w1 = await asyncio.open_unix_connection(sock_path)
        w1.write(b"\xff\xfe not json at all\n")
        await w1.drain()
        w1.close()
        await asyncio.sleep(0.1)

        # Server must still be running; send a valid event in a new connection.
        r2, w2 = await asyncio.open_unix_connection(sock_path)
        w2.write(json.dumps({"type": "start", "session": "s2"}).encode() + b"\n")
        await w2.drain()
        w2.close()
        await asyncio.sleep(0.1)

        assert reg.snapshot()["total"] == 1
        assert len(changes) == 1
    finally:
        server.close()
        await server.wait_closed()
        if os.path.exists(sock_path):
            os.unlink(sock_path)
