import importlib.util
import json
import os
import socket
import tempfile
import threading
import time

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


def test_request_decision_bridge_down(tmp_path, monkeypatch):
    """When no server is listening at SOCK_PATH, returns None without raising."""
    dead_path = tempfile.mktemp(prefix="buddy_dead_", suffix=".sock", dir="/tmp")
    monkeypatch.setattr(hook, "SOCK_PATH", dead_path)
    result = hook.request_decision("pid-1", "sess-1", "Bash", "ls", None)
    assert result is None


def test_skip_tools_includes_exit_plan_mode():
    # AskUserQuestion was removed from SKIP_TOOLS in Phase 2.5 (handled by hook branch).
    assert "ExitPlanMode" in hook.SKIP_TOOLS
    assert "AskUserQuestion" not in hook.SKIP_TOOLS


def test_request_decision_gets_allow(monkeypatch):
    """A fake AF_UNIX server that replies allow -> request_decision returns 'allow'."""
    sock_path = tempfile.mktemp(prefix="buddy_test_", suffix=".sock", dir="/tmp")
    monkeypatch.setattr(hook, "SOCK_PATH", sock_path)

    server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server_sock.bind(sock_path)
    server_sock.listen(1)

    def _serve():
        conn, _ = server_sock.accept()
        conn.recv(4096)  # consume the permission_request line
        conn.sendall(json.dumps({"decision": "allow"}).encode("utf-8") + b"\n")
        conn.close()
        server_sock.close()

    t = threading.Thread(target=_serve, daemon=True)
    t.start()

    result = hook.request_decision("pid-2", "sess-2", "Bash", "echo hi", None)
    t.join(timeout=5)
    assert result == "allow"


def test_active_message_resets_deadline(monkeypatch):
    """An {"type":"active"} message must reset the on-screen deadline.

    With DECISION_TIMEOUT shrunk to 0.2s, the fake bridge waits 0.15s (still
    inside the original budget) then sends `active`, then waits another 0.15s
    before sending the real decision. The decision therefore arrives ~0.30s
    after the request was sent — past the original 0.2s deadline. The only way
    request_decision can still return "allow" is if the `active` message at
    ~0.15s reset the deadline (giving a fresh 0.2s, ample for the second
    0.15s sleep); otherwise it would time out at 0.2s and return None.
    """
    monkeypatch.setattr(hook, "DECISION_TIMEOUT", 0.2)
    sock_path = tempfile.mktemp(prefix="buddy_reset_", suffix=".sock", dir="/tmp")
    monkeypatch.setattr(hook, "SOCK_PATH", sock_path)

    server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server_sock.bind(sock_path)
    server_sock.listen(1)

    def _serve():
        conn, _ = server_sock.accept()
        conn.recv(4096)  # consume the permission_request line
        # Send `active` near the end of, but inside, the original 0.2s budget.
        time.sleep(0.15)
        conn.sendall(json.dumps({"type": "active", "id": "pid-reset"}).encode("utf-8") + b"\n")
        # Total elapsed at decision (~0.30s) exceeds the original 0.2s budget,
        # but is well within the fresh 0.2s the reset grants from ~0.15s.
        time.sleep(0.15)
        conn.sendall(json.dumps({"decision": "allow"}).encode("utf-8") + b"\n")
        conn.close()
        server_sock.close()

    t = threading.Thread(target=_serve, daemon=True)
    t.start()

    result = hook.request_decision("pid-reset", "sess-reset", "Bash", "echo hi", None)
    t.join(timeout=5)
    assert result == "allow"


def test_active_with_insufficient_budget_bails_to_terminal(monkeypatch):
    """If queue wait consumed almost all of HOOK_PROCESS_BUDGET, an `active`
    promotion must fall back to the terminal (return None) rather than promise an
    on-screen window the process can't survive before Claude Code's external
    SIGTERM. Here we leave ~1s of budget, below MIN_ON_SCREEN (5s)."""
    monkeypatch.setattr(hook, "_HOOK_START",
                        time.monotonic() - (hook.HOOK_PROCESS_BUDGET - 1.0))
    sock_path = tempfile.mktemp(prefix="buddy_budget_", suffix=".sock", dir="/tmp")
    monkeypatch.setattr(hook, "SOCK_PATH", sock_path)

    server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server_sock.bind(sock_path)
    server_sock.listen(1)

    def _serve():
        conn, _ = server_sock.accept()
        conn.recv(4096)  # consume the permission_request line
        conn.sendall(json.dumps({"type": "active", "id": "pid-budget"}).encode("utf-8") + b"\n")
        # A decision follows, but the hook should already have bailed on `active`.
        conn.sendall(json.dumps({"decision": "allow"}).encode("utf-8") + b"\n")
        conn.close()
        server_sock.close()

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    result = hook.request_decision("pid-budget", "sess", "Bash", "echo hi", None)
    t.join(timeout=5)
    assert result is None


def test_build_detail_unknown_tool_serializes_args():
    detail, change = hook.build_detail("MyCustomTool",
                                       {"url": "https://x.test", "mode": "write"})
    assert change is None
    # Detail is JSON containing the args, not just the tool name.
    assert "https://x.test" in detail
    assert "mode" in detail
    assert detail != "MyCustomTool"


def test_build_detail_unknown_tool_summarizes_content_fields():
    big = "X" * 5000
    detail, _ = hook.build_detail("MyMcpTool",
                                  {"file": "/tmp/x", "body": big})
    # The 5000-char body must NEVER appear in the detail.
    assert big not in detail
    assert "<5000 chars>" in detail
    assert "/tmp/x" in detail


def test_build_detail_unknown_tool_caps_length():
    detail, _ = hook.build_detail("MyMcpTool",
                                  {"a": "x" * 1000, "b": "y" * 1000})
    # 'a' and 'b' aren't on the content-field denylist, so they get
    # included literally — but the whole detail must still be capped.
    assert len(detail) <= 200


def test_build_detail_unknown_tool_non_dict_input():
    detail, _ = hook.build_detail("WeirdTool", "just-a-string")
    assert detail == "just-a-string"


def test_ask_no_longer_in_skip_tools():
    # SKIP_TOOLS used to include AskUserQuestion (Phase 2 stop-gap).
    # Phase 2.5 handles it; the skip set should be exactly ExitPlanMode.
    assert hook.SKIP_TOOLS == ("ExitPlanMode",)


def test_ask_decision_output_shape():
    out = hook.ask_decision_output(
        questions=[{"question": "Q1?", "options": [{"label": "A"}, {"label": "B"}]}],
        answers=[{"label": "A"}])
    assert out == {"hookSpecificOutput": {
        "hookEventName": "PermissionRequest",
        "decision": {"behavior": "allow",
                     "updatedInput": {
                         "questions": [{"question": "Q1?",
                                        "options": [{"label": "A"}, {"label": "B"}]}],
                         "answers": {"Q1?": "A"}}}}}


def test_ask_decision_output_multi_select():
    out = hook.ask_decision_output(
        questions=[{"question": "Pick many?", "options": [
            {"label": "A"}, {"label": "B"}, {"label": "C"}]}],
        answers=[{"labels": ["A", "C"]}])
    assert (out["hookSpecificOutput"]["decision"]["updatedInput"]["answers"]
            == {"Pick many?": ["A", "C"]})


def test_ask_decision_output_multi_question():
    out = hook.ask_decision_output(
        questions=[{"question": "Q1?", "options": [{"label": "A"}]},
                   {"question": "Q2?", "options": [{"label": "B"}]}],
        answers=[{"label": "A"}, {"label": "B"}])
    assert (out["hookSpecificOutput"]["decision"]["updatedInput"]["answers"]
            == {"Q1?": "A", "Q2?": "B"})
