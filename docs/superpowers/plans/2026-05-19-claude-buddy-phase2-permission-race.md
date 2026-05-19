# Claude Buddy — Phase 2: Permission Race — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Approve or deny Claude Code tool-permission prompts from the M5Stack — racing the device buttons against a terminal keyboard prompt, with a device-side auto-approve toggle.

**Architecture:** A `PreToolUse` hook intercepts a tool call and opens two concurrent inputs — a `/dev/tty` raw-mode keyboard prompt and a request to the bridge (which relays the prompt to the device over BLE and waits for a button press). First responder wins; the loser side is cleared. A `PermissionBroker` in the bridge tracks pending requests, matches device decisions to them, and handles the auto-approve mode. The device gains 3-button input, a permission-takeover screen, and a TX-notify path back to the bridge.

**Tech Stack:** Python 3 + `asyncio` + `bleak` (bridge); PlatformIO + Arduino + `M5Unified` + `ArduinoJson` (firmware); Claude Code `PreToolUse` hook.

**Reference:** Design spec `docs/superpowers/specs/2026-05-18-claude-buddy-m5stack-design.md`. Builds on Phase 1 (`docs/superpowers/plans/2026-05-19-claude-buddy-phase1-status-display.md`), which is merged/PR'd and provides `protocol.py`, `state.py`, `socket_server.py`, `ble_link.py`, `__main__.py`, `hooks/buddy-hook.py`, and the firmware.

**Prerequisite already done:** NUS characteristics require an encrypted link (commit `849d063`). Task 11 verifies it end-to-end; do not re-implement it.

**Privacy (load-bearing):** The bridge sends the device the **complete pending tool call** untruncated (full command / file path / URL) but **never** file contents, diff bodies, or conversation text. For `Edit`/`Write` the device gets the path plus a change *size* only. See the spec's Privacy section.

---

## File Structure

```
bridge/buddy_bridge/
  protocol.py          MODIFY — add encode_prompt, encode_prompt_cancel, decode_device_message
  permissions.py       CREATE — PermissionBroker: pending requests, decision matching, auto-approve
  socket_server.py     MODIFY — handle permission_request (request/response) + prompt_cancel
  ble_link.py          MODIFY — subscribe to NUS TX notifications; on_device_message callback
  __main__.py          MODIFY — wire PermissionBroker; route device messages
hooks/
  buddy-permission-hook.py   CREATE — PreToolUse: detail builder + device/keyboard race
bridge/tests/
  test_protocol.py     MODIFY — prompt encoding + device-message decoding
  test_permissions.py  CREATE
  test_socket_server.py MODIFY — permission_request flow
  test_permission_hook.py CREATE — detail builder + decision-output shaping
firmware/src/main.cpp  MODIFY — buttons, prompt screen, decision send, auto-approve
```

Interfaces locked here (used across tasks):

- `protocol.encode_prompt(prompt_id:str, tool:str, detail:str, change:str|None=None) -> bytes`
  → `{"evt":"prompt","id":..,"tool":..,"detail":..,"change":..}` (`change` key omitted when `None`)
- `protocol.encode_prompt_cancel(prompt_id:str) -> bytes` → `{"cmd":"prompt_cancel","id":..}`
- `protocol.decode_device_message(line:str) -> dict|None`
  — parses `{"cmd":"permission","id":..,"decision":"allow"|"deny"}` and `{"cmd":"auto","state":bool}`; returns the dict, or `None` if unrecognized/malformed
- `PermissionBroker(send_prompt, send_cancel)` where `send_prompt(prompt_id,tool,detail,change)` and `send_cancel(prompt_id)` are sync callbacks
  - `await broker.request(prompt_id, tool, detail, change) -> "allow"|"deny"`
  - `broker.resolve(prompt_id, decision)` — a device decision arrived
  - `broker.cancel(prompt_id)` — the keyboard won; stop waiting
  - `broker.set_auto_approve(state:bool)`; `broker.auto_approve` (read-only)
- Hook→bridge socket request: `{"type":"permission_request","id":..,"session":..,"tool":..,"detail":..,"change":..}`; bridge replies with one line `{"decision":"allow"|"deny"}`. Hook may also send `{"type":"prompt_cancel","id":..}` on the same connection.
- Device→bridge (NUS TX notify): `{"cmd":"permission","id":..,"decision":..}`, `{"cmd":"auto","state":..}`
- `PreToolUse` hook output JSON: `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow"|"deny","permissionDecisionReason":".."}}`

---

## Task 1: `protocol.py` — prompt encoding and device-message decoding

**Files:**
- Modify: `bridge/buddy_bridge/protocol.py`
- Test: `bridge/tests/test_protocol.py`

- [ ] **Step 1: Write the failing tests**

Append to `bridge/tests/test_protocol.py`:

```python
from buddy_bridge.protocol import (
    encode_prompt, encode_prompt_cancel, decode_device_message)


def test_encode_prompt_without_change():
    obj = json.loads(encode_prompt("p1", "Bash", "ls -la").decode())
    assert obj == {"evt": "prompt", "id": "p1", "tool": "Bash", "detail": "ls -la"}


def test_encode_prompt_with_change():
    obj = json.loads(encode_prompt("p1", "Edit", "/tmp/x.py", "+3/-1").decode())
    assert obj["change"] == "+3/-1"
    assert obj["evt"] == "prompt"


def test_encode_prompt_cancel():
    obj = json.loads(encode_prompt_cancel("p1").decode())
    assert obj == {"cmd": "prompt_cancel", "id": "p1"}


def test_decode_permission_decision():
    out = decode_device_message('{"cmd":"permission","id":"p1","decision":"allow"}')
    assert out == {"cmd": "permission", "id": "p1", "decision": "allow"}


def test_decode_auto_toggle():
    out = decode_device_message('{"cmd":"auto","state":true}')
    assert out == {"cmd": "auto", "state": True}


def test_decode_rejects_bad_decision():
    assert decode_device_message('{"cmd":"permission","id":"p1","decision":"maybe"}') is None


def test_decode_rejects_garbage():
    assert decode_device_message("not json") is None
    assert decode_device_message('{"cmd":"unknown"}') is None
```

- [ ] **Step 2: Run to verify failure**

Run: `cd bridge && .venv/bin/python -m pytest tests/test_protocol.py -v`
Expected: FAIL — `ImportError` for `encode_prompt`.

- [ ] **Step 3: Implement**

Append to `bridge/buddy_bridge/protocol.py`:

```python
def encode_prompt(prompt_id: str, tool: str, detail: str,
                  change: str | None = None) -> bytes:
    """Encode a pending permission prompt for the device.

    `detail` is the complete tool call (full command / path / URL). Never
    file contents or diff bodies — see the spec's Privacy section.
    """
    obj = {"evt": "prompt", "id": prompt_id, "tool": tool, "detail": detail}
    if change is not None:
        obj["change"] = change
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")


def encode_prompt_cancel(prompt_id: str) -> bytes:
    """Tell the device to clear a prompt resolved on the keyboard."""
    obj = {"cmd": "prompt_cancel", "id": prompt_id}
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")


def decode_device_message(line: str) -> dict | None:
    """Parse one device->bridge message. Returns the dict or None if invalid."""
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    cmd = obj.get("cmd")
    if cmd == "permission":
        if obj.get("decision") in ("allow", "deny") and obj.get("id"):
            return {"cmd": "permission", "id": obj["id"],
                    "decision": obj["decision"]}
        return None
    if cmd == "auto":
        if isinstance(obj.get("state"), bool):
            return {"cmd": "auto", "state": obj["state"]}
        return None
    return None
```

- [ ] **Step 4: Run to verify pass**

Run: `cd bridge && .venv/bin/python -m pytest tests/test_protocol.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/buddy_bridge/protocol.py bridge/tests/test_protocol.py
git commit -m "feat(bridge): prompt encoding and device-message decoding"
```

---

## Task 2: `permissions.py` — `PermissionBroker`

**Files:**
- Create: `bridge/buddy_bridge/permissions.py`
- Test: `bridge/tests/test_permissions.py`

The broker owns one pending request at a time per `prompt_id`. `request()` either auto-approves immediately, or sends the prompt to the device and awaits a decision. The decision is delivered by `resolve()` (device button) or pre-empted by `cancel()` (keyboard won).

- [ ] **Step 1: Write the failing tests**

`bridge/tests/test_permissions.py`:

```python
import asyncio
import pytest

from buddy_bridge.permissions import PermissionBroker


def make_broker():
    sent, cancelled = [], []
    broker = PermissionBroker(
        send_prompt=lambda *a: sent.append(a),
        send_cancel=lambda pid: cancelled.append(pid))
    return broker, sent, cancelled


@pytest.mark.asyncio
async def test_auto_approve_returns_allow_immediately():
    broker, sent, _ = make_broker()
    broker.set_auto_approve(True)
    decision = await broker.request("p1", "Bash", "ls", None)
    assert decision == "allow"
    assert sent == []  # device never prompted in auto mode


@pytest.mark.asyncio
async def test_request_sends_prompt_and_awaits_resolve():
    broker, sent, _ = make_broker()
    task = asyncio.create_task(broker.request("p1", "Bash", "ls -la", None))
    await asyncio.sleep(0.05)
    assert sent == [("p1", "Bash", "ls -la", None)]
    broker.resolve("p1", "deny")
    assert await task == "deny"


@pytest.mark.asyncio
async def test_cancel_makes_request_return_deny():
    broker, _, cancelled = make_broker()
    task = asyncio.create_task(broker.request("p1", "Bash", "ls", None))
    await asyncio.sleep(0.05)
    broker.cancel("p1")
    # keyboard won; the bridge side resolves to "deny" so it stops waiting
    assert await task == "deny"
    assert cancelled == ["p1"]


@pytest.mark.asyncio
async def test_resolve_unknown_id_is_safe():
    broker, _, _ = make_broker()
    broker.resolve("never", "allow")  # must not raise


def test_auto_approve_default_off():
    broker, _, _ = make_broker()
    assert broker.auto_approve is False
```

- [ ] **Step 2: Run to verify failure**

Run: `cd bridge && .venv/bin/python -m pytest tests/test_permissions.py -v`
Expected: FAIL — no module `buddy_bridge.permissions`.

- [ ] **Step 3: Implement**

`bridge/buddy_bridge/permissions.py`:

```python
"""Tracks pending permission requests and matches device decisions to them."""
import asyncio
from typing import Callable


class PermissionBroker:
    """One pending request per prompt id. request() awaits a decision."""

    def __init__(self,
                 send_prompt: Callable[[str, str, str, str | None], None],
                 send_cancel: Callable[[str], None]) -> None:
        self._send_prompt = send_prompt
        self._send_cancel = send_cancel
        self._pending: dict[str, asyncio.Future] = {}
        self._auto_approve = False

    @property
    def auto_approve(self) -> bool:
        return self._auto_approve

    def set_auto_approve(self, state: bool) -> None:
        self._auto_approve = state

    async def request(self, prompt_id: str, tool: str, detail: str,
                      change: str | None) -> str:
        """Return 'allow' or 'deny'. Auto-approve short-circuits to 'allow'."""
        if self._auto_approve:
            return "allow"
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[prompt_id] = fut
        self._send_prompt(prompt_id, tool, detail, change)
        try:
            return await fut
        finally:
            self._pending.pop(prompt_id, None)

    def resolve(self, prompt_id: str, decision: str) -> None:
        """A device decision arrived for prompt_id."""
        fut = self._pending.get(prompt_id)
        if fut is not None and not fut.done():
            fut.set_result(decision)

    def cancel(self, prompt_id: str) -> None:
        """The keyboard answered first; stop waiting and clear the device."""
        fut = self._pending.get(prompt_id)
        if fut is not None and not fut.done():
            self._send_cancel(prompt_id)
            fut.set_result("deny")  # bridge side stops waiting; hook owns the
                                    # real decision it already returned
```

- [ ] **Step 4: Run to verify pass**

Run: `cd bridge && .venv/bin/python -m pytest tests/test_permissions.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/buddy_bridge/permissions.py bridge/tests/test_permissions.py
git commit -m "feat(bridge): PermissionBroker for pending permission requests"
```

---

## Task 3: `socket_server.py` — permission request/response path

**Files:**
- Modify: `bridge/buddy_bridge/socket_server.py`
- Test: `bridge/tests/test_socket_server.py`

Phase 1's handler reads fire-and-forget event lines. Phase 2 adds: when a line is a `permission_request`, call `broker.request(...)`, await the decision, and write `{"decision":..}` back on the *same* connection. A later `prompt_cancel` line on that connection calls `broker.cancel(...)`.

- [ ] **Step 1: Write the failing test**

Append to `bridge/tests/test_socket_server.py`:

```python
import pytest
from buddy_bridge.permissions import PermissionBroker


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
```

- [ ] **Step 2: Run to verify failure**

Run: `cd bridge && .venv/bin/python -m pytest tests/test_socket_server.py::test_permission_request_gets_decision_response -v`
Expected: FAIL — `serve()` has no `broker` parameter.

- [ ] **Step 3: Implement**

Replace the `serve` function in `bridge/buddy_bridge/socket_server.py` with:

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `cd bridge && .venv/bin/python -m pytest tests/test_socket_server.py -v`
Expected: all PASS (Phase 1 tests still green — `serve`'s `broker` defaults to `None`).

- [ ] **Step 5: Commit**

```bash
git add bridge/buddy_bridge/socket_server.py bridge/tests/test_socket_server.py
git commit -m "feat(bridge): permission request/response over the hook socket"
```

---

## Task 4: `ble_link.py` — subscribe to device notifications

**Files:**
- Modify: `bridge/buddy_bridge/ble_link.py`

The device sends decisions and auto-toggle state as notifications on the NUS TX characteristic. Subscribe on connect and forward each line to a callback.

- [ ] **Step 1: Implement**

In `bridge/buddy_bridge/ble_link.py`, add the TX UUID constant next to `NUS_RX`:

```python
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # device notifies here
```

Change `__init__` to accept a message callback:

```python
    def __init__(self, device_name: str = DEVICE_NAME,
                 on_device_message=None) -> None:
        self._device_name = device_name
        self._client: BleakClient | None = None
        self._last_payload: bytes | None = None
        self._on_device_message = on_device_message
        self._rx_buffer = b""
```

In `connect()`, after `await client.connect()` and before caching/replay, subscribe to TX:

```python
        await client.start_notify(NUS_TX, self._handle_notify)
```

Add the notification handler (device messages are `\n`-delimited JSON; buffer partial lines):

```python
    def _handle_notify(self, _char, data: bytes) -> None:
        if self._on_device_message is None:
            return
        self._rx_buffer += data
        while b"\n" in self._rx_buffer:
            line, self._rx_buffer = self._rx_buffer.split(b"\n", 1)
            text = line.decode("utf-8", errors="ignore").strip()
            if text:
                self._on_device_message(text)
```

- [ ] **Step 2: Verify it imports and Phase 1 tests still pass**

Run: `cd bridge && .venv/bin/python -c "from buddy_bridge.ble_link import BleLink, NUS_TX; print(NUS_TX)"`
Expected: prints the TX UUID, no import error.
Run: `cd bridge && .venv/bin/python -m pytest tests/ -v`
Expected: all existing tests PASS.

- [ ] **Step 3: Commit**

```bash
git add bridge/buddy_bridge/ble_link.py
git commit -m "feat(bridge): subscribe to NUS TX notifications from the device"
```

---

## Task 5: `__main__.py` — wire the broker and route device messages

**Files:**
- Modify: `bridge/buddy_bridge/__main__.py`

- [ ] **Step 1: Implement**

Rewrite `bridge/buddy_bridge/__main__.py`:

```python
"""Entry point: socket server + BLE link + permission broker."""
import asyncio
import os

from buddy_bridge.ble_link import BleLink
from buddy_bridge.permissions import PermissionBroker
from buddy_bridge.protocol import (
    decode_device_message, encode_prompt, encode_prompt_cancel, encode_status)
from buddy_bridge.socket_server import serve
from buddy_bridge.state import SessionRegistry

# SOCK_PATH is a cross-process contract — must match hooks/buddy-hook.py
# and hooks/buddy-permission-hook.py.
SOCK_PATH = os.path.expanduser("~/.claude-buddy/bridge.sock")


async def main() -> None:
    reg = SessionRegistry()
    link = BleLink()
    _pending: set[asyncio.Task] = set()

    def spawn(coro) -> None:
        t = asyncio.create_task(coro)
        _pending.add(t)
        t.add_done_callback(_pending.discard)

    def send_prompt(pid: str, tool: str, detail: str, change) -> None:
        spawn(link.send(encode_prompt(pid, tool, detail, change)))

    def send_cancel(pid: str) -> None:
        spawn(link.send(encode_prompt_cancel(pid)))

    broker = PermissionBroker(send_prompt=send_prompt, send_cancel=send_cancel)

    def on_device_message(text: str) -> None:
        msg = decode_device_message(text)
        if msg is None:
            return
        if msg["cmd"] == "permission":
            broker.resolve(msg["id"], msg["decision"])
        elif msg["cmd"] == "auto":
            broker.set_auto_approve(msg["state"])

    link._on_device_message = on_device_message  # set before connect()

    def push() -> None:
        snap = reg.snapshot()
        spawn(link.send(encode_status(snap["running"], snap["waiting"],
                                      snap["total"], snap["msg"])))

    server = await serve(SOCK_PATH, reg, on_change=push, broker=broker)
    print(f"[bridge] listening on {SOCK_PATH}")
    async with server:
        await asyncio.gather(server.serve_forever(), link.run_forever())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
```

- [ ] **Step 2: Verify it imports**

Run: `cd bridge && .venv/bin/python -c "import buddy_bridge.__main__"`
Expected: no error.

- [ ] **Step 3: Commit**

```bash
git add bridge/buddy_bridge/__main__.py
git commit -m "feat(bridge): wire PermissionBroker and device-message routing"
```

---

## Task 6: `buddy-permission-hook.py` — the device/keyboard race

**Files:**
- Create: `hooks/buddy-permission-hook.py`
- Test: `bridge/tests/test_permission_hook.py`

The `PreToolUse` hook receives the tool call on stdin. It builds a privacy-safe `detail`, then races: a `/dev/tty` raw-mode keyboard prompt against a `permission_request` to the bridge. First answer wins. It prints the `PreToolUse` decision JSON on stdout and exits 0.

- [ ] **Step 1: Write the failing tests**

`bridge/tests/test_permission_hook.py`:

```python
import importlib.util, os

spec = importlib.util.spec_from_file_location(
    "buddy_permission_hook",
    os.path.join(os.path.dirname(__file__), "..", "..", "hooks",
                 "buddy-permission-hook.py"))
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


def test_build_detail_bash_uses_full_command():
    detail, change = hook.build_detail("Bash", {"command": "rm -rf /tmp/x"})
    assert detail == "rm -rf /tmp/x"
    assert change is None


def test_build_detail_edit_uses_path_and_size():
    detail, change = hook.build_detail("Edit", {
        "file_path": "/tmp/a.py", "old_string": "x\ny", "new_string": "z"})
    assert detail == "/tmp/a.py"
    assert change is not None  # a "+N/-M" style size string


def test_build_detail_webfetch_uses_url():
    detail, change = hook.build_detail("WebFetch", {"url": "https://x.test/a"})
    assert detail == "https://x.test/a"


def test_build_detail_never_includes_file_contents():
    detail, change = hook.build_detail("Write", {
        "file_path": "/tmp/secret.txt", "content": "SENSITIVE BODY TEXT"})
    assert "SENSITIVE BODY TEXT" not in detail
    assert "SENSITIVE BODY TEXT" not in (change or "")


def test_decision_output_allow():
    out = hook.decision_output("allow")
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"


def test_decision_output_deny():
    out = hook.decision_output("deny")
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd bridge && .venv/bin/python -m pytest tests/test_permission_hook.py -v`
Expected: FAIL — the hook file does not exist.

- [ ] **Step 3: Implement**

`hooks/buddy-permission-hook.py`:

```python
#!/usr/bin/env python3
"""Claude Code PreToolUse hook — race the buddy device against the keyboard.

Wired to PreToolUse in ~/.claude/settings.json. Builds a privacy-safe detail
string for the pending tool call, then waits on TWO inputs at once: a
/dev/tty keyboard prompt and a permission_request to the bridge (which relays
to the device). First answer wins. Prints the PreToolUse decision and exits 0.

Privacy: `detail` carries the tool call (command / path / URL) only — never
file contents or diff bodies. See the design spec's Privacy section.
"""
import json
import os
import select
import socket
import sys
import uuid

SOCK_PATH = os.path.expanduser("~/.claude-buddy/bridge.sock")  # contract:
# must match bridge/buddy_bridge/__main__.py


def build_detail(tool: str, tool_input: dict):
    """Return (detail, change). detail is the tool call; change is a size
    string for Edit/Write or None. Never includes file contents."""
    if tool == "Bash":
        return str(tool_input.get("command", "")), None
    if tool in ("Edit", "MultiEdit"):
        old = tool_input.get("old_string", "")
        new = tool_input.get("new_string", "")
        change = f"+{new.count(chr(10)) + 1}/-{old.count(chr(10)) + 1} lines"
        return str(tool_input.get("file_path", "")), change
    if tool == "Write":
        content = tool_input.get("content", "")
        change = f"{content.count(chr(10)) + 1} lines"
        return str(tool_input.get("file_path", "")), change
    if tool in ("WebFetch", "WebSearch"):
        return str(tool_input.get("url") or tool_input.get("query", "")), None
    # Generic fallback: tool name only — never dump arbitrary input.
    return tool, None


def decision_output(decision: str) -> dict:
    """Shape the PreToolUse hook output for allow/deny."""
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": f"claude-buddy: {decision}"}}


def _ask_bridge(sock: socket.socket, prompt_id: str, session: str,
                tool: str, detail: str, change) -> None:
    req = {"type": "permission_request", "id": prompt_id, "session": session,
           "tool": tool, "detail": detail, "change": change}
    sock.sendall((json.dumps(req) + "\n").encode("utf-8"))


def race(prompt_id: str, session: str, tool: str, detail: str, change) -> str:
    """Race the device (via bridge socket) against /dev/tty keyboard input.

    Returns 'allow' or 'deny'. Falls back to keyboard-only if the bridge is
    unreachable, and to 'ask' (handled by caller) if there is no tty.
    """
    # Bridge connection (may fail — then keyboard-only).
    sock = None
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(SOCK_PATH)
        _ask_bridge(sock, prompt_id, session, tool, detail, change)
    except OSError:
        sock = None

    # Keyboard via the controlling terminal.
    try:
        tty = open("/dev/tty", "r+b", buffering=0)
    except OSError:
        tty = None

    if sock is None and tty is None:
        return "ask"  # caller yields to Claude's native prompt

    if tty is not None:
        tty.write(f"\n[buddy] Approve {tool}: {detail}\n"
                  f"  [a]llow  [d]eny  (or press a button on your buddy)\n"
                  .encode("utf-8"))

    import tty as ttymod
    import termios
    old = None
    if tty is not None:
        old = termios.tcgetattr(tty.fileno())
        ttymod.setcbreak(tty.fileno())

    try:
        fds = [f for f in (sock, tty) if f is not None]
        while True:
            readable, _, _ = select.select(fds, [], [])
            if tty is not None and tty in readable:
                ch = tty.read(1)
                if ch in (b"a", b"A"):
                    if sock is not None:
                        _cancel(sock, prompt_id)
                    return "allow"
                if ch in (b"d", b"D"):
                    if sock is not None:
                        _cancel(sock, prompt_id)
                    return "deny"
            if sock is not None and sock in readable:
                data = sock.recv(256)
                if not data:
                    fds = [f for f in fds if f is not sock]
                    sock = None
                    if tty is None:
                        return "deny"
                    continue
                for line in data.splitlines():
                    if not line.strip():
                        continue
                    try:
                        resp = json.loads(line.decode("utf-8"))
                    except ValueError:
                        continue
                    if resp.get("decision") in ("allow", "deny"):
                        return resp["decision"]
    finally:
        if tty is not None and old is not None:
            termios.tcsetattr(tty.fileno(), termios.TCSADRAIN, old)
            tty.close()
        if sock is not None:
            sock.close()


def _cancel(sock: socket.socket, prompt_id: str) -> None:
    try:
        sock.sendall(
            (json.dumps({"type": "prompt_cancel", "id": prompt_id}) + "\n")
            .encode("utf-8"))
    except OSError:
        pass


def main() -> None:
    try:
        hook_input = json.load(sys.stdin)
    except (ValueError, OSError):
        sys.exit(0)  # yield to Claude's native prompt
    tool = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {}) or {}
    session = hook_input.get("session_id", "")
    if not tool:
        sys.exit(0)
    detail, change = build_detail(tool, tool_input)
    prompt_id = str(uuid.uuid4())
    try:
        decision = race(prompt_id, session, tool, detail, change)
    except Exception:
        sys.exit(0)  # any failure: yield to Claude's native prompt
    if decision == "ask":
        sys.exit(0)
    print(json.dumps(decision_output(decision)))
    sys.exit(0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Make executable and run tests**

```bash
chmod +x hooks/buddy-permission-hook.py
cd bridge && .venv/bin/python -m pytest tests/test_permission_hook.py -v
```

Expected: all six tests PASS.

- [ ] **Step 5: Commit**

```bash
git add hooks/buddy-permission-hook.py bridge/tests/test_permission_hook.py
git commit -m "feat(hooks): PreToolUse device/keyboard permission race"
```

---

## Task 7: Wire the `PreToolUse` hook into settings

**Files:**
- Modify: `hooks/settings.example.json`
- Modify: `~/.claude/settings.json` (user's live config — done by the user)

- [ ] **Step 1: Add PreToolUse to the example file**

In `hooks/settings.example.json`, add this key inside `"hooks"` alongside the existing five:

```json
    "PreToolUse": [
      {"hooks": [{"type": "command", "command": "$HOME/terminal-claude-code-buddy-m5stack/hooks/buddy-permission-hook.py"}]}
    ]
```

- [ ] **Step 2: Validate the example JSON**

Run: `python3 -m json.tool hooks/settings.example.json > /dev/null && echo valid`
Expected: `valid`.

- [ ] **Step 3: Commit**

```bash
git add hooks/settings.example.json
git commit -m "docs(hooks): add PreToolUse wiring to settings example"
```

- [ ] **Step 4: User applies it to the live config**

The user adds the same `PreToolUse` block to `~/.claude/settings.json` and validates with `python3 -m json.tool`. This is a user step (it changes live Claude Code behavior) — do not edit the live file in an automated run.

---

## Task 8: Firmware — buttons and the permission-takeover screen

**Files:**
- Modify: `firmware/src/main.cpp`

- [ ] **Step 1: Add prompt state and the takeover renderer**

In `firmware/src/main.cpp`, add file-scope state above `setup()`:

```cpp
// Pending permission prompt state (empty id == no prompt).
static char promptId[48] = {0};
static char promptTool[24] = {0};
static char promptDetail[200] = {0};
static bool autoApprove = false;
```

Add a renderer (call site in Task 9's RX handler):

```cpp
static void renderPrompt(const char* tool, const char* detail) {
  M5.Display.fillScreen(TFT_NAVY);
  M5.Display.setTextColor(TFT_WHITE, TFT_NAVY);
  M5.Display.setTextSize(3);
  M5.Display.setCursor(8, 8);
  M5.Display.printf("Approve %s?", tool);
  M5.Display.setTextSize(2);
  M5.Display.setCursor(8, 50);
  M5.Display.setTextWrap(true);
  M5.Display.print(detail);              // full tool call; no file contents
  M5.Display.setTextColor(TFT_GREEN, TFT_NAVY);
  M5.Display.setCursor(8, 210);
  M5.Display.print("[A] Allow");
  M5.Display.setTextColor(TFT_RED, TFT_NAVY);
  M5.Display.setCursor(180, 210);
  M5.Display.print("[C] Deny");
}
```

- [ ] **Step 2: Poll buttons in `loop()`**

Replace the body of `loop()` with:

```cpp
void loop() {
  M5.update();
  if (promptId[0] != 0) {
    if (M5.BtnA.wasPressed()) { sendDecision("allow"); }
    else if (M5.BtnC.wasPressed()) { sendDecision("deny"); }
  }
  if (M5.BtnB.wasPressed()) { toggleAuto(); }
  delay(20);
}
```

`sendDecision` and `toggleAuto` are defined in Tasks 9 and 10 — declare them above `loop()` with forward declarations:

```cpp
static void sendDecision(const char* decision);
static void toggleAuto();
```

- [ ] **Step 3: Compile**

Run: `cd firmware && /opt/homebrew/bin/pio run -e m5stack-core`
Expected: `[SUCCESS]` (forward-declared functions are defined in the next tasks; if compiling this task alone fails on undefined references, proceed to Task 9 — they are committed together at Task 10).

Note: to keep each task independently compilable, implement Tasks 8, 9, and 10's function bodies before the first `pio run`. Treat Tasks 8–10 as one compile unit; commit after Task 10.

---

## Task 9: Firmware — send the decision over TX notify

**Files:**
- Modify: `firmware/src/main.cpp`

- [ ] **Step 1: Implement `sendDecision` and handle prompt messages**

Add `sendDecision` above `loop()`:

```cpp
static void sendNotify(const char* json) {
  if (txChar == nullptr || !centralConnected) return;
  txChar->setValue((uint8_t*)json, strlen(json));
  txChar->notify();
}

static void sendDecision(const char* decision) {
  char buf[96];
  snprintf(buf, sizeof(buf),
           "{\"cmd\":\"permission\",\"id\":\"%s\",\"decision\":\"%s\"}\n",
           promptId, decision);
  sendNotify(buf);
  promptId[0] = 0;                 // clear pending prompt
  renderStatus(0, 0, 0, "");       // back to status; next heartbeat refreshes
}
```

- [ ] **Step 2: Parse prompt and prompt_cancel in the RX callback**

In `RxCallbacks::onWrite`, after the existing `evt == "status"` branch, add:

```cpp
    if (doc["evt"] == "prompt") {
      strlcpy(promptId, doc["id"] | "", sizeof(promptId));
      strlcpy(promptTool, doc["tool"] | "", sizeof(promptTool));
      strlcpy(promptDetail, doc["detail"] | "", sizeof(promptDetail));
      if (autoApprove) { sendDecision("allow"); }
      else { renderPrompt(promptTool, promptDetail); }
    } else if (doc["cmd"] == "prompt_cancel") {
      if (strcmp(doc["id"] | "", promptId) == 0) {
        promptId[0] = 0;
        renderStatus(0, 0, 0, "");
      }
    }
```

- [ ] **Step 2 note:** the `renderStatus(0,0,0,"")` calls are placeholders cleared by the next status heartbeat (~within seconds). Acceptable for Phase 2.

---

## Task 10: Firmware — auto-approve toggle, banner, and beep

**Files:**
- Modify: `firmware/src/main.cpp`

- [ ] **Step 1: Implement `toggleAuto`**

Add above `loop()`:

```cpp
static void toggleAuto() {
  autoApprove = !autoApprove;
  char buf[40];
  snprintf(buf, sizeof(buf), "{\"cmd\":\"auto\",\"state\":%s}\n",
           autoApprove ? "true" : "false");
  sendNotify(buf);
  // Banner so the auto-approve state is never ambiguous.
  M5.Display.fillRect(0, 0, 320, 28, autoApprove ? TFT_RED : TFT_BLACK);
  if (autoApprove) {
    M5.Display.setTextColor(TFT_WHITE, TFT_RED);
    M5.Display.setTextSize(2);
    M5.Display.setCursor(8, 6);
    M5.Display.print("AUTO-APPROVE ON");
    M5.Speaker.tone(880, 120);
  }
}
```

- [ ] **Step 2: Beep on each auto-approval**

In `sendDecision`, at the top, add an audible cue when auto-approving:

```cpp
  if (autoApprove) { M5.Speaker.tone(660, 80); }
```

- [ ] **Step 3: Enable the speaker in `setup()`**

After `M5.begin(cfg);` add:

```cpp
  M5.Speaker.begin();
```

- [ ] **Step 4: Compile**

Run: `cd firmware && /opt/homebrew/bin/pio run -e m5stack-core`
Expected: `[SUCCESS]`. If the compile reports an M5Unified API mismatch for `M5.Speaker` or `BtnC`, consult the installed M5Unified headers and adjust (the API is authoritative over this plan).

- [ ] **Step 5: Commit Tasks 8–10 together**

```bash
git add firmware/src/main.cpp
git commit -m "feat(firmware): buttons, permission screen, decision notify, auto-approve"
```

---

## Task 11: Verify the encrypted-link prerequisite

**Files:** none — verification only.

The Phase 1 follow-up commit `849d063` made the NUS characteristics require an encrypted link. Confirm it still holds after the Phase 2 firmware changes.

- [ ] **Step 1: Flash and confirm forced pairing**

```bash
cd firmware && /opt/homebrew/bin/pio run -e m5stack-core -t upload --upload-port /dev/cu.usbserial-XXXX
```

(Use the actual `/dev/cu.usbserial-*` port.) Then run the bridge:

```bash
cd ../bridge && .venv/bin/python -m buddy_bridge
```

Expected: macOS shows a Bluetooth pairing dialog; enter the passkey shown on the M5Stack screen. If status/prompts flow with **no** pairing dialog, the encrypted-permission attributes were lost — re-check `setAccessPermissions` on RX/TX/CCCD in `firmware/src/main.cpp` before continuing.

---

## Task 12: End-to-end Phase 2 verification

**Files:** none — verification only.

- [ ] **Step 1: Full test suite**

Run: `cd bridge && .venv/bin/python -m pytest tests/ -v`
Expected: all tests PASS.

- [ ] **Step 2: Device-wins path**

With the bridge running, the device paired, and `PreToolUse` wired (Task 7), start a Claude Code session and trigger a tool call (e.g. ask it to run a `Bash` command).

Expected: the M5Stack shows the navy "Approve Bash?" screen with the full command; the terminal shows the `[buddy] Approve …` prompt. Press **A** on the device → the tool runs. Press **C** → it's denied.

- [ ] **Step 3: Keyboard-wins path**

Trigger another tool call. Answer in the terminal (`a` or `d`) before touching the device.

Expected: the decision applies immediately and the device's prompt screen clears (the bridge sent `prompt_cancel`).

- [ ] **Step 4: Auto-approve path**

Press **B** on the device — confirm the red `AUTO-APPROVE ON` banner and a beep. Trigger a tool call.

Expected: it is approved with no prompt screen and a short beep; no terminal prompt blocks. Press **B** again to turn it off.

- [ ] **Step 5: Fallback path**

Stop the bridge (Ctrl-C). Trigger a tool call.

Expected: the terminal `[buddy]` prompt still works (keyboard-only); answering it allows/denies. Claude Code is never blocked.

- [ ] **Step 6: Privacy check**

While triggering `Edit`/`Write` tool calls, watch the device screen and the bridge.

Expected: the device shows the file *path* and a change *size* (e.g. `+12/-3 lines`) — never file contents or diff bodies.

- [ ] **Step 7: Final commit and push**

```bash
git add -A
git commit -m "test: Phase 2 end-to-end verification complete"
git push
```

---

## Out of scope (Phase 3)

- The animated desk-pet character and GIF packs.
- `launchd` auto-start for the bridge (still run manually, or add as a small follow-up).

## Notes for the implementer

- **Firmware has no unit tests** — verified by flashing and observing. Tasks 8–10 are one compile unit; commit after Task 10.
- **The race** (`buddy-permission-hook.py`) cannot be fully unit-tested (it needs `/dev/tty` and a live socket) — tests cover `build_detail` and `decision_output`; the race itself is verified in Task 12.
- **Privacy is load-bearing.** `build_detail` must never place file contents or diff bodies into `detail`/`change`. Tasks include an explicit test for this.
- **`asyncio` re-entrancy:** `broker.request()` is awaited inside the socket handler; the handler reads further lines (`prompt_cancel`) on the same connection. The Task 3 implementation handles both in the per-connection loop.
