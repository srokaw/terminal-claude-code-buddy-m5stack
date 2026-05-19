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
import time
import uuid

# Cross-process contract: must match bridge/buddy_bridge/__main__.py.
SOCK_PATH = os.path.expanduser("~/.claude-buddy/bridge.sock")
# DECISION_TIMEOUT must stay below the `timeout` set on the PermissionRequest
# hook entry in ~/.claude/settings.json (currently 60 s) so that Claude Code's
# SIGTERM-cancel relationship holds.
DECISION_TIMEOUT = 45.0  # seconds to wait for a buddy button press

# Tools that are an interaction, not a buddy-approvable action — a question or
# a plan, not a command/file/network action. The device's binary allow/deny is
# meaningless for these, so the hook ignores them and lets Claude Code's native
# prompt handle them entirely.
SKIP_TOOLS = ("AskUserQuestion", "ExitPlanMode")

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


def request_decision(prompt_id, session, tool, detail, change):
    """Connect to the bridge, send a permission_request, and return the decision.

    Returns "allow", "deny", or None (bridge down / timeout / closed /
    malformed).  Never calls sys.exit — safe to call from tests.
    """
    global _sock, _prompt_id
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(SOCK_PATH)
    except OSError:
        return None  # bridge down -> native prompt stands

    _sock = sock
    _prompt_id = prompt_id
    try:
        _send(sock, {"type": "permission_request", "id": prompt_id,
                     "session": session, "tool": tool,
                     "detail": detail, "change": change})
        deadline = time.monotonic() + DECISION_TIMEOUT
        buf = b""
        while b"\n" not in buf:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or len(buf) > 65536:
                return None  # deadline/overflow -> caller decides to cancel
            sock.settimeout(remaining)
            chunk = sock.recv(256)
            if not chunk:
                return None  # bridge closed -> native prompt stands
            buf += chunk
        resp = json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
    except (OSError, ValueError):
        return None  # I/O or parse error -> native prompt stands
    decision = resp.get("decision")
    return decision if decision in ("allow", "deny") else None


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
    if tool in SKIP_TOOLS:
        sys.exit(0)  # native prompt handles these; the buddy never shows them
    detail, change = build_detail(tool, tool_input)
    _prompt_id = str(uuid.uuid4())

    signal.signal(signal.SIGTERM, _cancel_and_exit)
    decision = request_decision(_prompt_id, session, tool, detail, change)
    if decision is not None:
        print(json.dumps(decision_output(decision)))
        sys.exit(0)
    # No decision (timeout / bridge down / error): try to clear any buddy prompt
    # then let the native terminal prompt stand (exit 0, no stdout).
    _cancel_and_exit()


if __name__ == "__main__":
    main()
