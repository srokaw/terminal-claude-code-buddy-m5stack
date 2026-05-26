import asyncio
import pytest
from buddy_bridge.permissions import PermissionBroker


def make_broker(sends):
    """Broker whose device-sends append (kind, id) to `sends`; link always connected."""
    return PermissionBroker(
        send_prompt=lambda pid, tool, detail, change: sends.append(("prompt", pid)),
        send_cancel=lambda pid: sends.append(("cancel", pid)),
        send_auto_fired=lambda tool: sends.append(("auto_fired", tool)),
        send_ask=lambda aid, multi, qs: sends.append(("ask", aid)),
        send_ask_cancel=lambda aid: sends.append(("ask_cancel", aid)),
        link_connected=lambda: True,
    )


@pytest.mark.asyncio
async def test_second_request_queues_not_sent():
    sends = []
    b = make_broker(sends)
    t1 = asyncio.create_task(b.request("a", "Bash", "ls", None))
    t2 = asyncio.create_task(b.request("b", "Bash", "pwd", None))
    await asyncio.sleep(0)  # let both admit
    # Only the first reached the device; the second is queued.
    assert sends == [("prompt", "a")]
    assert b.active_id == "a"
    assert b.queue_ids == ["b"]
    # Resolve the active -> second promotes and is sent.
    b.resolve("a", "allow")
    assert await t1 == "allow"
    await asyncio.sleep(0)
    assert sends == [("prompt", "a"), ("prompt", "b")]
    assert b.active_id == "b"
    assert b.queue_ids == []
    b.resolve("b", "deny")
    assert await t2 == "deny"
    assert b.active_id is None


@pytest.mark.asyncio
async def test_fifo_order_three_requests():
    sends = []
    b = make_broker(sends)
    tasks = [asyncio.create_task(b.request(x, "Bash", x, None)) for x in "abc"]
    await asyncio.sleep(0)
    assert b.active_id == "a" and b.queue_ids == ["b", "c"]
    b.resolve("a", "allow"); await tasks[0]; await asyncio.sleep(0)
    assert b.active_id == "b" and b.queue_ids == ["c"]
    b.resolve("b", "allow"); await tasks[1]; await asyncio.sleep(0)
    assert b.active_id == "c" and b.queue_ids == []
    b.resolve("c", "allow"); await tasks[2]
    assert b.active_id is None
