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
# DECISION_TIMEOUT and ASK_TIMEOUT must stay below the `timeout` set on the
# PermissionRequest hook entry in ~/.claude/settings.json (currently 90 s) so
# that Claude Code's SIGTERM-cancel relationship holds.
DECISION_TIMEOUT = 30.0  # on-screen seconds for a binary permission
ASK_TIMEOUT = 85.0       # on-screen seconds for an ask (kept < settings.json 90s)

# Tools that are an interaction, not a buddy-approvable action — a question or
# a plan, not a command/file/network action. The device's binary allow/deny is
# meaningless for these, so the hook ignores them and lets Claude Code's native
# prompt handle them entirely.
SKIP_TOOLS = ("ExitPlanMode",)

_sock = None
_prompt_id = None


# Fields known to carry large content. Values replaced by a length summary
# before serialization so the device never receives the actual content.
_CONTENT_FIELDS = ("content", "body", "old_string", "new_string", "text", "data")
_DETAIL_MAX = 200


def _generic_detail(tool_input):
    """Serialize an unknown tool's input safely: summarize content-heavy
    fields by length, cap total detail length. The device sees roughly what
    the native prompt would show, never raw blobs."""
    if not isinstance(tool_input, dict):
        return str(tool_input)[:_DETAIL_MAX]
    summarized = {
        k: (f"<{len(v)} chars>" if k in _CONTENT_FIELDS and isinstance(v, str)
            else v)
        for k, v in tool_input.items()
    }
    s = json.dumps(summarized, separators=(",", ":"))
    return s if len(s) <= _DETAIL_MAX else s[: _DETAIL_MAX - 1] + "…"


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
    # Unknown tool (e.g. user-built MCP): serialize args with big-content
    # fields summarized. Better than just showing the tool name (which
    # would mean blind device approvals) and avoids the per-tool
    # maintenance hazard of fail-closed.
    return _generic_detail(tool_input), None


def ask_decision_output(questions, answers):
    """Build the PermissionRequest stdout that pre-fills an AskUserQuestion.

    `questions` is the original `tool_input["questions"]` (each item carries
    `question` and `options`). `answers` is the device's positional response
    list (one entry per question, either {"label":...} or {"labels":[...]}).
    The resulting `updatedInput.answers` dict is keyed by question text.
    """
    answers_map = {}
    for q, a in zip(questions, answers):
        key = q.get("question", "")
        if "labels" in a:
            answers_map[key] = list(a["labels"])
        else:
            answers_map[key] = a.get("label", "")
    return {"hookSpecificOutput": {
        "hookEventName": "PermissionRequest",
        "decision": {"behavior": "allow",
                     "updatedInput": {
                         "questions": questions,
                         "answers": answers_map}}}}


def decision_output(behavior):
    """Shape the PermissionRequest hook stdout for an allow/deny decision."""
    return {"hookSpecificOutput": {
        "hookEventName": "PermissionRequest",
        "decision": {"behavior": behavior}}}


def _send(sock, obj):
    sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))


# Set by main() so _cancel_and_exit knows which family this run belongs to.
_ask_mode = False


def _cancel_and_exit(*_):
    """Timeout or SIGTERM: clear the buddy screen, yield to the native prompt."""
    if _sock is not None and _prompt_id is not None:
        cancel_type = "ask_cancel" if _ask_mode else "prompt_cancel"
        try:
            _send(_sock, {"type": cancel_type, "id": _prompt_id})
        except OSError:
            pass
    sys.exit(0)


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
        while True:
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    msg = json.loads(line.decode("utf-8"))
                except ValueError:
                    continue
                if msg.get("type") == "active":
                    deadline = time.monotonic() + DECISION_TIMEOUT  # on-screen now
                    continue
                decision = msg.get("decision")
                return decision if decision in ("allow", "deny") else None
            remaining = deadline - time.monotonic()
            if remaining <= 0 or len(buf) > 65536:
                return None
            sock.settimeout(remaining)
            chunk = sock.recv(256)
            if not chunk:
                return None
            buf += chunk
    except (OSError, ValueError):
        return None


def request_answers(ask_id, session, multi_select, questions):
    """Connect to the bridge, send an ask_request, return the answers list.

    Returns the list of per-question answers (each {"label":...} or
    {"labels":[...]}), or None on bridge down / timeout / cancel / closed /
    malformed. Never calls sys.exit — safe to call from tests.
    """
    global _sock, _prompt_id
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(SOCK_PATH)
    except OSError:
        return None
    _sock = sock
    _prompt_id = ask_id
    try:
        _send(sock, {"type": "ask_request", "id": ask_id,
                     "session": session,
                     "multiSelect": multi_select,
                     "questions": questions})
        deadline = time.monotonic() + ASK_TIMEOUT
        buf = b""
        while True:
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    msg = json.loads(line.decode("utf-8"))
                except ValueError:
                    continue
                if msg.get("type") == "active":
                    deadline = time.monotonic() + ASK_TIMEOUT
                    continue
                answers = msg.get("answers")
                return answers if isinstance(answers, list) else None
            remaining = deadline - time.monotonic()
            if remaining <= 0 or len(buf) > 65536:
                return None
            sock.settimeout(remaining)
            chunk = sock.recv(512)
            if not chunk:
                return None
            buf += chunk
    except (OSError, ValueError):
        return None


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

    global _ask_mode
    _prompt_id = str(uuid.uuid4())
    signal.signal(signal.SIGTERM, _cancel_and_exit)

    if tool == "AskUserQuestion":
        _ask_mode = True
        questions = tool_input.get("questions", []) or []
        multi_select = any(q.get("multiSelect") for q in questions)
        # Strip down to the fields the device needs (text + options).
        device_qs = [{"text": q.get("question", ""),
                      "options": [{"label": o.get("label", ""),
                                   "desc":  o.get("description", "")}
                                  for o in (q.get("options") or [])]}
                     for q in questions]
        answers = request_answers(_prompt_id, session, multi_select, device_qs)
        if answers is not None and len(answers) == len(questions):
            print(json.dumps(ask_decision_output(questions, answers)))
            sys.exit(0)
        _cancel_and_exit()
        return

    # Default branch: binary permission prompt.
    detail, change = build_detail(tool, tool_input)
    decision = request_decision(_prompt_id, session, tool, detail, change)
    if decision is not None:
        print(json.dumps(decision_output(decision)))
        sys.exit(0)
    _cancel_and_exit()


if __name__ == "__main__":
    main()
