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


def encode_prompt(prompt_id: str, tool: str, detail: str,
                  change: str | None = None) -> bytes:
    """Encode a pending permission prompt for the device.

    `detail` is the complete tool call (full command / path / URL). Never
    file contents or diff bodies — see the spec's Privacy section.
    """
    obj = {"evt": "prompt", "id": prompt_id, "tool": tool, "detail": detail}
    if change is not None:
        obj["change"] = change
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")


def encode_prompt_cancel(prompt_id: str) -> bytes:
    """Tell the device to clear a prompt resolved on the keyboard."""
    obj = {"cmd": "prompt_cancel", "id": prompt_id}
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")


def decode_device_message(line: str) -> dict | None:
    """Parse one device->bridge message. Returns the dict or None if invalid."""
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    cmd = obj.get("cmd")
    if cmd == "permission":
        if obj.get("decision") in ("allow", "deny") and obj.get("id"):
            return {"cmd": "permission", "id": obj["id"],
                    "decision": obj["decision"]}
        return None
    if cmd == "auto":
        if isinstance(obj.get("state"), bool):
            return {"cmd": "auto", "state": obj["state"]}
        return None
    return None
