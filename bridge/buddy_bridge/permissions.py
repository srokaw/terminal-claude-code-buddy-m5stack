"""Tracks pending permission requests and matches device decisions to them."""
import asyncio
from typing import Callable


class PermissionBroker:
    """One pending request per prompt id. request() awaits a decision."""

    def __init__(self,
                 send_prompt: Callable[[str, str, str, str | None], None],
                 send_cancel: Callable[[str], None],
                 send_auto_fired=None) -> None:
        self._send_prompt = send_prompt
        self._send_cancel = send_cancel
        self._send_auto_fired = send_auto_fired
        self._pending: dict[str, asyncio.Future] = {}
        self._auto_approve = False

    @property
    def auto_approve(self) -> bool:
        return self._auto_approve

    def set_auto_approve(self, state: bool) -> None:
        self._auto_approve = state

    async def request(self, prompt_id: str, tool: str, detail: str,
                      change: str | None) -> str:
        """Return 'allow' or 'deny'. Auto-approve short-circuits to 'allow'."""
        if self._auto_approve:
            if self._send_auto_fired is not None:
                self._send_auto_fired(tool)
            return "allow"
        loop = asyncio.get_running_loop()
        # If there is already a live future for this id, resolve it to "deny"
        # so the old caller isn't orphaned.
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
        """A device decision arrived for prompt_id."""
        fut = self._pending.get(prompt_id)
        if fut is not None and not fut.done():
            fut.set_result(decision)

    def cancel(self, prompt_id: str) -> None:
        """Abandon a pending request. The hook receives no decision (None) and
        outputs no JSON, so Claude Code's native prompt handles it."""
        fut = self._pending.get(prompt_id)
        if fut is not None and not fut.done():
            self._send_cancel(prompt_id)
            fut.set_result(None)
