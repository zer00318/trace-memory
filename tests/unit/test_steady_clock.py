"""Time moves forward or it does not move.

The daemon stamped every claim with `int(time.time() * 1000)` — the wall
clock, raw. An NTP correction, a manual time change, or a laptop waking with
a bad RTC steps that clock, sometimes backwards by seconds, and every claim
written afterwards carries a timestamp EARLIER than claims already stored.

Nothing notices, and three things break at once: the timeline reorders so an
afternoon appears before its morning; episode boundaries are cut at the wrong
instants, since the binder segments on time; and range queries silently omit
claims that should have fallen inside them.

None of it raises. It looks like a memory that is simply wrong about a day.
"""
from __future__ import annotations

from tracemem.capture.steady_clock import JUMP_MS, SteadyClock


class Fake:
    """A wall clock and a monotonic clock that move independently — which is
    the entire point, since only one of them can be corrected."""

    def __init__(self, wall=1_700_000_000.0):
        self.wall = wall
        self.mono = 1000.0

    def tick(self, seconds):
        """Real time passing: both advance."""
        self.wall += seconds
        self.mono += seconds

    def step_wall(self, seconds):
        """A clock CORRECTION: only the wall clock moves."""
        self.wall += seconds


def _clock(f):
    return SteadyClock(wall=lambda: f.wall, mono=lambda: f.mono)


def test_ordinary_time_passes_normally():
    f = Fake()
    c = _clock(f)
    a = c.now_ms()
    f.tick(5)
    assert c.now_ms() - a == 5_000


def test_a_backward_correction_never_reorders_claims():
    """THE BUG. Ten seconds of NTP correction backwards used to mean ten
    seconds of a person's day filed before the morning that preceded it."""
    f = Fake()
    c = _clock(f)
    before = c.now_ms()
    f.step_wall(-10)
    after = c.now_ms()
    assert after >= before, "a claim was stamped earlier than the one before"


def test_time_still_advances_after_a_backward_correction():
    """It must not freeze either — a clock stuck at one value would collapse
    a whole afternoon into a single instant."""
    f = Fake()
    c = _clock(f)
    c.now_ms()
    f.step_wall(-10)
    stalled = c.now_ms()
    f.tick(30)
    assert c.now_ms() > stalled


def test_a_forward_correction_is_followed_not_ignored():
    """The wall clock is what every other program on this machine believes.
    A memory that insisted on its own timeline would disagree with every file
    mtime and calendar entry it ever reads."""
    f = Fake()
    c = _clock(f)
    c.now_ms()
    f.step_wall(3600)
    assert c.now_ms() >= int((f.wall - 1) * 1000)


def test_both_directions_are_reported():
    """A forward step is also a jump. Waking from sleep looks like one and is
    harmless; a clock corrected forwards by an hour is not. Guessing which
    happened is how the harmful one gets missed."""
    f = Fake()
    c = _clock(f)
    c.now_ms()
    f.step_wall(-30)
    c.now_ms()
    f.step_wall(7200)
    c.now_ms()
    jumps = c.drain_jumps()
    assert len(jumps) == 2
    assert jumps[0][1] < 0 and jumps[1][1] > 0


def test_ordinary_drift_is_not_called_a_jump():
    """Scheduler delays and coarse timers move these apart by tens of
    milliseconds. Crying jump at that makes the witness worthless."""
    f = Fake()
    c = _clock(f)
    c.now_ms()
    f.step_wall(JUMP_MS / 1000.0 / 4)
    c.now_ms()
    assert c.drain_jumps() == []


def test_a_drained_jump_is_not_witnessed_twice():
    f = Fake()
    c = _clock(f)
    c.now_ms()
    f.step_wall(-30)
    c.now_ms()
    assert len(c.drain_jumps()) == 1
    assert c.drain_jumps() == []


def test_the_jump_list_cannot_grow_without_bound():
    """A machine with a broken RTC could step every second for a week, and a
    list nobody drains is a leak in a daemon meant to run for years."""
    f = Fake()
    c = _clock(f)
    for _ in range(400):
        f.step_wall(-5)
        f.tick(1)
        c.now_ms()
    assert len(c.drain_jumps()) <= 256
    assert c.jumps_seen >= 400, "it stopped COUNTING as well as storing"


def test_it_is_callable_as_a_now_ms_function():
    """Every caller in the daemon passes `now_ms` around as a plain
    callable; this has to drop into that slot without a wrapper."""
    f = Fake()
    c = _clock(f)
    assert isinstance(c(), int)
    assert c() >= c() - 1


# -- the daemon writes it down --------------------------------------------

def test_the_daemon_can_actually_build_the_witness():
    """THE BRANCH NO TEST WOULD OTHERWISE REACH.

    The witness for a clock jump lives inside the supervision loop and only
    runs when a jump has actually happened, so the whole suite can pass while
    that line raises NameError. It did: `__main__` imported WitnessKind and
    not `witness`, and the first real NTP correction would have taken the
    daemon down instead of recording anything.

    This builds the same witness the loop builds, from the same imports.
    """
    from tracemem.__main__ import witness as w
    from tracemem.app.witness import WitnessKind

    claim = w(WitnessKind.CLOCK_JUMP, 1_700_000_000_000, device_id="mac-1",
              detail="wall clock stepped -10.0s",
              structured_extra={"delta_ms": -10_000})
    assert claim.structured["delta_ms"] == -10_000
    assert "stepped" in claim.text or "stepped" in str(claim.structured)


def test_the_jump_witness_kind_has_always_existed():
    """It was in the vocabulary since July and nothing ever produced one,
    because nothing was watching the clock."""
    from tracemem.app.witness import WitnessKind

    assert WitnessKind.CLOCK_JUMP.value == "clock-jump"


def test_the_daemon_stamps_with_a_steady_clock():
    import inspect

    from tracemem import __main__ as m

    src = inspect.getsource(m)
    assert "clock = SteadyClock()" in src
    assert "now_ms = clock.now_ms" in src
    assert "now_ms = lambda: int(time.time() * 1000)" not in src


def test_real_daemon_loop_witnesses_a_backward_step_and_keeps_append_order(tmp_path):
    """Black-box the production seam, not the witness constructor.

    The organ changes the wall after START has been appended. The next real
    supervision beat must detect it through the same injected clock, append a
    CLOCK_JUMP for the requested device, and shut down normally. This catches
    both historical reachability defects: draining a bound method (which has
    no drain_jumps) and reading the CLI-only ``args`` name inside run().
    """
    from tracemem.__main__ import run
    from tracemem.app.witness import WitnessKind
    from tracemem.text.claim_log import ClaimLog

    fake = Fake()
    clock = _clock(fake)

    class StepOnStart:
        name = "clock-stepper"

        def __init__(self):
            self.running = False

        def start(self):
            self.running = True
            fake.step_wall(-30)

        def stop(self):
            self.running = False

        def alive(self):
            return self.running

    path = tmp_path / "claims.sqlite3"
    report = run(str(path), seconds=0.05, tick_seconds=0,
                 lock_path=str(tmp_path / "daemon.lock"),
                 organs=[StepOnStart()], crypto=None, clock=clock,
                 device_id="mac-test")

    rows = ClaimLog(str(path), crypto=None).all_claims()
    kinds = [c.structured.get("witness") for c in rows]
    jump = next(c for c in rows
                if c.structured.get("witness") == WitnessKind.CLOCK_JUMP.value)
    assert report["ticks"] > 0
    assert jump.coord.capture.device_id == "mac-test"
    assert jump.structured["delta_ms"] < 0
    assert kinds.index(WitnessKind.START.value) < kinds.index(
        WitnessKind.CLOCK_JUMP.value) < kinds.index(WitnessKind.CLEAN_STOP.value)
    assert [c.coord.t_ms for c in rows] == sorted(c.coord.t_ms for c in rows)


def test_eye_stream_uses_the_same_injected_civil_clock():
    """The frame adapter used to default back to raw time.time()."""
    from tracemem.app.eye_organ import EyeOrgan

    made = []

    class Session:
        def __init__(self, **kw):
            made.append(kw)

        def start(self):
            pass

        def stop(self):
            pass

    eye = EyeOrgan(lambda _claim: True, session_factory=Session,
                   clock=lambda: 1_234_567)
    eye.start()
    assert made[0]["clock"]() == 1_234.567
