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
        # First line is the active message sent on promotion.
        active = await asyncio.wait_for(reader.readline(), timeout=1.0)
        assert json.loads(active) == {"type": "active", "id": "p1"}
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
async def test_active_message_sent_to_promoted_hook(tmp_path):
    """When a queued entry is promoted to the device, the bridge pushes
    {"type":"active","id":<id>} to THAT hook's socket BEFORE its decision."""
    import tempfile
    sock_path = tempfile.mktemp(prefix="buddy_", suffix=".sock", dir="/tmp")
    reg = SessionRegistry()
    broker = PermissionBroker(send_prompt=lambda *a: None,
                              send_cancel=lambda pid: None)
    server = await serve(sock_path, reg, on_change=lambda: None, broker=broker)
    try:
        # Client A: becomes active immediately.
        ra, wa = await asyncio.open_unix_connection(sock_path)
        wa.write(json.dumps({"type": "permission_request", "id": "a",
                             "session": "sa", "tool": "Bash",
                             "detail": "ls", "change": None}).encode() + b"\n")
        await wa.drain()
        await asyncio.sleep(0.05)
        assert broker.active_id == "a"

        # Client B: queues behind A.
        rb, wb = await asyncio.open_unix_connection(sock_path)
        wb.write(json.dumps({"type": "permission_request", "id": "b",
                             "session": "sb", "tool": "Bash",
                             "detail": "pwd", "change": None}).encode() + b"\n")
        await wb.drain()
        await asyncio.sleep(0.05)
        assert broker.queue_ids == ["b"]

        # A becomes active first; its own active message is delivered to A.
        line_a = await asyncio.wait_for(ra.readline(), timeout=1.0)
        assert json.loads(line_a) == {"type": "active", "id": "a"}

        # Resolve A -> B is promoted.
        broker.resolve("a", "allow")
        await asyncio.sleep(0.05)
        assert broker.active_id == "b"

        # A receives its decision.
        dec_a = await asyncio.wait_for(ra.readline(), timeout=1.0)
        assert json.loads(dec_a) == {"decision": "allow"}

        # B's FIRST line must be the active message (sent on promotion),
        # arriving before B's eventual decision line.
        line_b = await asyncio.wait_for(rb.readline(), timeout=1.0)
        assert json.loads(line_b) == {"type": "active", "id": "b"}

        broker.resolve("b", "deny")
        dec_b = await asyncio.wait_for(rb.readline(), timeout=1.0)
        assert json.loads(dec_b) == {"decision": "deny"}

        wa.close()
        wb.close()
    finally:
        server.close()
        await server.wait_closed()
        if os.path.exists(sock_path):
            os.unlink(sock_path)


@pytest.mark.asyncio
async def test_permission_request_connection_drop_cleans_up(tmp_path):
    """If the hook disconnects before any decision, no active/queued entry remains."""
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
        assert broker.active_id is None, (
            f"Expected no active entry after connection drop, got {broker.active_id}")
        assert broker.queue_ids == [], (
            f"Expected empty queue after connection drop, got {broker.queue_ids}")
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
        # First line is the active message sent on promotion.
        active = await asyncio.wait_for(reader.readline(), timeout=1.0)
        assert json.loads(active) == {"type": "active", "id": pid}
        # Send prompt_cancel on the same connection (keyboard won).
        writer.write(json.dumps({"type": "prompt_cancel", "id": pid}).encode() + b"\n")
        await writer.drain()
        # The bridge should respond with a decision (None/null from cancel,
        # meaning the hook abstains and native prompt handles it).
        line = await asyncio.wait_for(reader.readline(), timeout=1.0)
        resp = json.loads(line)
        assert "decision" in resp, (
            f"Expected decision key in response, got {resp}")
        # Broker state must be cleared (no leak).
        await asyncio.sleep(0.1)
        assert broker.active_id is None, (
            f"Expected no active entry after cancel, got {broker.active_id}")
        assert broker.queue_ids == [], (
            f"Expected empty queue after cancel, got {broker.queue_ids}")
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


@pytest.mark.asyncio
async def test_ask_request_round_trip(tmp_path):
    import tempfile
    sock = tempfile.mktemp(prefix="buddy_ask_", suffix=".sock", dir="/tmp")
    reg = SessionRegistry()
    sent_ask = []
    broker = PermissionBroker(
        send_prompt=lambda *a: None, send_cancel=lambda *a: None,
        send_ask=lambda pid, ms, qs, session="": sent_ask.append((pid, ms, qs)),
        send_ask_cancel=lambda *a: None)
    server = await serve(sock, reg, on_change=lambda: None, broker=broker)
    try:
        reader, writer = await asyncio.open_unix_connection(sock)
        req = {"type": "ask_request", "id": "k1", "multiSelect": False,
               "questions": [{"text": "?",
                              "options": [{"label": "X", "desc": ""}]}]}
        writer.write((json.dumps(req) + "\n").encode())
        await writer.drain()
        # Give the server a tick to invoke broker.ask
        for _ in range(10):
            await asyncio.sleep(0.01)
            if sent_ask:
                break
        assert sent_ask and sent_ask[0][0] == "k1"
        # First line is the active message sent on promotion.
        active = await asyncio.wait_for(reader.readline(), timeout=1.0)
        assert json.loads(active) == {"type": "active", "id": "k1"}
        broker.resolve_ask("k1", [{"label": "X"}])
        line = await asyncio.wait_for(reader.readline(), timeout=1.0)
        assert json.loads(line) == {"answers": [{"label": "X"}]}
    finally:
        server.close()
        await server.wait_closed()
        if os.path.exists(sock):
            os.unlink(sock)


@pytest.mark.asyncio
async def test_ask_cancel_from_hook(tmp_path):
    import tempfile
    sock = tempfile.mktemp(prefix="buddy_ask_", suffix=".sock", dir="/tmp")
    reg = SessionRegistry()
    cancels = []
    broker = PermissionBroker(
        send_prompt=lambda *a: None, send_cancel=lambda *a: None,
        send_ask=lambda *a: None,
        send_ask_cancel=lambda pid: cancels.append(pid))
    server = await serve(sock, reg, on_change=lambda: None, broker=broker)
    try:
        reader, writer = await asyncio.open_unix_connection(sock)
        writer.write((json.dumps({"type": "ask_request", "id": "k2",
                                  "multiSelect": False, "questions": []}) +
                      "\n").encode())
        await writer.drain()
        await asyncio.sleep(0.05)
        # First line is the active message sent on promotion.
        active = await asyncio.wait_for(reader.readline(), timeout=1.0)
        assert json.loads(active) == {"type": "active", "id": "k2"}
        writer.write((json.dumps({"type": "ask_cancel", "id": "k2"}) +
                      "\n").encode())
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=1.0)
        assert json.loads(line) == {"answers": None}
        assert cancels == ["k2"]
    finally:
        server.close()
        await server.wait_closed()
        if os.path.exists(sock):
            os.unlink(sock)
