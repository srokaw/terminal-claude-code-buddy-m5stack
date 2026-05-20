# Claude Buddy — Phase 2 (revised): Permission Approval via the PermissionRequest Hook — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Supersedes** `2026-05-19-claude-buddy-phase2-permission-race.md`. That plan built the hook on `PreToolUse` plus a `/dev/tty` keyboard race. Verification proved hooks cannot open `/dev/tty` at all (so that race never ran), and that `PreToolUse` is the wrong hook. This plan rebuilds the hook on `PermissionRequest`.

**Goal:** Approve or deny Claude Code tool-permission prompts from the M5Stack — racing the device buttons against Claude Code's own native terminal prompt — with a device-side auto-approve toggle.

**Architecture:** Claude Code fires a `PermissionRequest` hook whenever it is about to show a native permission prompt. The native terminal prompt appears as normal; concurrently the hook relays the prompt to the Python bridge, which shows it on the M5Stack. If a device button is pressed, the hook returns a decision that overrides the native prompt. If the device is not used (timeout, bridge down, device off), the hook outputs nothing and the native terminal prompt stands. The bridge and firmware are unchanged from the existing `phase2-permission-race` branch — only the hook and its wiring change.

**Tech Stack:** Python 3 (hook + bridge); Claude Code `PermissionRequest` hook; existing `bleak`/`asyncio` bridge and M5Unified firmware.

**Reference:** Design spec `docs/superpowers/specs/2026-05-18-claude-buddy-m5stack-design.md`.

**Why PermissionRequest (not PreToolUse):** `PermissionRequest` fires *only* when Claude Code itself has decided a prompt is needed — auto-allowed tools and `AskUserQuestion` never trigger it. Claude Code's `permissions` config stays the single source of truth; no command-classifying "smart matcher" is needed. Confirmed against the official hooks docs and two reference implementations (`anthropics/claude-desktop-buddy` PR #12; `SnowWarri0r/cc-buddy-bridge`).

---

## What is NOT changing

The existing `phase2-permission-race` branch already has, reviewed and passing:

- `bridge/buddy_bridge/{protocol,permissions,socket_server,ble_link,__main__}.py` — the `PermissionBroker`, the concurrent-read `permission_request`/`prompt_cancel` socket path, BLE TX-notify. **All reused unchanged** — the hook still speaks the same `{"type":"permission_request",...}` / `{"type":"prompt_cancel",...}` socket protocol.
- `firmware/src/main.cpp` — buttons, permission-takeover screen, decision TX-notify, auto-approve. **Reused unchanged** — the firmware is hook-agnostic.

Only `hooks/buddy-permission-hook.py` (full rewrite) and the hook wiring change.

## PermissionRequest hook contract (from the official docs)

- **Input (stdin JSON):** `{"hook_event_name":"PermissionRequest","tool_name":..,"tool_input":{..},"session_id":..,"tool_use_id":..,"permission_suggestions":[..],"cwd":..,"permission_mode":..}`
- **Output to decide (stdout JSON):**
  `{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow"|"deny"}}}`
- **Output nothing / exit 0 / timeout / error:** Claude Code's native permission prompt stands and the user decides.
- **Registered** under the `PermissionRequest` event key in `~/.claude/settings.json`, with an optional per-entry `timeout` (seconds).

---

## Task 1: Rewrite `buddy-permission-hook.py` as a `PermissionRequest` hook

**Files:**
- Rewrite: `hooks/buddy-permission-hook.py`
- Rewrite: `bridge/tests/test_permission_hook.py`

- [ ] **Step 1: Replace the test file**

Overwrite `bridge/tests/test_permission_hook.py` with:

```python
import importlib.util
import os

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
    assert change is not None


def test_build_detail_webfetch_uses_url():
    detail, _ = hook.build_detail("WebFetch", {"url": "https://x.test/a"})
    assert detail == "https://x.test/a"


def test_build_detail_never_includes_file_contents():
    detail, change = hook.build_detail("Write", {
        "file_path": "/tmp/secret.txt", "content": "SENSITIVE BODY TEXT"})
    assert "SENSITIVE BODY TEXT" not in detail
    assert "SENSITIVE BODY TEXT" not in (change or "")


def test_decision_output_allow():
    out = hook.decision_output("allow")
    assert out == {"hookSpecificOutput": {
        "hookEventName": "PermissionRequest",
        "decision": {"behavior": "allow"}}}


def test_decision_output_deny():
    out = hook.decision_output("deny")
    assert out["hookSpecificOutput"]["decision"]["behavior"] == "deny"
    assert out["hookSpecificOutput"]["hookEventName"] == "PermissionRequest"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd bridge && .venv/bin/python -m pytest tests/test_permission_hook.py -v`
Expected: FAIL — the rewritten hook doesn't exist yet (old `decision_output` returns the old `PreToolUse` shape, so `test_decision_output_allow` fails; or import fails).

- [ ] **Step 3: Rewrite the hook**

Overwrite `hooks/buddy-permission-hook.py` with:

```python
#!/usr/bin/env python3
"""Claude Code PermissionRequest hook -> buddy device.

Registered under hooks.PermissionRequest in ~/.claude/settings.json. Claude
Code fires this when it is about to show a native permission prompt. The
native terminal prompt appears as normal; this hook concurrently relays the
prompt to the buddy and, if a button is pressed, returns a decision that
overrides the native prompt. If the buddy is not used (timeout / bridge down /
device off), the hook outputs nothing and the native terminal prompt stands.

Privacy: only the tool call (command / path / URL) is sent to the bridge —
never file contents or diff bodies. See the design spec's Privacy section.
"""
import json
import os
import signal
import socket
import sys
import uuid

# Cross-process contract: must match bridge/buddy_bridge/__main__.py.
SOCK_PATH = os.path.expanduser("~/.claude-buddy/bridge.sock")
DECISION_TIMEOUT = 45.0  # seconds to wait for a buddy button press

_sock = None
_prompt_id = None


def build_detail(tool, tool_input):
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
        return (str(tool_input.get("file_path", "")),
                f"{content.count(chr(10)) + 1} lines")
    if tool in ("WebFetch", "WebSearch"):
        return str(tool_input.get("url") or tool_input.get("query", "")), None
    return tool, None  # generic fallback: tool name only


def decision_output(behavior):
    """Shape the PermissionRequest hook stdout for an allow/deny decision."""
    return {"hookSpecificOutput": {
        "hookEventName": "PermissionRequest",
        "decision": {"behavior": behavior}}}


def _send(sock, obj):
    sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))


def _cancel_and_exit(*_):
    """Timeout or SIGTERM: clear the buddy prompt, yield to the native prompt."""
    if _sock is not None and _prompt_id is not None:
        try:
            _send(_sock, {"type": "prompt_cancel", "id": _prompt_id})
        except OSError:
            pass
    sys.exit(0)  # no JSON on stdout -> native terminal prompt stands


def main():
    global _sock, _prompt_id
    try:
        hook_input = json.load(sys.stdin)
    except (ValueError, OSError):
        sys.exit(0)
    tool = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {}) or {}
    session = hook_input.get("session_id", "")
    if not tool:
        sys.exit(0)
    detail, change = build_detail(tool, tool_input)
    _prompt_id = str(uuid.uuid4())

    try:
        _sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        _sock.connect(SOCK_PATH)
    except OSError:
        sys.exit(0)  # bridge down -> native prompt stands

    signal.signal(signal.SIGTERM, _cancel_and_exit)
    try:
        _send(_sock, {"type": "permission_request", "id": _prompt_id,
                      "session": session, "tool": tool,
                      "detail": detail, "change": change})
        _sock.settimeout(DECISION_TIMEOUT)
        buf = b""
        while b"\n" not in buf:
            chunk = _sock.recv(256)
            if not chunk:
                sys.exit(0)  # bridge closed -> native prompt stands
            buf += chunk
        resp = json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
    except (OSError, ValueError):
        _cancel_and_exit()
        return
    decision = resp.get("decision")
    if decision in ("allow", "deny"):
        print(json.dumps(decision_output(decision)))
    sys.exit(0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Make executable and run the tests**

```bash
chmod +x hooks/buddy-permission-hook.py
cd bridge && .venv/bin/python -m pytest tests/test_permission_hook.py -v
```

Expected: all six tests PASS.

- [ ] **Step 5: Run the full suite to confirm nothing else broke**

Run: `cd bridge && .venv/bin/python -m pytest tests/ -v`
Expected: all tests PASS (the bridge tests are unchanged and still green).

- [ ] **Step 6: Commit**

```bash
git add hooks/buddy-permission-hook.py bridge/tests/test_permission_hook.py
git -c user.name="Claude" -c user.email="noreply@anthropic.com" commit -m "feat(hooks): rebuild permission hook on PermissionRequest

Replaces the PreToolUse + /dev/tty keyboard-race hook (hooks cannot open
/dev/tty, so that race never ran). PermissionRequest fires only when
Claude Code is about to prompt; the native terminal prompt races the
buddy with no terminal drawing."
```

---

## Task 2: Update the hook wiring example

**Files:**
- Modify: `hooks/settings.example.json`

- [ ] **Step 1: Replace the PreToolUse entry with PermissionRequest**

In `hooks/settings.example.json`, replace the `"PreToolUse"` block with:

```json
    "PermissionRequest": [
      {"hooks": [{"type": "command", "command": "$HOME/terminal-claude-code-buddy-m5stack/hooks/buddy-permission-hook.py", "timeout": 60}]}
    ]
```

(The `timeout: 60` gives the hook's own 45 s decision wait headroom before Claude Code cancels it.)

- [ ] **Step 2: Validate the JSON**

Run: `python3 -m json.tool hooks/settings.example.json > /dev/null && echo valid`
Expected: `valid`.

- [ ] **Step 3: Commit**

```bash
git add hooks/settings.example.json
git -c user.name="Claude" -c user.email="noreply@anthropic.com" commit -m "docs(hooks): wire PermissionRequest instead of PreToolUse"
```

---

## Task 3: Wire `PermissionRequest` into the live config (user step)

**Files:**
- Modify: `~/.claude/settings.json`

- [ ] **Step 1: Add the PermissionRequest hook**

Add this key inside the `"hooks"` object of `~/.claude/settings.json`, alongside the Phase 1 entries:

```json
    "PermissionRequest": [
      {"hooks": [{"type": "command", "command": "$HOME/terminal-claude-code-buddy-m5stack/hooks/buddy-permission-hook.py", "timeout": 60}]}
    ]
```

There must be **no** `PreToolUse` entry (the old broken hook was already removed).

- [ ] **Step 2: Validate**

Run: `python3 -m json.tool ~/.claude/settings.json > /dev/null && echo valid`
Expected: `valid`.

This changes live Claude Code behavior, so it is applied by the user (or on explicit request), not in an automated run.

---

## Task 4: End-to-end verification

**Files:** none — verification only.

- [ ] **Step 1: Confirm firmware and bridge are current**

The firmware (`firmware/src/main.cpp`) and bridge are unchanged by this plan. Ensure the latest firmware is flashed:
`cd firmware && /opt/homebrew/bin/pio run -e m5stack-core -t upload --upload-port /dev/cu.usbserial-XXXX`
(Use the real `/dev/cu.usbserial-*` port.)

- [ ] **Step 2: Start the bridge**

Run: `cd bridge && .venv/bin/python -m buddy_bridge`
Expected: `[bridge] listening …`; the bridge connects to `Claude-Buddy`.

- [ ] **Step 3: Device-wins path**

In a fresh Claude Code session, trigger a permission-gated tool call (e.g. a `Bash` command not on your allowlist).

Expected: Claude Code shows its **native** permission prompt in the terminal **and** the M5Stack shows the "Approve Bash?" screen. Press **A** on the device → the native prompt resolves to allow and the tool runs. Press **C** → it is denied.

- [ ] **Step 4: Keyboard-wins path**

Trigger another gated tool call. Answer it in the terminal with Claude Code's native prompt before touching the device.

Expected: the tool call resolves from your terminal answer. The device's prompt screen clears within the hook's 45 s timeout (the hook sends `prompt_cancel` when it gives up).

- [ ] **Step 5: Auto-approve path**

Press **B** on the device — confirm the red `AUTO-APPROVE ON` banner and beep. Trigger a gated tool call.

Expected: it is approved with a short beep and no manual action. Press **B** again to turn it off.

- [ ] **Step 6: Fallback path**

Stop the bridge (Ctrl-C). Trigger a gated tool call.

Expected: the hook can't reach the bridge, outputs nothing, and Claude Code's native terminal prompt works exactly as normal. Claude Code is never blocked.

- [ ] **Step 7: Privacy check**

Trigger `Edit`/`Write` gated calls. Watch the device.

Expected: the device shows the file path and a change size (e.g. `+12/-3 lines`) — never file contents or diff bodies.

- [ ] **Step 8: Final commit and push**

```bash
git add -A
git -c user.name="Claude" -c user.email="noreply@anthropic.com" commit -m "test: Phase 2 (PermissionRequest) end-to-end verification complete"
git push
```

---

## Out of scope

- Mirroring/answering `AskUserQuestion` multi-choice on the device — not reachable with terminal-CLI hooks; would require running Claude via the Agent SDK (`canUseTool`). Deferred indefinitely.
- The animated desk-pet character (Phase 3).
- `launchd` auto-start for the bridge.

## Notes for the implementer

- **The bridge is not modified.** The hook still speaks the same `permission_request` / `prompt_cancel` socket protocol the existing `PermissionBroker` and `socket_server._handle_permission` already handle. Do not change `bridge/`.
- **The firmware is not modified.** It is hook-agnostic.
- **No `/dev/tty`, no `select`, no `termios`** — the rewritten hook does none of that. If you find that code, it means the old hook wasn't fully replaced.
- **Privacy is load-bearing.** `build_detail` must never put file contents or diff bodies into `detail`/`change`; there is an explicit test.
