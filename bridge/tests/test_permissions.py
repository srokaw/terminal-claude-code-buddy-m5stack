import asyncio
import pytest

from buddy_bridge.permissions import PermissionBroker


def make_broker():
    sent, cancelled = [], []
    broker = PermissionBroker(
        send_prompt=lambda *a: sent.append(a),
        send_cancel=lambda pid: cancelled.append(pid))
    return broker, sent, cancelled


@pytest.mark.asyncio
async def test_auto_approve_returns_allow_immediately():
    broker, sent, _ = make_broker()
    broker.set_auto_approve(True)
    decision = await broker.request("p1", "Bash", "ls", None)
    assert decision == "allow"
    assert sent == []  # device never prompted in auto mode


@pytest.mark.asyncio
async def test_request_sends_prompt_and_awaits_resolve():
    broker, sent, _ = make_broker()
    task = asyncio.create_task(broker.request("p1", "Bash", "ls -la", None))
    await asyncio.sleep(0.05)
    assert sent == [("p1", "Bash", "ls -la", None, "")]
    broker.resolve("p1", "deny")
    assert await task == "deny"


@pytest.mark.asyncio
async def test_cancel_makes_request_return_none():
    broker, _, cancelled = make_broker()
    task = asyncio.create_task(broker.request("p1", "Bash", "ls", None))
    await asyncio.sleep(0.05)
    broker.cancel("p1")
    assert await task is None
    assert cancelled == ["p1"]


@pytest.mark.asyncio
async def test_resolve_unknown_id_is_safe():
    broker, _, _ = make_broker()
    broker.resolve("never", "allow")  # must not raise


def test_auto_approve_default_off():
    broker, _, _ = make_broker()
    assert broker.auto_approve is False


@pytest.mark.asyncio
async def test_auto_approve_calls_send_auto_fired():
    fired = []
    broker = PermissionBroker(
        send_prompt=lambda *a: None,
        send_cancel=lambda pid: None,
        send_auto_fired=lambda tool: fired.append(tool))
    broker.set_auto_approve(True)
    decision = await broker.request("p1", "Bash", "ls", None)
    assert decision == "allow"
    assert fired == ["Bash"]


# NOTE: The old test_duplicate_prompt_id_does_not_orphan_second_request was
# removed with the FIFO-queue rewrite. The queue keys entries by prompt_id
# (_entries[id]), so it assumes ids are unique. That holds in production: the
# hook generates a fresh uuid4 per invocation. The old duplicate-id eviction
# behavior no longer exists by design.


@pytest.mark.asyncio
async def test_ask_request_resolves_with_answers():
    sent = []
    broker = PermissionBroker(
        send_prompt=lambda *a: None,
        send_cancel=lambda *a: None,
        send_ask=lambda pid, ms, qs, session="": sent.append(("ask", pid, ms, qs)),
        send_ask_cancel=lambda pid: sent.append(("cancel", pid)))
    task = asyncio.create_task(broker.ask("askid",
        multi_select=False,
        questions=[{"text": "Q?", "options": [{"label": "A", "desc": ""}]}]))
    await asyncio.sleep(0)  # let the broker send the request
    assert sent[0][0] == "ask"
    broker.resolve_ask("askid", [{"label": "A"}])
    answers = await task
    assert answers == [{"label": "A"}]


@pytest.mark.asyncio
async def test_ask_request_cancel_returns_none():
    broker = PermissionBroker(
        send_prompt=lambda *a: None, send_cancel=lambda *a: None,
        send_ask=lambda *a: None, send_ask_cancel=lambda *a: None)
    task = asyncio.create_task(broker.ask("askid", multi_select=False,
                                          questions=[]))
    await asyncio.sleep(0)
    broker.cancel_ask("askid")
    assert await task is None


@pytest.mark.asyncio
async def test_ask_and_permission_dont_collide():
    """Both futures can be in flight under different ids; resolving one
    doesn't affect the other."""
    broker = PermissionBroker(
        send_prompt=lambda *a: None, send_cancel=lambda *a: None,
        send_ask=lambda *a: None, send_ask_cancel=lambda *a: None)
    perm_task = asyncio.create_task(broker.request("p1", "Bash", "ls", None))
    ask_task = asyncio.create_task(broker.ask("a1", False, []))
    await asyncio.sleep(0)
    broker.resolve("p1", "allow")
    broker.resolve_ask("a1", [{"label": "X"}])
    assert await perm_task == "allow"
    assert await ask_task == [{"label": "X"}]


@pytest.mark.asyncio
async def test_cancel_unknown_permission_id_is_safe_noop():
    broker = PermissionBroker(
        send_prompt=lambda *a: None, send_cancel=lambda *a: None,
        send_ask=lambda *a: None, send_ask_cancel=lambda *a: None)
    # No pending permission with this id; must not raise.
    broker.cancel("does-not-exist")


@pytest.mark.asyncio
async def test_cancel_ask_resolves_pending_ask_to_none():
    broker = PermissionBroker(
        send_prompt=lambda *a: None, send_cancel=lambda *a: None,
        send_ask=lambda *a: None, send_ask_cancel=lambda *a: None)
    task = asyncio.create_task(broker.ask("a9", False, []))
    await asyncio.sleep(0)
    broker.cancel_ask("a9")
    assert await task is None
