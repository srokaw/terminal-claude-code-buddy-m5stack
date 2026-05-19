from buddy_bridge.state import SessionRegistry


def test_empty_registry_is_idle():
    reg = SessionRegistry()
    assert reg.snapshot() == {"running": 0, "waiting": 0, "total": 0, "msg": "idle"}


def test_start_adds_idle_session():
    reg = SessionRegistry()
    reg.start("s1")
    snap = reg.snapshot()
    assert snap["total"] == 1
    assert snap["running"] == 0


def test_state_counts_running_and_waiting():
    reg = SessionRegistry()
    reg.start("s1")
    reg.start("s2")
    reg.start("s3")
    reg.set_state("s1", "running")
    reg.set_state("s2", "waiting")
    snap = reg.snapshot()
    assert snap == {"running": 1, "waiting": 1, "total": 3,
                    "msg": "1 running · 1 waiting"}


def test_set_state_auto_registers_unknown_session():
    reg = SessionRegistry()
    reg.set_state("late", "running")  # no prior start()
    assert reg.snapshot()["total"] == 1
    assert reg.snapshot()["running"] == 1


def test_end_removes_session():
    reg = SessionRegistry()
    reg.start("s1")
    reg.end("s1")
    assert reg.snapshot()["total"] == 0


def test_end_unknown_session_is_safe():
    reg = SessionRegistry()
    reg.end("never-existed")  # must not raise
    assert reg.snapshot()["total"] == 0
