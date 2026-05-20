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


def encode_auto_fired(tool: str) -> bytes:
    """Encode a notification that the bridge just auto-approved a tool call."""
    obj = {"evt": "auto_fired", "tool": tool}
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")


def encode_get_auto() -> bytes:
    """Bridge-driven query: ask the device for its current auto-approve state."""
    return b'{"cmd":"get_auto"}\n'


def encode_ask_request(prompt_id: str, multi_select: bool,
                       questions: list) -> bytes:
    """Encode an AskUserQuestion prompt for the device.

    `questions` is a list of `{"text": ..., "options": [{"label": ..., "desc": ...}, ...]}`
    dicts. Question text and option labels/descriptions go to the device verbatim
    — the user is reading them to decide. Never any other tool input or
    transcript content.
    """
    obj = {"evt": "ask", "id": prompt_id,
           "multiSelect": multi_select,
           "questions": questions}
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")


def encode_ask_cancel(prompt_id: str) -> bytes:
    """Tell the device to clear an ask screen resolved on the keyboard."""
    obj = {"cmd": "ask_cancel", "id": prompt_id}
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
    if cmd == "prompt_busy":
        if obj.get("id"):
            return {"cmd": "prompt_busy", "id": obj["id"]}
        return None
    if cmd == "ask_answer":
        answers = obj.get("answers")
        if not obj.get("id") or not isinstance(answers, list):
            return None
        # Each answer must be {"label": str} OR {"labels": [str, ...]}.
        for a in answers:
            if not isinstance(a, dict):
                return None
            if "label" in a and isinstance(a["label"], str):
                continue
            if "labels" in a and isinstance(a["labels"], list) and \
                    all(isinstance(x, str) for x in a["labels"]):
                continue
            return None
        return {"cmd": "ask_answer", "id": obj["id"], "answers": answers}
    return None
