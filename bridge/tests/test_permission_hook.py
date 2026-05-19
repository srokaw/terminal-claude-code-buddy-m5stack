import importlib.util
import json
import os
import socket
import tempfile
import threading

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


def test_skip_tools_includes_interaction_tools():
    assert "AskUserQuestion" in hook.SKIP_TOOLS
    assert "ExitPlanMode" in hook.SKIP_TOOLS


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
