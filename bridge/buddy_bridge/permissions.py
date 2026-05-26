"""Tracks pending permission requests and AskUserQuestion prompts and matches
device responses to them."""
import asyncio
from typing import Callable, Optional


class PermissionBroker:
    """One pending future per id, per family (permission, ask)."""

    def __init__(
        self,
        send_prompt: Callable[[str, str, str, Optional[str]], None],
        send_cancel: Callable[[str], None],
        send_auto_fired: Optional[Callable[[str], None]] = None,
        send_ask: Optional[Callable[[str, bool, list], None]] = None,
        send_ask_cancel: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._send_prompt = send_prompt
        self._send_cancel = send_cancel
        self._send_auto_fired = send_auto_fired
        self._send_ask = send_ask
        self._send_ask_cancel = send_ask_cancel
        self._pending: dict[str, asyncio.Future] = {}
        self._pending_ask: dict[str, asyncio.Future] = {}
        self._auto_approve = False

    @property
    def auto_approve(self) -> bool:
        return self._auto_approve

    def set_auto_approve(self, state: bool) -> None:
        self._auto_approve = state

    # ----- permission family (unchanged behavior) -----

    async def request(self, prompt_id: str, tool: str, detail: str,
                      change: Optional[str]) -> Optional[str]:
        if self._auto_approve:
            if self._send_auto_fired is not None:
                self._send_auto_fired(tool)
            return "allow"
        loop = asyncio.get_running_loop()
        existing = self._pending.get(prompt_id)
        if existing is not None and not existing.done():
            existing.set_result("deny")
        fut: asyncio.Future = loop.create_future()
        self._pending[prompt_id] = fut
        self._send_prompt(prompt_id, tool, detail, change)
        try:
            return await fut
        finally:
            if self._pending.get(prompt_id) is fut:
                self._pending.pop(prompt_id, None)

    def resolve(self, prompt_id: str, decision: str) -> None:
        fut = self._pending.get(prompt_id)
        if fut is not None and not fut.done():
            fut.set_result(decision)

    def cancel(self, prompt_id: str) -> None:
        fut = self._pending.get(prompt_id)
        if fut is not None and not fut.done():
            self._send_cancel(prompt_id)
            fut.set_result(None)

    # ----- ask family (new) -----

    async def ask(self, ask_id: str, multi_select: bool,
                  questions: list) -> Optional[list]:
        """Return the list of per-question answers, or None on cancel/timeout."""
        loop = asyncio.get_running_loop()
        existing = self._pending_ask.get(ask_id)
        if existing is not None and not existing.done():
            existing.set_result(None)
        fut: asyncio.Future = loop.create_future()
        self._pending_ask[ask_id] = fut
        if self._send_ask is not None:
            self._send_ask(ask_id, multi_select, questions)
        try:
            return await fut
        finally:
            if self._pending_ask.get(ask_id) is fut:
                self._pending_ask.pop(ask_id, None)

    def resolve_ask(self, ask_id: str, answers: list) -> None:
        fut = self._pending_ask.get(ask_id)
        if fut is not None and not fut.done():
            fut.set_result(answers)

    def cancel_ask(self, ask_id: str) -> None:
        fut = self._pending_ask.get(ask_id)
        if fut is not None and not fut.done():
            if self._send_ask_cancel is not None:
                self._send_ask_cancel(ask_id)
            fut.set_result(None)
