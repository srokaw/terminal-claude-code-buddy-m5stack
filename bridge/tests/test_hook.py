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
