"""The Watcher's skeleton under attack: deaths, benching, goodbyes, locks."""
from __future__ import annotations

from tracemem.app.daemon import (
    RESTART_BENCH_THRESHOLD, SingleInstanceLock, Supervisor,
)
from tracemem.app.witness import WitnessKind


class FakeOrgan:
    def __init__(self, name):
        self.name = name
        self._alive = False
        self.starts = 0
    def start(self):
        self._alive = True
        self.starts += 1
    def stop(self):
        self._alive = False
    def alive(self):
        return self._alive
    def die(self):
        self._alive = False


def make(now=[1000]):
    sink = []
    sup = Supervisor(sink.append, lambda: now[0])
    return sup, sink, now


def kinds(sink):
    return [c.structured["witness"] for c in sink]


def test_boot_writes_gap_then_start_then_organs_run():
    sup, sink, _ = make()
    o = FakeOrgan("eye")
    sup.register(o)
    sup.boot(last_claim_t_ms=-40_000_000, last_witness_kind=WitnessKind.START)
    assert kinds(sink) == ["kill-detected", "start"]     # the hole FIRST
    assert o.alive() and sup.health() == {"eye": "alive"}


def test_dead_organ_restarts_with_witness():
    sup, sink, _ = make()
    o = FakeOrgan("ear")
    sup.register(o)
    sup.boot(None, None)
    o.die()
    sup.tick()
    assert o.alive() and "organ-restart" in kinds(sink)


def test_repeated_deaths_bench_not_kill():
    sup, sink, now = make()
    o = FakeOrgan("sick")
    ok = FakeOrgan("healthy")
    sup.register(o); sup.register(ok)
    sup.boot(None, None)
    for _ in range(RESTART_BENCH_THRESHOLD + 1):
        o.die(); sup.tick(); now[0] += 1000
    assert sup.health()["sick"] == "benched"
    assert sup.health()["healthy"] == "alive"            # never fatal to the whole
    assert "organ-benched" in kinds(sink)
    o_starts = o.starts
    o.die(); sup.tick()
    assert o.starts == o_starts                          # benched stays benched


def test_clean_stop_says_goodbye():
    sup, sink, _ = make()
    o = FakeOrgan("eye"); sup.register(o)
    sup.boot(None, None)
    sup.stop()
    assert not o.alive() and kinds(sink)[-1] == "clean-stop"


def test_single_instance_lock(tmp_path):
    p = str(tmp_path / "watcher.lock")
    a, b = SingleInstanceLock(p), SingleInstanceLock(p)
    assert a.acquire()
    assert not b.acquire()                               # second instance refused
    a.release()
    assert b.acquire()                                   # reclaimable after release
    b.release()


# ---- an organ that cannot START must not take the daemon with it ---------

def test_an_organ_that_fails_to_start_is_witnessed_not_fatal():
    """WITNESSED IN PRODUCTION 2026-07-29, and it took the founder's capture
    down for minutes.

    The daemon was restarted while his screen was asleep. `SCStreamSession
    .start()` raised "ScreenCaptureKit reports zero displays", the exception
    propagated straight out of `boot()`, and the whole process died — taking
    the ask socket, the brain and the night with it. The store was intact;
    nothing was watching or answering, and nothing anywhere said so.

    This was the supervisor's own contract left unimplemented at the one
    moment it mattered. `tick()` has always caught a dying organ, witnessed
    it, retried it and benched it rather than dying — while `boot()` called
    start() bare. So a transient condition at the wrong second was fatal, and
    the identical condition one beat later was routine.
    """
    sink = []
    sup = Supervisor(sink=sink.append, now_ms=lambda: 1000)

    class _Doomed:
        name = "eye"
        started = 0

        def start(self):
            _Doomed.started += 1
            raise RuntimeError("ScreenCaptureKit reports zero displays")

        def stop(self):
            pass

        def alive(self):
            return False

    sup.register(_Doomed())
    sup.boot(last_claim_t_ms=None, last_witness_kind=None)

    kinds = [c.structured.get("witness") for c in sink if c.structured]
    assert WitnessKind.ORGAN_START_FAILED.value in kinds
    assert sup.health()["eye"] == "dead"


def test_the_daemon_keeps_serving_when_one_organ_cannot_start():
    """The whole point: the socket, the brain and the night outlive an eye
    that could not open. Perception is the top of the hierarchy, but a dead
    perception organ must not silence the parts that still work."""
    sink = []
    sup = Supervisor(sink=sink.append, now_ms=lambda: 1000)

    class _Doomed:
        name = "eye"

        def start(self):
            raise RuntimeError("nope")

        def stop(self):
            pass

        def alive(self):
            return False

    class _Fine:
        name = "ear"

        def __init__(self):
            self._on = False

        def start(self):
            self._on = True

        def stop(self):
            self._on = False

        def alive(self):
            return self._on

    sup.register(_Doomed())
    sup.register(_Fine())
    sup.boot(last_claim_t_ms=None, last_witness_kind=None)

    assert sup.health() == {"eye": "dead", "ear": "alive"}
    sup.tick()          # and the next beat retries the failed one, as ever


def test_an_organ_that_cannot_start_yet_is_never_benched():
    """CAUGHT BEFORE IT EVER BIT, by asking what happens when the founder
    unlocks his Mac.

    A death means the organ RAN and then broke — evidence of sickness, and
    the bench exists so a sick organ cannot restart-loop. A failed start
    means the WORLD was not ready: a sleeping or waking display, a permission
    not yet granted. Nothing is sick and the condition clears on its own.

    Counted the same, the arithmetic was fatal: the loop ticks once a second,
    the bench threshold is 3 within 60s, and NOTHING EVER UN-BENCHES an
    organ. A display taking three seconds to become available to
    ScreenCaptureKit after an unlock would have stopped the eye permanently —
    silently, with the daemon still up and answering. Invisible, permanent,
    and it looks like everything is fine.
    """
    now = [1000]
    sink = []
    sup = Supervisor(sink=sink.append, now_ms=lambda: now[0])

    class _NotYet:
        name = "eye"

        def __init__(self):
            self.ok = False
            self.attempts = 0

        def start(self):
            self.attempts += 1
            if not self.ok:
                raise RuntimeError("ScreenCaptureKit reports zero displays")

        def stop(self):
            self.ok = False

        def alive(self):
            return self.ok and self.attempts > 0

    organ = _NotYet()
    sup.register(organ)
    sup.boot(last_claim_t_ms=None, last_witness_kind=None)

    for _ in range(300):                      # five minutes of one-second beats
        now[0] += 1000
        sup.tick()

    assert sup.health()["eye"] != "benched"
    kinds = [c.structured.get("witness") for c in sink if c.structured]
    assert WitnessKind.ORGAN_BENCHED.value not in kinds


def test_the_eye_comes_back_the_moment_the_display_does():
    """THE ORACLE THAT MATTERS MOST. If this is wrong, the founder's Mac
    stops capturing after one lock and nothing ever says so."""
    now = [1000]
    sink = []
    sup = Supervisor(sink=sink.append, now_ms=lambda: now[0])

    class _NotYet:
        name = "eye"

        def __init__(self):
            self.world_ready = False
            self._on = False

        def start(self):
            if not self.world_ready:
                raise RuntimeError("zero displays")
            self._on = True

        def stop(self):
            self._on = False

        def alive(self):
            return self._on

    organ = _NotYet()
    sup.register(organ)
    sup.boot(last_claim_t_ms=None, last_witness_kind=None)
    for _ in range(120):
        now[0] += 1000
        sup.tick()
    assert not organ.alive()

    organ.world_ready = True                  # the display wakes
    for _ in range(80):                       # within one backoff cap (60s)
        now[0] += 1000
        sup.tick()

    assert organ.alive(), "capture must resume once the world allows it"
    assert sup.health()["eye"] == "alive"


def test_the_retry_storm_does_not_flood_the_store_with_witnesses():
    """A hole in perception must be explainable — but one witness per second
    for an hour is a store full of the same sentence. The first failure is
    always recorded; after that the exponential backoff makes it sparse."""
    now = [1000]
    sink = []
    sup = Supervisor(sink=sink.append, now_ms=lambda: now[0])

    class _Doomed:
        name = "eye"

        def start(self):
            raise RuntimeError("nope")

        def stop(self):
            pass

        def alive(self):
            return False

    sup.register(_Doomed())
    sup.boot(last_claim_t_ms=None, last_witness_kind=None)
    for _ in range(600):                      # ten minutes of beats
        now[0] += 1000
        sup.tick()

    failed = [c for c in sink if c.structured
              and c.structured.get("witness")
              == WitnessKind.ORGAN_START_FAILED.value]
    assert failed, "the first failure is always on the record"
    assert len(failed) == 2, (
        "a stable failed start is recorded on entry and on reaching the "
        "terminal backoff tier, not once per minute: got %d" % len(failed))


def test_a_real_death_after_a_recovery_still_benches():
    """The bench must not be lost. An organ that starts fine and then keeps
    DYING is sick, and that is what the bench was built for."""
    now = [1000]
    sink = []
    sup = Supervisor(sink=sink.append, now_ms=lambda: now[0])

    class _Flaky:
        name = "eye"

        def __init__(self):
            self._on = False

        def start(self):
            self._on = True

        def stop(self):
            self._on = False

        def alive(self):
            was, self._on = self._on, False   # dies immediately every beat
            return was and False

    sup.register(_Flaky())
    sup.boot(last_claim_t_ms=None, last_witness_kind=None)
    for _ in range(10):
        now[0] += 1000
        sup.tick()

    assert sup.health()["eye"] == "benched"


def test_main_can_actually_start_the_brain(tmp_path, monkeypatch):
    """THE TEST THAT WAS MISSING FOR THREE WEEKS.

    Every existing daemon test calls `run()` directly with a hand-built brain.
    Nothing called `main()` down the `--ask` path, so an UnboundLocalError on
    its very first statement (a local `import ClaimLog` shadowing the module
    one, commit 0376163) sat behind 1,400 green tests from 2026-07-30 until
    2026-08-20. The suite could not have caught it, because the suite never
    started the program.

    Bounded by `--seconds`, no eye, no night: this asserts only that the
    entrypoint REACHES a running loop and returns cleanly.
    """
    import os
    import tempfile

    import tracemem.__main__ as m

    # The socket lives OUTSIDE tmp_path: pytest's tmp dirs are ~90 bytes deep
    # and sockaddr_un caps the path at 103, which AskSocket refuses loudly and
    # correctly. That refusal is not what this test is about.
    sock_dir = tempfile.mkdtemp(prefix="tm", dir="/tmp")
    store = tmp_path / "s.sqlite3"
    rc = m.main(["--ask", "--no-night", "--seconds", "0.2",
                 "--store", str(store),
                 "--graph", str(tmp_path / "g.sqlite3"),
                 "--lock", str(tmp_path / "l.lock"),
                 "--socket", os.path.join(sock_dir, "a.sock"),
                 "--plaintext"])
    assert rc == 0
    assert store.exists()
