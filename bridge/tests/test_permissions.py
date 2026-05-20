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
    assert sent == [("p1", "Bash", "ls -la", None)]
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


@pytest.mark.asyncio
async def test_duplicate_prompt_id_does_not_orphan_second_request():
    """Regression: A's finally-pop must not remove B's future when prompt_id is reused."""
    broker, _, _ = make_broker()

    # Task A starts waiting on "dup"
    task_a = asyncio.create_task(broker.request("dup", "Bash", "cmd_a", None))
    await asyncio.sleep(0.05)  # let A install its future

    # Task B arrives with the same prompt_id; this resolves A to "deny"
    # and installs B's own future in _pending["dup"]
    task_b = asyncio.create_task(broker.request("dup", "Bash", "cmd_b", None))
    await asyncio.sleep(0.05)  # let B install its future; A's finally runs here

    # A should have been resolved to "deny" by B's arrival
    assert await task_a == "deny"

    # Resolve B explicitly — if A's finally orphaned B, this resolve does nothing
    # and task_b hangs, so we give it a short timeout
    broker.resolve("dup", "allow")
    result_b = await asyncio.wait_for(task_b, timeout=1.0)
    assert result_b == "allow"
