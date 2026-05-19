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
