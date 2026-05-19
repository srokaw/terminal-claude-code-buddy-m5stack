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
        sock_buf = b""
        while True:
            readable, _, _ = select.select(fds, [], [], 60.0)
            if not readable:
                # Timeout: no answer from device or keyboard; yield to Claude.
                return "ask"
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
                data = sock.recv(4096)
                if not data:
                    fds = [f for f in fds if f is not sock]
                    sock = None
                    if tty is None:
                        return "deny"
                    continue
                sock_buf += data
                while b"\n" in sock_buf:
                    raw_line, sock_buf = sock_buf.split(b"\n", 1)
                    if not raw_line.strip():
                        continue
                    try:
                        resp = json.loads(raw_line.decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
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
