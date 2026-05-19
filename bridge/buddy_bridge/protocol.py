"""Encoding of bridge -> device messages. JSON, one object per line."""
import json


def encode_status(running: int, waiting: int, total: int, msg: str) -> bytes:
    """Encode a live status message for the device.

    Privacy: only counts and a short status string. Never message text,
    file contents, or transcript data.
    """
    obj = {
        "evt": "status",
        "running": running,
        "waiting": waiting,
        "total": total,
        "msg": msg,
    }
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")
