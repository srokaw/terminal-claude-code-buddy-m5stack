# Claude Buddy — Phase 1: Live Status Display — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A desk device (M5Stack Core Basic) that shows the live aggregate status of all terminal Claude Code sessions — how many are running, waiting, or idle — with no dependency on the Claude desktop app.

**Architecture:** Three components. (1) Claude Code **hooks** fire on session lifecycle events and send a one-line JSON event to (2) a local **Python bridge** over a Unix domain socket; the bridge aggregates state across all sessions and, acting as a BLE central, writes a compact status message to (3) **firmware** on the M5Stack, which renders it on the 320×240 screen. Phase 1 is read-only — no permission approvals (Phase 2).

**Tech Stack:** Python 3.13 + `bleak` (BLE central) + `asyncio` (bridge); PlatformIO + Arduino + `M5Unified` + `ArduinoJson` (firmware); Claude Code hooks wired via `~/.claude/settings.json`.

**Reference:** Design spec at `docs/superpowers/specs/2026-05-18-claude-buddy-m5stack-design.md`. The diagnostic probe (`$HOME/m5stack-buddy-probe/`) is the firmware starting point — it already advertises the Nordic UART Service and handles bonded pairing.

**Privacy (load-bearing):** The bridge sends the device ONLY counts and a one-line status string. It never reads Claude Code transcript files and never transmits message text or file contents. Phase 1 has no mechanism that could — keep it that way.

---

## File Structure

```
terminal-claude-code-buddy-m5stack/   (fork of anthropics/claude-desktop-buddy, renamed)
├── firmware/
│   ├── platformio.ini           PlatformIO env for m5stack-core-esp32
│   └── src/main.cpp             BLE peripheral + 320×240 status screen
├── bridge/
│   ├── requirements.txt         bleak
│   ├── buddy_bridge/
│   │   ├── __init__.py
│   │   ├── protocol.py          JSON message encoding
│   │   ├── state.py             SessionRegistry — aggregates session state
│   │   ├── socket_server.py     Unix-socket server receiving hook events
│   │   ├── ble_link.py          bleak BLE central — writes status to device
│   │   └── __main__.py          wires socket server + BLE link together
│   └── tests/
│       ├── test_protocol.py
│       ├── test_state.py
│       └── test_socket_server.py
├── hooks/
│   └── buddy-hook.py            single hook script, dispatched by event name
└── docs/superpowers/{specs,plans}/
```

Interfaces locked here (used across tasks):

- `protocol.encode_status(running:int, waiting:int, total:int, msg:str) -> bytes`
- `SessionRegistry`: `.start(sid)`, `.end(sid)`, `.set_state(sid, state)`, `.snapshot() -> dict`
  - `state` is one of `"idle"`, `"running"`, `"waiting"`
  - `snapshot()` returns `{"running":int,"waiting":int,"total":int,"msg":str}`
- Bridge socket event (one JSON object per line): `{"type":"start"|"end"|"state","session":str,"state":str?}`

---

## Task 1: Fork, rename, and clone the repo

**Files:**
- Create: repository directory structure (no code yet)

- [ ] **Step 1–3: Fork, rename, clone (already done via gh CLI)**

The fork was created and cloned with:

```bash
gh repo fork anthropics/claude-desktop-buddy --fork-name claude-buddy-m5stack --clone --default-branch-only
gh repo rename terminal-claude-code-buddy-m5stack --repo srokaw/claude-buddy-m5stack --yes
```

Result: `srokaw/terminal-claude-code-buddy-m5stack` on GitHub (forked from
anthropics/claude-desktop-buddy), cloned to
`$HOME/terminal-claude-code-buddy-m5stack` with `origin` and
`upstream` remotes set.

- [ ] **Step 4: Create the new directory structure**

```bash
cd ~/terminal-claude-code-buddy-m5stack
mkdir -p firmware/src bridge/buddy_bridge bridge/tests hooks docs/superpowers/specs docs/superpowers/plans
cp ~/m5stack-claude-buddy/docs/superpowers/specs/2026-05-18-claude-buddy-m5stack-design.md docs/superpowers/specs/
cp ~/m5stack-claude-buddy/docs/superpowers/plans/2026-05-19-claude-buddy-phase1-status-display.md docs/superpowers/plans/
```

- [ ] **Step 5: Add a top-level README section and commit**

Append to the existing `README.md` (do not delete Anthropic's content — it stays as the protocol reference):

```markdown

---

## terminal-claude-code-buddy-m5stack

A fork adapting this project for the **M5Stack Core Basic**, driven by a local
Python bridge instead of the Claude desktop app, for terminal Claude Code users.

- `firmware/` — M5Stack Core Basic firmware
- `bridge/`   — Python local bridge (BLE central)
- `hooks/`    — Claude Code hook scripts
- `docs/`     — design spec and implementation plans

Anthropic's original firmware (`src/`, `characters/`, …) is kept as a protocol reference.
```

```bash
git add -A
git commit -m "chore: add claude-buddy-m5stack directory structure"
git push
```

---

## Task 2: Bridge — Python package scaffolding

**Files:**
- Create: `bridge/requirements.txt`
- Create: `bridge/buddy_bridge/__init__.py`
- Create: `bridge/tests/test_smoke.py`

- [ ] **Step 1: Write the dependency file**

`bridge/requirements.txt`:

```
bleak>=0.22
pytest>=8.0
```

- [ ] **Step 2: Create the package marker**

`bridge/buddy_bridge/__init__.py`:

```python
"""Local bridge: Claude Code hooks -> BLE -> M5Stack buddy device."""
```

- [ ] **Step 3: Write a smoke test**

`bridge/tests/test_smoke.py`:

```python
import buddy_bridge


def test_package_imports():
    assert buddy_bridge is not None
```

- [ ] **Step 4: Create the venv, install deps, run the test**

```bash
cd ~/terminal-claude-code-buddy-m5stack/bridge
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests/ -v
```

Expected: `test_package_imports PASSED`.

- [ ] **Step 5: Commit**

```bash
cd ~/terminal-claude-code-buddy-m5stack
echo "bridge/.venv/" >> .gitignore
echo "__pycache__/" >> .gitignore
git add -A
git commit -m "chore: scaffold Python bridge package"
```

---

## Task 3: Bridge — `protocol.py` (status message encoding)

**Files:**
- Create: `bridge/buddy_bridge/protocol.py`
- Test: `bridge/tests/test_protocol.py`

- [ ] **Step 1: Write the failing test**

`bridge/tests/test_protocol.py`:

```python
import json
from buddy_bridge.protocol import encode_status


def test_encode_status_is_one_json_line():
    raw = encode_status(running=2, waiting=1, total=4, msg="2 running · 1 waiting")
    assert isinstance(raw, bytes)
    assert raw.endswith(b"\n")
    assert raw.count(b"\n") == 1


def test_encode_status_fields():
    raw = encode_status(running=2, waiting=1, total=4, msg="hi")
    obj = json.loads(raw.decode("utf-8"))
    assert obj == {"evt": "status", "running": 2, "waiting": 1, "total": 4, "msg": "hi"}
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd ~/terminal-claude-code-buddy-m5stack/bridge && .venv/bin/python -m pytest tests/test_protocol.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'buddy_bridge.protocol'`.

- [ ] **Step 3: Write the implementation**

`bridge/buddy_bridge/protocol.py`:

```python
"""Encoding of bridge -> device messages. JSON, one object per line."""
import json


def encode_status(running: int, waiting: int, total: int, msg: str) -> bytes:
    """Encode a live status message for the device.

    Privacy: only counts and a short status string. Never message text,
    file contents, or transcript data.
    """
    obj = {
        "evt": "status",
        "running": running,
        "waiting": waiting,
        "total": total,
        "msg": msg,
    }
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd ~/terminal-claude-code-buddy-m5stack/bridge && .venv/bin/python -m pytest tests/test_protocol.py -v
```

Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/terminal-claude-code-buddy-m5stack
git add bridge/buddy_bridge/protocol.py bridge/tests/test_protocol.py
git commit -m "feat(bridge): status message encoding"
```

---

## Task 4: Bridge — `state.py` (`SessionRegistry`)

**Files:**
- Create: `bridge/buddy_bridge/state.py`
- Test: `bridge/tests/test_state.py`

- [ ] **Step 1: Write the failing test**

`bridge/tests/test_state.py`:

```python
from buddy_bridge.state import SessionRegistry


def test_empty_registry_is_idle():
    reg = SessionRegistry()
    assert reg.snapshot() == {"running": 0, "waiting": 0, "total": 0, "msg": "idle"}


def test_start_adds_idle_session():
    reg = SessionRegistry()
    reg.start("s1")
    snap = reg.snapshot()
    assert snap["total"] == 1
    assert snap["running"] == 0


def test_state_counts_running_and_waiting():
    reg = SessionRegistry()
    reg.start("s1")
    reg.start("s2")
    reg.start("s3")
    reg.set_state("s1", "running")
    reg.set_state("s2", "waiting")
    snap = reg.snapshot()
    assert snap == {"running": 1, "waiting": 1, "total": 3,
                    "msg": "1 running · 1 waiting"}


def test_set_state_auto_registers_unknown_session():
    reg = SessionRegistry()
    reg.set_state("late", "running")  # no prior start()
    assert reg.snapshot()["total"] == 1
    assert reg.snapshot()["running"] == 1


def test_end_removes_session():
    reg = SessionRegistry()
    reg.start("s1")
    reg.end("s1")
    assert reg.snapshot()["total"] == 0


def test_end_unknown_session_is_safe():
    reg = SessionRegistry()
    reg.end("never-existed")  # must not raise
    assert reg.snapshot()["total"] == 0
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd ~/terminal-claude-code-buddy-m5stack/bridge && .venv/bin/python -m pytest tests/test_state.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'buddy_bridge.state'`.

- [ ] **Step 3: Write the implementation**

`bridge/buddy_bridge/state.py`:

```python
"""Aggregated state across all terminal Claude Code sessions."""

VALID_STATES = ("idle", "running", "waiting")


class SessionRegistry:
    """Tracks one state string per session id and produces a snapshot."""

    def __init__(self) -> None:
        self._sessions: dict[str, str] = {}

    def start(self, session_id: str) -> None:
        self._sessions[session_id] = "idle"

    def end(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def set_state(self, session_id: str, state: str) -> None:
        if state not in VALID_STATES:
            raise ValueError(f"unknown state: {state}")
        # Auto-register: a hook event may arrive for a session whose
        # start() the bridge missed (e.g. bridge started mid-session).
        self._sessions[session_id] = state

    def snapshot(self) -> dict:
        running = sum(1 for s in self._sessions.values() if s == "running")
        waiting = sum(1 for s in self._sessions.values() if s == "waiting")
        total = len(self._sessions)
        if total == 0:
            msg = "idle"
        else:
            msg = f"{running} running · {waiting} waiting"
        return {"running": running, "waiting": waiting,
                "total": total, "msg": msg}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd ~/terminal-claude-code-buddy-m5stack/bridge && .venv/bin/python -m pytest tests/test_state.py -v
```

Expected: all six tests PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/terminal-claude-code-buddy-m5stack
git add bridge/buddy_bridge/state.py bridge/tests/test_state.py
git commit -m "feat(bridge): SessionRegistry state aggregation"
```

---

## Task 5: Bridge — `socket_server.py` (Unix socket for hook events)

**Files:**
- Create: `bridge/buddy_bridge/socket_server.py`
- Test: `bridge/tests/test_socket_server.py`

The server listens on a Unix domain socket. Each hook invocation connects, writes one JSON line, and disconnects. The server applies the event to a `SessionRegistry` and invokes an `on_change` callback so the caller can push a BLE update.

- [ ] **Step 1: Write the failing test**

`bridge/tests/test_socket_server.py`:

```python
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
    sock_path = str(tmp_path / "bridge.sock")
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
```

- [ ] **Step 2: Add asyncio test support and run the test to verify it fails**

```bash
cd ~/terminal-claude-code-buddy-m5stack/bridge
echo "pytest-asyncio>=0.23" >> requirements.txt
.venv/bin/pip install -r requirements.txt
printf '[pytest]\nasyncio_mode = auto\n' > pytest.ini
.venv/bin/python -m pytest tests/test_socket_server.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'buddy_bridge.socket_server'`.

- [ ] **Step 3: Write the implementation**

`bridge/buddy_bridge/socket_server.py`:

```python
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
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd ~/terminal-claude-code-buddy-m5stack/bridge && .venv/bin/python -m pytest tests/ -v
```

Expected: all tests PASS (smoke + protocol + state + socket_server).

- [ ] **Step 5: Commit**

```bash
cd ~/terminal-claude-code-buddy-m5stack
git add bridge/
git commit -m "feat(bridge): Unix-socket server for hook events"
```

---

## Task 6: Bridge — `ble_link.py` (BLE central)

**Files:**
- Create: `bridge/buddy_bridge/ble_link.py`

This connects to the M5Stack as a BLE central and writes status bytes to the Nordic UART RX characteristic. It is verified manually against the probe (BLE hardware cannot be unit-tested).

- [ ] **Step 1: Write the implementation**

`bridge/buddy_bridge/ble_link.py`:

```python
"""BLE central: connects to the M5Stack buddy and writes status messages."""
import asyncio

from bleak import BleakClient, BleakScanner

# Nordic UART Service — matches the firmware.
NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # central writes here
DEVICE_NAME = "Claude-Buddy"


class BleLink:
    """Maintains a connection to the buddy device and writes status lines."""

    def __init__(self, device_name: str = DEVICE_NAME) -> None:
        self._device_name = device_name
        self._client: BleakClient | None = None
        self._last_payload: bytes | None = None

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    async def connect(self) -> bool:
        """Scan for and connect to the device. Returns True on success."""
        device = await BleakScanner.find_device_by_name(
            self._device_name, timeout=15.0)
        if device is None:
            return False
        client = BleakClient(device)
        await client.connect()
        self._client = client
        if self._last_payload is not None:
            await self._client.write_gatt_char(NUS_RX, self._last_payload,
                                                response=False)
        return True

    async def send(self, payload: bytes) -> None:
        """Write a status payload. Caches it for re-send after reconnect."""
        self._last_payload = payload
        if self.connected:
            await self._client.write_gatt_char(NUS_RX, payload, response=False)

    async def run_forever(self) -> None:
        """Connect, and reconnect with backoff whenever the link drops."""
        while True:
            if not self.connected:
                ok = await self.connect()
                if not ok:
                    await asyncio.sleep(5.0)
                    continue
            await asyncio.sleep(2.0)
```

- [ ] **Step 2: Manual verification against the probe**

The probe (`~/m5stack-buddy-probe/`) advertises as `Claude-Probe`. Temporarily test against it: in a Python REPL inside the bridge venv, run a one-off connect using `device_name="Claude-Probe"` and `send(b'{"evt":"status"}\n')`, and confirm the probe's serial monitor prints `[rx] {"evt":"status"}`.

```bash
cd ~/terminal-claude-code-buddy-m5stack/bridge
.venv/bin/python - <<'EOF'
import asyncio
from buddy_bridge.ble_link import BleLink

async def main():
    link = BleLink(device_name="Claude-Probe")
    ok = await link.connect()
    print("connected:", ok)
    if ok:
        await link.send(b'{"evt":"status","running":1,"waiting":0,"total":1,"msg":"test"}\n')
        print("sent")
    await asyncio.sleep(2)

asyncio.run(main())
EOF
```

Expected: `connected: True`, `sent`, and the probe's serial monitor shows the `[rx]` line. (macOS may show a pairing dialog; enter the passkey from the probe's serial output.)

- [ ] **Step 3: Commit**

```bash
cd ~/terminal-claude-code-buddy-m5stack
git add bridge/buddy_bridge/ble_link.py
git commit -m "feat(bridge): BLE central link to the buddy device"
```

---

## Task 7: Bridge — `__main__.py` (wire it together)

**Files:**
- Create: `bridge/buddy_bridge/__main__.py`

- [ ] **Step 1: Write the implementation**

`bridge/buddy_bridge/__main__.py`:

```python
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
```

- [ ] **Step 2: Manual smoke test**

With the probe powered on, temporarily change `BleLink()` to `BleLink(device_name="Claude-Probe")`, then:

```bash
cd ~/terminal-claude-code-buddy-m5stack/bridge && .venv/bin/python -m buddy_bridge
```

Expected: `[bridge] listening on …/bridge.sock`. In another terminal:

```bash
printf '{"type":"start","session":"s1"}\n' | nc -U ~/.claude-buddy/bridge.sock
```

Expected: the probe's serial monitor prints an `[rx] {"evt":"status",...,"total":1,...}` line. Revert the `device_name` change afterward (Ctrl-C the bridge).

- [ ] **Step 3: Commit**

```bash
cd ~/terminal-claude-code-buddy-m5stack
git add bridge/buddy_bridge/__main__.py
git commit -m "feat(bridge): entry point wiring socket server and BLE link"
```

---

## Task 8: Hook script — `hooks/buddy-hook.py`

**Files:**
- Create: `hooks/buddy-hook.py`
- Test: `bridge/tests/test_hook.py`

One script handles every hook event. Claude Code passes hook input as JSON on stdin (including `hook_event_name` and `session_id`). The script maps the event to a bridge event and sends it to the socket. It must be fast and must never fail the hook — if the bridge is down, it exits 0 silently.

- [ ] **Step 1: Write the failing test**

`bridge/tests/test_hook.py`:

```python
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "hooks"))
import importlib.util

spec = importlib.util.spec_from_file_location(
    "buddy_hook",
    os.path.join(os.path.dirname(__file__), "..", "..", "hooks", "buddy-hook.py"))
buddy_hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(buddy_hook)


def test_session_start_maps_to_start():
    out = buddy_hook.to_bridge_event(
        {"hook_event_name": "SessionStart", "session_id": "abc"})
    assert out == {"type": "start", "session": "abc"}


def test_session_end_maps_to_end():
    out = buddy_hook.to_bridge_event(
        {"hook_event_name": "SessionEnd", "session_id": "abc"})
    assert out == {"type": "end", "session": "abc"}


def test_user_prompt_submit_maps_to_running():
    out = buddy_hook.to_bridge_event(
        {"hook_event_name": "UserPromptSubmit", "session_id": "abc"})
    assert out == {"type": "state", "session": "abc", "state": "running"}


def test_stop_maps_to_idle():
    out = buddy_hook.to_bridge_event(
        {"hook_event_name": "Stop", "session_id": "abc"})
    assert out == {"type": "state", "session": "abc", "state": "idle"}


def test_notification_maps_to_waiting():
    out = buddy_hook.to_bridge_event(
        {"hook_event_name": "Notification", "session_id": "abc"})
    assert out == {"type": "state", "session": "abc", "state": "waiting"}


def test_unknown_event_maps_to_none():
    out = buddy_hook.to_bridge_event(
        {"hook_event_name": "PreCompact", "session_id": "abc"})
    assert out is None


def test_missing_session_id_maps_to_none():
    out = buddy_hook.to_bridge_event({"hook_event_name": "Stop"})
    assert out is None
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd ~/terminal-claude-code-buddy-m5stack/bridge && .venv/bin/python -m pytest tests/test_hook.py -v
```

Expected: FAIL — the file `hooks/buddy-hook.py` does not exist.

- [ ] **Step 3: Write the implementation**

`hooks/buddy-hook.py`:

```python
#!/usr/bin/env python3
"""Claude Code hook -> bridge. Maps a hook event to a bridge socket event.

Wired to multiple hook events in ~/.claude/settings.json. Reads the hook
JSON on stdin, sends one line to the bridge socket, and always exits 0 so it
can never block or fail a Claude Code session.
"""
import json
import os
import socket
import sys

SOCK_PATH = os.path.expanduser("~/.claude-buddy/bridge.sock")

_EVENT_MAP = {
    "SessionStart": ("start", None),
    "SessionEnd": ("end", None),
    "UserPromptSubmit": ("state", "running"),
    "Stop": ("state", "idle"),
    "Notification": ("state", "waiting"),
}


def to_bridge_event(hook_input: dict):
    """Map a Claude Code hook payload to a bridge event, or None to ignore."""
    name = hook_input.get("hook_event_name")
    session = hook_input.get("session_id")
    if not session or name not in _EVENT_MAP:
        return None
    etype, state = _EVENT_MAP[name]
    event = {"type": etype, "session": session}
    if state is not None:
        event["state"] = state
    return event


def main() -> None:
    try:
        hook_input = json.load(sys.stdin)
    except (ValueError, OSError):
        sys.exit(0)
    event = to_bridge_event(hook_input)
    if event is None:
        sys.exit(0)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            sock.connect(SOCK_PATH)
            sock.sendall((json.dumps(event) + "\n").encode("utf-8"))
    except OSError:
        pass  # bridge not running — never block Claude Code
    sys.exit(0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Make it executable and run the test to verify it passes**

```bash
chmod +x ~/terminal-claude-code-buddy-m5stack/hooks/buddy-hook.py
cd ~/terminal-claude-code-buddy-m5stack/bridge && .venv/bin/python -m pytest tests/test_hook.py -v
```

Expected: all seven tests PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/terminal-claude-code-buddy-m5stack
git add hooks/buddy-hook.py bridge/tests/test_hook.py
git commit -m "feat(hooks): buddy-hook.py event-to-bridge mapping"
```

---

## Task 9: Wire hooks into `~/.claude/settings.json` and verify live

**Files:**
- Modify: `~/.claude/settings.json`

- [ ] **Step 1: Inspect the current settings**

```bash
cat ~/.claude/settings.json
```

Note whether a top-level `"hooks"` key already exists. If the file does not exist, treat it as `{}`.

- [ ] **Step 2: Add the hook entries**

Merge this `"hooks"` block into `~/.claude/settings.json` (if `"hooks"` already exists, add these five keys alongside the existing ones). Use the absolute path to the script:

```json
{
  "hooks": {
    "SessionStart": [
      {"hooks": [{"type": "command", "command": "$HOME/terminal-claude-code-buddy-m5stack/hooks/buddy-hook.py"}]}
    ],
    "SessionEnd": [
      {"hooks": [{"type": "command", "command": "$HOME/terminal-claude-code-buddy-m5stack/hooks/buddy-hook.py"}]}
    ],
    "UserPromptSubmit": [
      {"hooks": [{"type": "command", "command": "$HOME/terminal-claude-code-buddy-m5stack/hooks/buddy-hook.py"}]}
    ],
    "Stop": [
      {"hooks": [{"type": "command", "command": "$HOME/terminal-claude-code-buddy-m5stack/hooks/buddy-hook.py"}]}
    ],
    "Notification": [
      {"hooks": [{"type": "command", "command": "$HOME/terminal-claude-code-buddy-m5stack/hooks/buddy-hook.py"}]}
    ]
  }
}
```

- [ ] **Step 3: Verify the JSON is valid**

```bash
python3 -m json.tool ~/.claude/settings.json > /dev/null && echo "valid JSON"
```

Expected: `valid JSON`.

- [ ] **Step 4: Live integration test**

Start the bridge (no device needed for this check — comment out the `link.run_forever()` BLE part is unnecessary; the BLE scan just fails harmlessly). Run the bridge with a print added, or simply observe via the socket. Easiest: temporarily add a `print` of the snapshot inside `push()` in `__main__.py`, run the bridge, then start a fresh Claude Code session in another terminal and submit a prompt.

```bash
cd ~/terminal-claude-code-buddy-m5stack/bridge && .venv/bin/python -m buddy_bridge
```

Expected: when you open a new Claude Code session, the bridge logs a state change with `total >= 1`; submitting a prompt flips a session to `running`; the assistant finishing flips it to `idle`. Remove the temporary `print` afterward.

- [ ] **Step 5: Commit**

The settings file lives outside the repo, so commit only a documented copy.

```bash
cd ~/terminal-claude-code-buddy-m5stack
cp ~/.claude/settings.json hooks/settings.example.json
git add hooks/settings.example.json
git commit -m "docs(hooks): example settings.json hook wiring"
```

---

## Task 10: Firmware — add M5Unified and the display

**Files:**
- Create: `firmware/platformio.ini`
- Create: `firmware/src/main.cpp` (start from the probe, then extend)

- [ ] **Step 1: Copy the probe as the firmware starting point**

```bash
cp ~/m5stack-buddy-probe/src/main.cpp ~/terminal-claude-code-buddy-m5stack/firmware/src/main.cpp
```

- [ ] **Step 2: Write the PlatformIO config**

`firmware/platformio.ini`:

```ini
[env:m5stack-core]
platform = espressif32
board = m5stack-core-esp32
framework = arduino
monitor_speed = 115200
build_flags = -DCORE_DEBUG_LEVEL=0
lib_deps =
    m5stack/M5Unified@^0.2.0
    bblanchon/ArduinoJson@^7.0.0
```

- [ ] **Step 3: Add M5Unified init and a startup screen**

In `firmware/src/main.cpp`, add near the top with the other includes:

```cpp
#include <M5Unified.h>
```

In `setup()`, as the very first lines (before `Serial.begin`):

```cpp
  auto cfg = M5.config();
  M5.begin(cfg);
  M5.Display.setRotation(1);              // 320x240 landscape
  M5.Display.fillScreen(TFT_BLACK);
  M5.Display.setTextColor(TFT_WHITE, TFT_BLACK);
  M5.Display.setTextSize(2);
  M5.Display.setCursor(8, 8);
  M5.Display.print("Claude Buddy");
  M5.Display.setCursor(8, 40);
  M5.Display.print("starting...");
```

Also change the advertised name from `"Claude-Probe"` to `"Claude-Buddy"`:

```cpp
  BLEDevice::init("Claude-Buddy");
```

And in `onPassKeyNotify`, also show the passkey on screen:

```cpp
  void onPassKeyNotify(uint32_t pk) override {
    Serial.printf("\n  BLE PAIRING PASSKEY: %06u\n\n", pk);
    M5.Display.fillScreen(TFT_BLACK);
    M5.Display.setTextSize(2);
    M5.Display.setCursor(8, 8);
    M5.Display.print("Pair this code:");
    M5.Display.setTextSize(4);
    M5.Display.setCursor(8, 60);
    M5.Display.printf("%06u", pk);
  }
```

- [ ] **Step 4: Build and flash**

```bash
cd ~/terminal-claude-code-buddy-m5stack/firmware
pio run -e m5stack-core -t upload --upload-port /dev/cu.usbserial-XXXXXXXXXXX
```

Expected: `[SUCCESS]`. The M5Stack screen shows "Claude Buddy / starting..." after reset.

- [ ] **Step 5: Commit**

```bash
cd ~/terminal-claude-code-buddy-m5stack
git add firmware/platformio.ini firmware/src/main.cpp
git commit -m "feat(firmware): M5Unified display, rename to Claude-Buddy"
```

---

## Task 11: Firmware — parse status JSON and render the status screen

**Files:**
- Modify: `firmware/src/main.cpp`

- [ ] **Step 1: Add the JSON include and a render function**

In `firmware/src/main.cpp`, add to the includes:

```cpp
#include <ArduinoJson.h>
```

Add this function above `setup()`:

```cpp
// Renders the live status screen. Phase 1 shows counts only — no message
// text or transcript content ever reaches the device.
static void renderStatus(int running, int waiting, int total,
                         const char* msg) {
  M5.Display.fillScreen(TFT_BLACK);
  M5.Display.setTextColor(TFT_WHITE, TFT_BLACK);

  M5.Display.setTextSize(2);
  M5.Display.setCursor(8, 8);
  M5.Display.print("Claude Buddy");

  M5.Display.setTextSize(6);
  M5.Display.setTextColor(TFT_GREEN, TFT_BLACK);
  M5.Display.setCursor(8, 56);
  M5.Display.printf("%d", running);
  M5.Display.setTextSize(2);
  M5.Display.setCursor(8, 120);
  M5.Display.setTextColor(TFT_GREEN, TFT_BLACK);
  M5.Display.print("running");

  M5.Display.setTextSize(6);
  M5.Display.setTextColor(TFT_ORANGE, TFT_BLACK);
  M5.Display.setCursor(170, 56);
  M5.Display.printf("%d", waiting);
  M5.Display.setTextSize(2);
  M5.Display.setCursor(170, 120);
  M5.Display.setTextColor(TFT_ORANGE, TFT_BLACK);
  M5.Display.print("waiting");

  M5.Display.setTextSize(2);
  M5.Display.setTextColor(TFT_WHITE, TFT_BLACK);
  M5.Display.setCursor(8, 160);
  M5.Display.printf("%d sessions", total);
  M5.Display.setCursor(8, 200);
  M5.Display.setTextColor(TFT_DARKGREY, TFT_BLACK);
  M5.Display.print(msg);
}
```

- [ ] **Step 2: Parse incoming JSON in the RX callback**

Replace the body of `RxCallbacks::onWrite` with:

```cpp
  void onWrite(BLECharacteristic* c) override {
    std::string v = c->getValue();
    if (v.empty()) return;
    Serial.print("[rx] ");
    Serial.write(reinterpret_cast<const uint8_t*>(v.data()), v.size());
    Serial.println();

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, v);
    if (err) return;
    if (doc["evt"] == "status") {
      renderStatus(doc["running"] | 0, doc["waiting"] | 0,
                   doc["total"] | 0, doc["msg"] | "");
    }
  }
```

- [ ] **Step 3: Build and flash**

```bash
cd ~/terminal-claude-code-buddy-m5stack/firmware
pio run -e m5stack-core -t upload --upload-port /dev/cu.usbserial-XXXXXXXXXXX
```

Expected: `[SUCCESS]`.

- [ ] **Step 4: Verify rendering with a manual BLE write**

With the firmware flashed, run the bridge venv one-off (the device now advertises as `Claude-Buddy`, so no name override needed):

```bash
cd ~/terminal-claude-code-buddy-m5stack/bridge
.venv/bin/python - <<'EOF'
import asyncio
from buddy_bridge.ble_link import BleLink
from buddy_bridge.protocol import encode_status

async def main():
    link = BleLink()
    print("connected:", await link.connect())
    await link.send(encode_status(3, 1, 5, "3 running · 1 waiting"))
    await asyncio.sleep(2)

asyncio.run(main())
EOF
```

Expected: macOS pairing dialog shows the passkey now displayed on the M5Stack screen; after pairing, the screen shows `3` (green) / `1` (orange) / `5 sessions` / the message line.

- [ ] **Step 5: Commit**

```bash
cd ~/terminal-claude-code-buddy-m5stack
git add firmware/src/main.cpp
git commit -m "feat(firmware): parse status JSON and render status screen"
```

---

## Task 12: End-to-end Phase 1 verification

**Files:** none — this task only runs and observes.

- [ ] **Step 1: Power on the M5Stack and start the bridge**

```bash
cd ~/terminal-claude-code-buddy-m5stack/bridge && .venv/bin/python -m buddy_bridge
```

Expected: `[bridge] listening on …`; within ~15s the bridge connects to `Claude-Buddy` (complete the macOS pairing dialog using the on-screen passkey if prompted).

- [ ] **Step 2: Drive it with real Claude Code sessions**

In separate terminals, start two Claude Code sessions. In one, submit a prompt that takes a while.

Expected on the M5Stack screen, live:
- Opening a session → `sessions` count increases.
- Submitting a prompt → `running` shows `1` (green).
- Assistant finishing its turn → `running` returns to `0`.
- A session asking for input/permission → `waiting` shows `1` (orange).
- Closing a session → `sessions` count decreases.

- [ ] **Step 3: Confirm the privacy guarantee**

Watch the M5Stack serial monitor (`pio device monitor -p /dev/cu.usbserial-XXXXXXXXXXX`) while sessions run.

Expected: every `[rx]` line is a `{"evt":"status",...}` object with only counts and a short `msg`. **No** message text, file paths, file contents, or transcript data appears. If any does, that is a Phase 1 bug — fix before closing the phase.

- [ ] **Step 4: Run the full test suite**

```bash
cd ~/terminal-claude-code-buddy-m5stack/bridge && .venv/bin/python -m pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 5: Final commit and push**

```bash
cd ~/terminal-claude-code-buddy-m5stack
git add -A
git commit -m "test: Phase 1 end-to-end verification complete"
git push
```

---

## Out of scope (later phases)

- **Phase 2:** `PreToolUse` permission race (device-or-keyboard approval), the `prompt` / `prompt_cancel` protocol messages, device buttons (A/C/B), auto-approve toggle, device→bridge notifications on the NUS TX characteristic.
- **Phase 2/polish:** `launchd` user agent so the bridge auto-starts and auto-reconnects (Phase 1 runs the bridge manually in a terminal).
- **Phase 3:** the animated desk-pet character.

## Notes for the implementer

- **Firmware has no unit tests** — embedded code is verified by flashing and observing the screen + serial monitor. That is intentional; do not invent a test harness.
- **BLE pairing** is handled by macOS, not the bridge. The first encrypted access triggers a system dialog; enter the passkey shown on the M5Stack screen. The bond persists across reconnects.
- **The probe** (`~/m5stack-buddy-probe/`) stays as-is — a separate throwaway tool. Phase 1 firmware is a fresh copy that diverges from it.
- **Privacy is load-bearing.** Any task that would put message text or file contents on the BLE link is wrong — re-check against the spec's Privacy section.
