#!/usr/bin/env python3
"""Manual test driver for the multi-session prompt queue.

Acts as N concurrent "hooks": each opens its own Unix-socket connection to a
*running* bridge and sends a permission_request / ask_request, exactly like
hooks/buddy-permission-hook.py does. The real bridge then talks to the real
M5Stack over BLE, so this lets you exercise the FIFO queue end-to-end with
controllable concurrency and timing while you watch the device and press its
buttons.

It does NOT need real Claude Code sessions — it speaks the bridge's socket
protocol directly. Run the bridge first (python -m buddy_bridge), then run a
scenario here and correlate the timestamped events with the device screen.

Examples (run from the bridge/ directory):
    python tools/prompt_queue_driver.py fifo --count 3
    python tools/prompt_queue_driver.py queuefull          # 5 at once -> 5th falls back
    python tools/prompt_queue_driver.py ask
    python tools/prompt_queue_driver.py mixed              # a perm + a queued ask
    python tools/prompt_queue_driver.py cancel             # queued entry self-cancels
    python tools/prompt_queue_driver.py hold --count 2     # keep open for reconnect test

Each line printed is one event, tagged with the session label and seconds since
the driver started, so you can line it up against what the device shows:
    [ +0.00s][A] SENT permission_request tool=Bash detail='ls -la'
    [ +0.01s][A] DEVICE-SHOWING (active)        <- bridge promoted A to the screen
    [ +4.30s][A] DECISION: allow                <- you pressed a button
    [ +0.00s][E] FALLBACK (decision=null)       <- queue full, would defer to terminal
"""
import argparse
import json
import os
import socket
import sys
import threading
import time
import uuid

DEFAULT_SOCK = os.path.expanduser("~/.claude-buddy/bridge.sock")
_START = time.monotonic()
_print_lock = threading.Lock()


def log(label: str, msg: str) -> None:
    with _print_lock:
        print(f"[{time.monotonic() - _START:+6.2f}s][{label}] {msg}", flush=True)


def _send(sock: socket.socket, obj: dict) -> None:
    sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))


def run_session(sock_path: str, label: str, kind: str, payload: dict,
                read_timeout: float, cancel_after: float | None = None) -> None:
    """One fake hook: connect, send the request, then read messages until a
    terminal decision/answers, the socket closes, or read_timeout elapses.

    If cancel_after is set, send a prompt_cancel/ask_cancel after that many
    seconds (simulating the keyboard winning / the queued hook giving up)."""
    pid = payload["id"]
    cancel_type = "ask_cancel" if kind == "ask_request" else "prompt_cancel"
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(sock_path)
    except OSError as e:
        log(label, f"CONNECT-FAILED ({e}) — is the bridge running at {sock_path}?")
        return

    tool = payload.get("tool", payload.get("multiSelect", ""))
    detail = payload.get("detail", f"{len(payload.get('questions', []))} question(s)")
    _send(sock, payload)
    log(label, f"SENT {kind} id={pid[:8]} {('tool=' + str(tool)) if kind=='permission_request' else 'ask'} detail={detail!r}")

    deadline = time.monotonic() + read_timeout
    cancel_deadline = (time.monotonic() + cancel_after) if cancel_after else None
    buf = b""
    sock.settimeout(0.25)
    try:
        while True:
            now = time.monotonic()
            if now > deadline:
                log(label, "READ-TIMEOUT (driver gave up waiting)")
                return
            if cancel_deadline is not None and now >= cancel_deadline:
                _send(sock, {"type": cancel_type, "id": pid})
                log(label, f"SENT {cancel_type} (self-cancel)")
                cancel_deadline = None  # only once
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line.decode("utf-8"))
                except ValueError:
                    continue
                if msg.get("type") == "active":
                    log(label, "DEVICE-SHOWING (active)  <- promoted to the screen")
                    continue
                if "decision" in msg:
                    d = msg["decision"]
                    if d is None:
                        log(label, "FALLBACK (decision=null)  <- queue-full/cancel/timeout; real hook defers to terminal")
                    else:
                        log(label, f"DECISION: {d}")
                    return
                if "answers" in msg:
                    a = msg["answers"]
                    if a is None:
                        log(label, "FALLBACK (answers=null)")
                    else:
                        log(label, f"ANSWERS: {json.dumps(a)}")
                    return
                log(label, f"OTHER {msg}")
            try:
                chunk = sock.recv(1024)
            except socket.timeout:
                continue
            if not chunk:
                log(label, "CLOSED (bridge closed the connection)")
                return
            buf += chunk
    finally:
        try:
            sock.close()
        except OSError:
            pass


def perm_payload(label: str, tool: str = "Bash", detail: str | None = None,
                 session: str | None = None) -> dict:
    return {"type": "permission_request", "id": str(uuid.uuid4()),
            "session": session or f"sess-{label}", "tool": tool,
            "detail": detail or f"echo from-{label}", "change": None}


def ask_payload(label: str, session: str | None = None, multi: bool = False) -> dict:
    return {"type": "ask_request", "id": str(uuid.uuid4()),
            "session": session or f"sess-{label}", "multiSelect": multi,
            "questions": [
                {"text": f"Pick an option ({label})",
                 "options": [{"label": "Yes", "desc": "do it"},
                             {"label": "No", "desc": "skip"}]}]}


def _labels(n: int):
    # A, B, C, ... then A1, B1, ... if needed.
    for i in range(n):
        yield chr(ord("A") + i) if i < 26 else f"S{i}"


def launch(sessions, stagger: float, read_timeout: float) -> None:
    """sessions: list of (label, kind, payload, cancel_after). Launch each in a
    thread, staggered by `stagger` seconds, and wait for all to finish."""
    threads = []
    for label, kind, payload, cancel_after in sessions:
        t = threading.Thread(
            target=run_session,
            args=(ARGS.sock, label, kind, payload, read_timeout, cancel_after),
            daemon=True)
        t.start()
        threads.append(t)
        if stagger:
            time.sleep(stagger)
    for t in threads:
        t.join()
    log("*", "all sessions finished")


def scenario_fifo(count: int, stagger: float) -> None:
    log("*", f"FIFO: launching {count} permission prompts (stagger={stagger}s). "
            f"Expect the device to show them one at a time in arrival order; "
            f"approve/deny each on the device to reveal the next.")
    sessions = [(lbl, "permission_request", perm_payload(lbl), None)
                for lbl in _labels(count)]
    launch(sessions, stagger, read_timeout=ARGS.read_timeout)


def scenario_queuefull(stagger: float) -> None:
    log("*", "QUEUE-FULL: launching 5 prompts at once. Expect 1 active + 3 queued "
            "(MAX_DEPTH=3); the 5th should immediately return FALLBACK (null) — "
            "the real hook would defer it to the terminal.")
    sessions = [(lbl, "permission_request", perm_payload(lbl), None)
                for lbl in _labels(5)]
    launch(sessions, stagger, read_timeout=ARGS.read_timeout)


def scenario_ask() -> None:
    log("*", "ASK: one AskUserQuestion. Verify the device renders the question + "
            "options and that pressing a button returns the matching answer.")
    sessions = [("A", "ask_request", ask_payload("A"), None)]
    launch(sessions, 0.0, read_timeout=ARGS.read_timeout)


def scenario_asks(count: int, stagger: float) -> None:
    log("*", f"ASKS: {count} concurrent AskUserQuestion prompts (the reported case: "
            f"two sessions both asking). On the OLD bridge, expect the device to "
            f"show the FIRST ask and the rest to report FALLBACK (busy-rejected). "
            f"Press A on the device to answer the shown ask and watch: does the "
            f"green 'Sent' splash clear promptly (~1s) or linger? Does anything else "
            f"appear? Correlate with the firmware serial log.")
    sessions = [(lbl, "ask_request", ask_payload(lbl), None)
                for lbl in _labels(count)]
    launch(sessions, stagger, read_timeout=ARGS.read_timeout)


def scenario_mixed() -> None:
    log("*", "MIXED: a permission prompt, then an ask queued behind it. Resolve "
            "the permission on the device; the ask should then appear.")
    sessions = [
        ("A", "permission_request", perm_payload("A"), None),
        ("B", "ask_request", ask_payload("B"), None),
    ]
    launch(sessions, 0.3, read_timeout=ARGS.read_timeout)


def scenario_cancel() -> None:
    log("*", "CANCEL: two prompts; the queued one (B) self-cancels after 4s. "
            "Expect the device to keep showing A (B was never on screen) and B to "
            "report FALLBACK. Confirms a dead queued entry is pruned, not stuck.")
    sessions = [
        ("A", "permission_request", perm_payload("A"), None),
        ("B", "permission_request", perm_payload("B"), 4.0),
    ]
    launch(sessions, 0.3, read_timeout=ARGS.read_timeout)


def scenario_hold(count: int) -> None:
    log("*", f"HOLD: {count} prompts kept open for up to {ARGS.read_timeout}s. Use "
            f"this to test reconnect: once they're on/queued, toggle the device's "
            f"BLE (power-cycle or move out of range), then bring it back — the "
            f"active prompt should reappear (resend) with no stuck line. Approve "
            f"on the device to release each.")
    sessions = [(lbl, "permission_request", perm_payload(lbl), None)
                for lbl in _labels(count)]
    launch(sessions, 0.3, read_timeout=ARGS.read_timeout)


def main() -> None:
    global ARGS
    p = argparse.ArgumentParser(
        description="Drive the bridge's prompt queue as N concurrent fake hooks.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("scenario",
                   choices=["fifo", "queuefull", "ask", "asks", "mixed", "cancel", "hold"],
                   help="which test scenario to run")
    p.add_argument("--count", type=int, default=3,
                   help="number of prompts (fifo/hold; default 3)")
    p.add_argument("--stagger", type=float, default=0.2,
                   help="seconds between launches; 0 = simultaneous (default 0.2)")
    p.add_argument("--read-timeout", type=float, default=200.0,
                   help="seconds each session waits for a decision (default 200, "
                        "just above the 180s hook budget)")
    p.add_argument("--sock", default=DEFAULT_SOCK,
                   help=f"bridge socket path (default {DEFAULT_SOCK})")
    ARGS = p.parse_args()

    if not os.path.exists(ARGS.sock):
        print(f"error: no bridge socket at {ARGS.sock} — start the bridge first "
              f"(python -m buddy_bridge).", file=sys.stderr)
        sys.exit(1)

    log("*", f"scenario={ARGS.scenario} sock={ARGS.sock}")
    if ARGS.scenario == "fifo":
        scenario_fifo(ARGS.count, ARGS.stagger)
    elif ARGS.scenario == "queuefull":
        scenario_queuefull(ARGS.stagger)
    elif ARGS.scenario == "ask":
        scenario_ask()
    elif ARGS.scenario == "asks":
        scenario_asks(ARGS.count, ARGS.stagger)
    elif ARGS.scenario == "mixed":
        scenario_mixed()
    elif ARGS.scenario == "cancel":
        scenario_cancel()
    elif ARGS.scenario == "hold":
        scenario_hold(ARGS.count)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
