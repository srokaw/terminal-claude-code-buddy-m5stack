"""Unix-domain-socket server receiving hook events from Claude Code."""
import asyncio
import json
import os
from typing import Callable

from buddy_bridge.state import SessionRegistry


def apply_event(reg: SessionRegistry, event: dict) -> None:
    """Apply one hook event to the registry. Never raises on bad input."""
    etype = event.get("type")
    session = event.get("session")
    if not session:
        return
    if etype == "start":
        reg.start(session)
    elif etype == "end":
        reg.end(session)
    elif etype == "state":
        state = event.get("state")
        if state in ("idle", "running", "waiting"):
            reg.set_state(session, state)


async def _handle_permission(reader, writer, broker, event) -> None:
    """Race broker.request() against further lines on the same connection.

    This keeps the read loop alive so prompt_cancel and EOF are noticed
    without parking the handler. On EOF (hook disconnected) the pending
    broker future is cleaned up. On prompt_cancel the broker future is
    resolved and the response is sent back.
    """
    pid = event.get("id", "")

    def send_active() -> None:
        try:
            writer.write((json.dumps({"type": "active", "id": pid}) + "\n").encode())
        except (ConnectionError, OSError):
            pass

    req_task = asyncio.create_task(broker.request(
        pid, event.get("tool", ""), event.get("detail", ""),
        event.get("change"), send_active=send_active))
    read_task = asyncio.create_task(reader.readline())
    while True:
        done, _ = await asyncio.wait(
            {req_task, read_task}, return_when=asyncio.FIRST_COMPLETED)
        if read_task in done:
            line = read_task.result()
            if not line:                       # hook disconnected
                broker.cancel(pid)             # resolve+clean the pending future
                req_task.cancel()
                return
            try:
                msg = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                msg = {}
            if msg.get("type") == "prompt_cancel" and msg.get("id") == pid:
                broker.cancel(pid)             # keyboard won
            read_task = asyncio.create_task(reader.readline())
        if req_task in done:
            read_task.cancel()
            break
    try:
        decision = await req_task
    except asyncio.CancelledError:
        return
    try:
        writer.write((json.dumps({"decision": decision}) + "\n").encode())
        await writer.drain()
    except (ConnectionError, OSError):
        pass


async def _handle_ask(reader, writer, broker, event) -> None:
    """Race broker.ask() against further lines on the same connection.

    Mirrors _handle_permission: on EOF or ask_cancel from the hook, cancel
    the broker future and stop. On broker future completion, write the
    answers (or null) back to the hook.
    """
    aid = event.get("id", "")
    multi = bool(event.get("multiSelect", False))
    questions = event.get("questions", []) or []

    def send_active() -> None:
        try:
            writer.write((json.dumps({"type": "active", "id": aid}) + "\n").encode())
        except (ConnectionError, OSError):
            pass

    req_task = asyncio.create_task(
        broker.ask(aid, multi, questions, send_active=send_active))
    read_task = asyncio.create_task(reader.readline())
    while True:
        done, _ = await asyncio.wait(
            {req_task, read_task}, return_when=asyncio.FIRST_COMPLETED)
        if read_task in done:
            line = read_task.result()
            if not line:
                broker.cancel_ask(aid)
                req_task.cancel()
                return
            try:
                msg = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                msg = {}
            if msg.get("type") == "ask_cancel" and msg.get("id") == aid:
                broker.cancel_ask(aid)
            read_task = asyncio.create_task(reader.readline())
        if req_task in done:
            read_task.cancel()
            break
    try:
        answers = await req_task
    except asyncio.CancelledError:
        return
    try:
        writer.write((json.dumps({"answers": answers}) + "\n").encode())
        await writer.drain()
    except (ConnectionError, OSError):
        pass


async def serve(sock_path: str, reg: SessionRegistry,
                on_change: Callable[[], None],
                broker=None) -> asyncio.AbstractServer:
    """Start the Unix-socket server. `broker` enables permission requests."""
    if os.path.exists(sock_path):
        os.unlink(sock_path)
    os.makedirs(os.path.dirname(sock_path), exist_ok=True)

    async def handle(reader: asyncio.StreamReader,
                     writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                if not line.strip():
                    continue
                try:
                    event = json.loads(line.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue
                etype = event.get("type")
                if etype == "permission_request" and broker is not None:
                    await _handle_permission(reader, writer, broker, event)
                    # After _handle_permission returns, the connection loop ends:
                    # the hook process closes after receiving the decision.
                    break
                elif etype == "ask_request" and broker is not None:
                    await _handle_ask(reader, writer, broker, event)
                    break
                elif etype == "prompt_cancel" and broker is not None:
                    broker.cancel(event.get("id", ""))
                elif etype == "ask_cancel" and broker is not None:
                    broker.cancel_ask(event.get("id", ""))
                else:
                    apply_event(reg, event)
                    on_change()
        finally:
            writer.close()

    return await asyncio.start_unix_server(handle, path=sock_path)
