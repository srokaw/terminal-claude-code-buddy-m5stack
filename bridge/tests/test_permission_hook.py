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
