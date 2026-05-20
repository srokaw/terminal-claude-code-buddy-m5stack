import json
from buddy_bridge.protocol import encode_status


def test_encode_status_is_one_json_line():
    raw = encode_status(running=2, waiting=1, total=4, msg="2 running · 1 waiting")
    assert isinstance(raw, bytes)
    assert raw.endswith(b"\n")
    assert raw.count(b"\n") == 1


def test_encode_status_fields():
    raw = encode_status(running=2, waiting=1, total=4, msg="hi")
    obj = json.loads(raw.decode("utf-8"))
    assert obj == {"evt": "status", "running": 2, "waiting": 1, "total": 4, "msg": "hi"}


from buddy_bridge.protocol import (
    encode_prompt, encode_prompt_cancel, decode_device_message)


def test_encode_prompt_without_change():
    obj = json.loads(encode_prompt("p1", "Bash", "ls -la").decode())
    assert obj == {"evt": "prompt", "id": "p1", "tool": "Bash", "detail": "ls -la"}


def test_encode_prompt_with_change():
    obj = json.loads(encode_prompt("p1", "Edit", "/tmp/x.py", "+3/-1").decode())
    assert obj["change"] == "+3/-1"
    assert obj["evt"] == "prompt"


def test_encode_prompt_cancel():
    obj = json.loads(encode_prompt_cancel("p1").decode())
    assert obj == {"cmd": "prompt_cancel", "id": "p1"}


def test_encode_auto_fired():
    from buddy_bridge.protocol import encode_auto_fired
    obj = json.loads(encode_auto_fired("Bash").decode())
    assert obj == {"evt": "auto_fired", "tool": "Bash"}


def test_decode_permission_decision():
    out = decode_device_message('{"cmd":"permission","id":"p1","decision":"allow"}')
    assert out == {"cmd": "permission", "id": "p1", "decision": "allow"}


def test_decode_auto_toggle():
    out = decode_device_message('{"cmd":"auto","state":true}')
    assert out == {"cmd": "auto", "state": True}


def test_decode_rejects_bad_decision():
    assert decode_device_message('{"cmd":"permission","id":"p1","decision":"maybe"}') is None


def test_decode_rejects_garbage():
    assert decode_device_message("not json") is None
    assert decode_device_message('{"cmd":"unknown"}') is None


def test_encode_get_auto():
    from buddy_bridge.protocol import encode_get_auto
    obj = json.loads(encode_get_auto().decode())
    assert obj == {"cmd": "get_auto"}


def test_decode_prompt_busy():
    out = decode_device_message('{"cmd":"prompt_busy","id":"p1"}')
    assert out == {"cmd": "prompt_busy", "id": "p1"}


def test_decode_prompt_busy_requires_id():
    assert decode_device_message('{"cmd":"prompt_busy"}') is None


def test_encode_ask_request_minimal():
    from buddy_bridge.protocol import encode_ask_request
    out = encode_ask_request(
        "abc", multi_select=False,
        questions=[{"text": "Which pkg manager?",
                    "options": [{"label": "npm",  "desc": "Default"},
                                {"label": "pnpm", "desc": "Fast"}]}])
    assert out.endswith(b"\n")
    obj = json.loads(out)
    assert obj == {
        "evt": "ask", "id": "abc", "multiSelect": False,
        "questions": [{"text": "Which pkg manager?",
                       "options": [{"label": "npm",  "desc": "Default"},
                                   {"label": "pnpm", "desc": "Fast"}]}]}


def test_encode_ask_cancel():
    from buddy_bridge.protocol import encode_ask_cancel
    out = encode_ask_cancel("abc")
    assert out == b'{"cmd":"ask_cancel","id":"abc"}\n'


def test_decode_ask_answer_single_select():
    from buddy_bridge.protocol import decode_device_message
    msg = decode_device_message(
        '{"cmd":"ask_answer","id":"abc","answers":[{"label":"pnpm"}]}')
    assert msg == {"cmd": "ask_answer", "id": "abc",
                   "answers": [{"label": "pnpm"}]}


def test_decode_ask_answer_multi_select():
    from buddy_bridge.protocol import decode_device_message
    msg = decode_device_message(
        '{"cmd":"ask_answer","id":"abc","answers":[{"labels":["A","B"]}]}')
    assert msg == {"cmd": "ask_answer", "id": "abc",
                   "answers": [{"labels": ["A", "B"]}]}


def test_decode_ask_answer_requires_id_and_answers():
    from buddy_bridge.protocol import decode_device_message
    assert decode_device_message('{"cmd":"ask_answer"}') is None
    assert decode_device_message('{"cmd":"ask_answer","id":"abc"}') is None
    assert decode_device_message(
        '{"cmd":"ask_answer","answers":[{"label":"x"}]}') is None
