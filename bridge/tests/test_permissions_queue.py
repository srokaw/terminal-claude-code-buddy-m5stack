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


@pytest.mark.asyncio
async def test_cancel_active_promotes_and_sends_device_cancel():
    sends = []
    b = make_broker(sends)
    t1 = asyncio.create_task(b.request("a", "Bash", "ls", None))
    t2 = asyncio.create_task(b.request("b", "Bash", "pwd", None))
    await asyncio.sleep(0)
    b.cancel("a")  # keyboard won on the active entry
    assert await t1 is None
    await asyncio.sleep(0)
    # device told to clear "a", then "b" promoted.
    assert sends == [("prompt", "a"), ("cancel", "a"), ("prompt", "b")]
    assert b.active_id == "b"
    b.resolve("b", "allow"); await t2


@pytest.mark.asyncio
async def test_cancel_queued_entry_is_pruned_not_cleared_on_device():
    sends = []
    b = make_broker(sends)
    t1 = asyncio.create_task(b.request("a", "Bash", "ls", None))
    t2 = asyncio.create_task(b.request("b", "Bash", "pwd", None))
    await asyncio.sleep(0)
    b.cancel("b")  # the QUEUED entry's hook timed out
    assert await t2 is None
    # No device cancel for b (it was never on screen); a still active.
    assert sends == [("prompt", "a")]
    assert b.active_id == "a" and b.queue_ids == []
    b.resolve("a", "allow"); await t1


@pytest.mark.asyncio
async def test_queue_full_busy_rejects_when_depth_exceeded():
    sends = []
    b = make_broker(sends)
    tasks = [asyncio.create_task(b.request(x, "Bash", x, None))
             for x in ["a", "b", "c", "d"]]
    await asyncio.sleep(0)
    # active=a; queue=[b,c,d] is depth 3 (MAX_DEPTH) -> all admitted.
    assert b.active_id == "a" and b.queue_ids == ["b", "c", "d"]
    assert all(not t.done() for t in tasks)  # none rejected yet
    # A fifth request exceeds MAX_DEPTH -> busy-reject immediately to terminal.
    t5 = asyncio.create_task(b.request("e", "Bash", "e", None))
    assert await t5 is None
    assert b.queue_ids == ["b", "c", "d"]
    for x in ["a", "b", "c", "d"]:
        b.resolve(x, "allow")
        await asyncio.sleep(0)


class FakeLoopTimers:
    """Capture call_later callbacks so tests can fire them deterministically."""
    def __init__(self, real_loop):
        self._real = real_loop
        self.timers = []
    def call_later(self, delay, cb, *args):
        handle = self._real.call_later(1e9, lambda: None)  # never auto-fires
        self.timers.append((delay, cb, args, handle))
        return handle
    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.mark.asyncio
async def test_watchdog_fires_only_as_backstop(monkeypatch):
    sends = []
    b = make_broker(sends)
    real = asyncio.get_running_loop()
    fake = FakeLoopTimers(real)
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: fake)
    t1 = asyncio.create_task(b.request("a", "Bash", "ls", None))
    t2 = asyncio.create_task(b.request("b", "Bash", "pwd", None))
    await asyncio.sleep(0)
    # One watchdog armed for the active entry "a".
    assert len(fake.timers) == 1
    delay, cb, args, _ = fake.timers[0]
    assert delay == 30.0 + 2.0  # BINARY_TIMEOUT + WATCHDOG_MARGIN
    # Fire it: presumes "a" dead -> clears + promotes "b".
    cb(*args)
    assert await t1 is None
    await asyncio.sleep(0)
    assert ("cancel", "a") in sends and ("prompt", "b") in sends
    assert b.active_id == "b"
    b.resolve("b", "allow"); await t2


@pytest.mark.asyncio
async def test_stale_watchdog_does_not_pull_new_entry(monkeypatch):
    sends = []
    b = make_broker(sends)
    real = asyncio.get_running_loop()
    fake = FakeLoopTimers(real)
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: fake)
    t1 = asyncio.create_task(b.request("a", "Bash", "ls", None))
    t2 = asyncio.create_task(b.request("b", "Bash", "pwd", None))
    await asyncio.sleep(0)
    a_delay, a_cb, a_args, _ = fake.timers[0]   # watchdog for "a"
    b.resolve("a", "allow")                      # real signal resolves "a"
    await t1; await asyncio.sleep(0)
    assert b.active_id == "b"
    # Now fire the STALE "a" watchdog: must be a no-op, not touch "b".
    a_cb(*a_args)
    assert not t2.done()
    assert b.active_id == "b"
    b.resolve("b", "allow"); await t2


@pytest.mark.asyncio
async def test_on_busy_for_active_resends_then_fails_after_bound():
    sends = []
    b = make_broker(sends)
    t = asyncio.create_task(b.request("a", "Bash", "ls", None))
    await asyncio.sleep(0)
    assert sends.count(("prompt", "a")) == 1
    for _ in range(3):           # MAX_RESENDS resends
        b.on_busy("a")
    assert sends.count(("prompt", "a")) == 4   # initial + 3 resends
    assert not t.done()
    b.on_busy("a")               # exceeds bound -> fail to terminal
    assert await t is None
    assert b.active_id is None


@pytest.mark.asyncio
async def test_on_busy_for_non_active_is_noop():
    sends = []
    b = make_broker(sends)
    t1 = asyncio.create_task(b.request("a", "Bash", "ls", None))
    t2 = asyncio.create_task(b.request("b", "Bash", "pwd", None))
    await asyncio.sleep(0)
    before = list(sends)
    b.on_busy("b")               # queued id -> no-op
    b.on_busy("zzz")             # unknown id -> no-op
    assert sends == before
    assert not t1.done() and not t2.done()
    b.resolve("a", "allow"); await t1
    await asyncio.sleep(0)
    b.resolve("b", "allow"); await t2


@pytest.mark.asyncio
async def test_resend_active_resends_current():
    sends = []
    b = make_broker(sends)
    t = asyncio.create_task(b.request("a", "Bash", "ls", None))
    await asyncio.sleep(0)
    sends.clear()
    b.resend_active()
    assert sends == [("prompt", "a")]
    b.resolve("a", "allow"); await t


@pytest.mark.asyncio
async def test_resend_active_noop_when_idle():
    sends = []
    b = make_broker(sends)
    b.resend_active()
    assert sends == []


@pytest.mark.asyncio
async def test_promote_with_link_down_fails_to_terminal():
    sends = []
    connected = {"v": False}
    b = PermissionBroker(
        send_prompt=lambda pid, tool, detail, change: sends.append(("prompt", pid)),
        send_cancel=lambda pid: sends.append(("cancel", pid)),
        send_ask=lambda aid, m, q: sends.append(("ask", aid)),
        send_ask_cancel=lambda aid: sends.append(("ask_cancel", aid)),
        link_connected=lambda: connected["v"],
    )
    t = asyncio.create_task(b.request("a", "Bash", "ls", None))
    assert await t is None          # link down -> immediate terminal fallback
    assert sends == []              # nothing sent to the device
    assert b.active_id is None


@pytest.mark.asyncio
async def test_link_down_skips_to_next_connected_entry():
    sends = []
    connected = {"v": True}
    b = PermissionBroker(
        send_prompt=lambda pid, tool, detail, change: sends.append(("prompt", pid)),
        send_cancel=lambda pid: sends.append(("cancel", pid)),
        link_connected=lambda: connected["v"],
    )
    t1 = asyncio.create_task(b.request("a", "Bash", "ls", None))
    t2 = asyncio.create_task(b.request("b", "Bash", "pwd", None))
    await asyncio.sleep(0)
    assert b.active_id == "a"
    connected["v"] = False
    b.cancel("a")                   # clears a; tries to promote b but link down
    assert await t1 is None
    assert await t2 is None         # b also fails to terminal (link down)
    assert b.active_id is None


@pytest.mark.asyncio
async def test_auto_on_drains_permission_backlog():
    sends = []
    b = make_broker(sends)
    ta = asyncio.create_task(b.request("a", "Bash", "ls", None))
    tb = asyncio.create_task(b.request("b", "Bash", "pwd", None))
    await asyncio.sleep(0)
    b.set_auto_approve(True)
    assert await ta == "allow"
    assert await tb == "allow"
    assert b.active_id is None and b.queue_ids == []
    # Active "a" was on screen -> device told to clear it.
    assert ("cancel", "a") in sends


@pytest.mark.asyncio
async def test_auto_drain_does_not_flash_queued_entry_on_device():
    sends = []
    b = make_broker(sends)
    ta = asyncio.create_task(b.request("a", "Bash", "ls", None))
    tb = asyncio.create_task(b.request("b", "Bash", "pwd", None))
    await asyncio.sleep(0)
    assert sends == [("prompt", "a")]      # only the active "a" was ever sent
    b.set_auto_approve(True)
    assert await ta == "allow"
    assert await tb == "allow"
    # "b" must NEVER have been sent to the device (no flash), nor cancelled.
    assert ("prompt", "b") not in sends
    assert ("cancel", "b") not in sends
    # The active "a" was on screen, so it is cleared.
    assert ("cancel", "a") in sends
    assert b.active_id is None and b.queue_ids == []


@pytest.mark.asyncio
async def test_auto_on_leaves_active_ask_but_drains_queued_binary():
    sends = []
    b = make_broker(sends)
    tq = asyncio.create_task(b.ask("q", False, [{"text": "?", "options": []}]))
    await asyncio.sleep(0)                # ask "q" is active
    tb = asyncio.create_task(b.request("b", "Bash", "pwd", None))  # queued binary
    await asyncio.sleep(0)
    b.set_auto_approve(True)
    assert await tb == "allow"            # queued binary drained
    assert not tq.done()                  # active ask untouched
    assert b.active_id == "q" and b.queue_ids == []
    b.resolve_ask("q", [{"label": "x"}]); await tq
