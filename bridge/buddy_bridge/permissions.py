"""Owns the single device screen: one active prompt/ask + a bounded FIFO queue.

The broker is the sole authority on what is displayed. Concurrent requests from
multiple Claude Code sessions line up FIFO instead of colliding. All active/queue
state changes happen synchronously in the settle path (resolve/cancel/watchdog),
never in the awaiting coroutine's finally."""
import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

MAX_DEPTH = 3          # max queued entries, not counting the active one
BINARY_TIMEOUT = 30.0  # on-screen seconds for a binary permission (must match hook)
ASK_TIMEOUT = 85.0     # on-screen seconds for an ask (must stay < settings.json 90s)
WATCHDOG_MARGIN = 2.0  # backstop fires this much after the hook's own timeout
MAX_RESENDS = 3        # bound on busy-driven resends before failing the active entry


@dataclass
class Entry:
    id: str
    family: str                       # "permission" or "ask"
    fut: asyncio.Future
    send_device: Callable[[], None]   # push this entry's payload to the device (BLE)
    send_cancel: Callable[[], None]   # tell the device to clear this entry
    send_active: Optional[Callable[[], None]] = None  # push "active" to this hook's socket
    resend_count: int = 0
    watchdog: Optional[asyncio.TimerHandle] = None


class PermissionBroker:
    def __init__(
        self,
        send_prompt: Callable[[str, str, str, Optional[str]], None],
        send_cancel: Callable[[str], None],
        send_auto_fired: Optional[Callable[[str], None]] = None,
        send_ask: Optional[Callable[[str, bool, list], None]] = None,
        send_ask_cancel: Optional[Callable[[str], None]] = None,
        link_connected: Callable[[], bool] = lambda: True,
    ) -> None:
        self._send_prompt = send_prompt
        self._send_cancel = send_cancel
        self._send_auto_fired = send_auto_fired
        self._send_ask = send_ask
        self._send_ask_cancel = send_ask_cancel
        self._link_connected = link_connected
        self._active: Optional[Entry] = None
        self._queue: deque[Entry] = deque()
        self._entries: dict[str, Entry] = {}   # id -> Entry (active or queued)
        self._auto_approve = False

    # ----- introspection (used by tests) -----
    @property
    def active_id(self) -> Optional[str]:
        return self._active.id if self._active else None

    @property
    def queue_ids(self) -> list:
        return [e.id for e in self._queue]

    @property
    def auto_approve(self) -> bool:
        return self._auto_approve

    # ----- request entry points -----
    async def request(self, prompt_id: str, tool: str, detail: str,
                      change: Optional[str],
                      send_active: Optional[Callable[[], None]] = None,
                      session: str = "") -> Optional[str]:
        if self._auto_approve:
            if self._send_auto_fired is not None:
                self._send_auto_fired(tool)
            return "allow"
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        entry = Entry(
            id=prompt_id, family="permission", fut=fut,
            send_device=lambda: self._send_prompt(prompt_id, tool, detail, change, session),
            send_cancel=lambda: self._send_cancel(prompt_id),
            send_active=send_active,
        )
        self._admit(entry)
        try:
            return await fut
        finally:
            # Backstop only; the settle path already removed structural state.
            self._entries.pop(prompt_id, None)

    async def ask(self, ask_id: str, multi_select: bool, questions: list,
                  send_active: Optional[Callable[[], None]] = None,
                  session: str = "") -> Optional[list]:
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        entry = Entry(
            id=ask_id, family="ask", fut=fut,
            send_device=lambda: (self._send_ask and
                                 self._send_ask(ask_id, multi_select, questions, session)),
            send_cancel=lambda: (self._send_ask_cancel and
                                 self._send_ask_cancel(ask_id)),
            send_active=send_active,
        )
        self._admit(entry)
        try:
            return await fut
        finally:
            self._entries.pop(ask_id, None)

    # ----- response/settle entry points -----
    def resolve(self, prompt_id: str, decision: str) -> None:
        e = self._entries.get(prompt_id)
        if e is not None and e.family == "permission":
            self._settle(e, decision, send_cancel=False)

    def cancel(self, prompt_id: str) -> None:
        e = self._entries.get(prompt_id)
        if e is not None and e.family == "permission":
            self._settle(e, None, send_cancel=True)

    def resolve_ask(self, ask_id: str, answers: list) -> None:
        e = self._entries.get(ask_id)
        if e is not None and e.family == "ask":
            self._settle(e, answers, send_cancel=False)

    def cancel_ask(self, ask_id: str) -> None:
        e = self._entries.get(ask_id)
        if e is not None and e.family == "ask":
            self._settle(e, None, send_cancel=True)

    def set_auto_approve(self, state: bool) -> None:
        self._auto_approve = state
        if state:
            self._drain_for_auto()

    def _drain_for_auto(self) -> None:
        """AUTO turned on: resolve all pending PERMISSION entries as allow.
        Asks are not auto-answerable and are left untouched (an active ask stays
        on screen; its queued successors that are permissions still drain).

        Queued entries are drained before the active one so that settling the
        active entry never promotes a doomed entry mid-drain (which would cause
        a spurious device send+cancel / on-screen flash)."""
        # Active last: a False sort key (queued) sorts before True (active).
        entries = sorted(self._entries.values(), key=lambda e: self._active is e)
        for e in entries:
            if e.family == "permission":
                self._settle(e, "allow", send_cancel=(self._active is e))

    def on_busy(self, prompt_id: str) -> None:
        """Device reported it is busy for this id. Only meaningful for the
        active entry (a queued entry was never sent). Bounded resend; after the
        bound, fail the active entry to terminal rather than looping forever."""
        e = self._entries.get(prompt_id)
        if e is None or self._active is not e:
            return
        e.resend_count += 1
        if e.resend_count > MAX_RESENDS:
            self._settle(e, None, send_cancel=False)
        elif self._link_connected():
            e.send_device()

    def resend_active(self) -> None:
        """Re-send the on-screen entry to the device (e.g. after BLE reconnect)."""
        if self._active is not None and self._link_connected():
            self._active.send_device()

    # ----- internal: synchronous queue mechanics -----
    def _admit(self, entry: Entry) -> None:
        self._entries[entry.id] = entry
        if self._active is None:
            self._queue.append(entry)
            self._advance()
        elif len(self._queue) < MAX_DEPTH:
            self._queue.append(entry)
        else:                          # queue full -> busy-reject to terminal
            self._entries.pop(entry.id, None)
            if not entry.fut.done():
                entry.fut.set_result(None)

    def _advance(self) -> None:
        """Promote the next queued entry into the (empty) active slot."""
        while self._queue:
            nxt = self._queue.popleft()
            if nxt.fut.done():
                self._entries.pop(nxt.id, None)
                continue
            self._active = nxt
            nxt.resend_count = 0
            if not self._link_connected():     # BLE down: don't hold a blank screen
                self._active = None
                self._entries.pop(nxt.id, None)
                if not nxt.fut.done():
                    nxt.fut.set_result(None)
                continue
            nxt.send_device()
            if nxt.send_active is not None:
                nxt.send_active()
            self._arm_watchdog(nxt)
            return
        self._active = None

    def _settle(self, entry: Entry, result, send_cancel: bool) -> None:
        if entry.fut.done():
            return
        was_active = self._active is entry
        if send_cancel and was_active:
            entry.send_cancel()
        self._cancel_watchdog(entry)
        self._entries.pop(entry.id, None)
        if was_active:
            self._active = None
        else:
            try:
                self._queue.remove(entry)
            except ValueError:
                pass
        entry.fut.set_result(result)
        if was_active:
            self._advance()

    def _arm_watchdog(self, entry: Entry) -> None:
        loop = asyncio.get_running_loop()
        timeout = ASK_TIMEOUT if entry.family == "ask" else BINARY_TIMEOUT
        entry.watchdog = loop.call_later(
            timeout + WATCHDOG_MARGIN, self._on_watchdog, entry)

    def _cancel_watchdog(self, entry: Entry) -> None:
        if entry.watchdog is not None:
            entry.watchdog.cancel()
            entry.watchdog = None

    def _on_watchdog(self, entry: Entry) -> None:
        # Identity-bound: only fire if this exact entry is still active.
        if self._active is entry and not entry.fut.done():
            self._settle(entry, None, send_cancel=True)
