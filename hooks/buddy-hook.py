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

# Cross-process contract: this path MUST match the SOCK_PATH in
# bridge/buddy_bridge/__main__.py — both files use the same socket.
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
