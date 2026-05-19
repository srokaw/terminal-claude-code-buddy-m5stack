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
async def test_cancel_makes_request_return_deny():
    broker, _, cancelled = make_broker()
    task = asyncio.create_task(broker.request("p1", "Bash", "ls", None))
    await asyncio.sleep(0.05)
    broker.cancel("p1")
    # keyboard won; the bridge side resolves to "deny" so it stops waiting
    assert await task == "deny"
    assert cancelled == ["p1"]


@pytest.mark.asyncio
async def test_resolve_unknown_id_is_safe():
    broker, _, _ = make_broker()
    broker.resolve("never", "allow")  # must not raise


def test_auto_approve_default_off():
    broker, _, _ = make_broker()
    assert broker.auto_approve is False
