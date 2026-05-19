"""Aggregated state across all terminal Claude Code sessions."""

VALID_STATES = ("idle", "running", "waiting")


class SessionRegistry:
    """Tracks one state string per session id and produces a snapshot."""

    def __init__(self) -> None:
        self._sessions: dict[str, str] = {}

    def start(self, session_id: str) -> None:
        self._sessions[session_id] = "idle"

    def end(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def set_state(self, session_id: str, state: str) -> None:
        if state not in VALID_STATES:
            raise ValueError(f"unknown state: {state}")
        # Auto-register: a hook event may arrive for a session whose
        # start() the bridge missed (e.g. bridge started mid-session).
        self._sessions[session_id] = state

    def snapshot(self) -> dict:
        running = sum(1 for s in self._sessions.values() if s == "running")
        waiting = sum(1 for s in self._sessions.values() if s == "waiting")
        total = len(self._sessions)
        if total == 0:
            msg = "idle"
        else:
            msg = f"{running} running · {waiting} waiting"
        return {"running": running, "waiting": waiting,
                "total": total, "msg": msg}
