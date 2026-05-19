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
