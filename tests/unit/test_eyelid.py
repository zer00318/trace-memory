"""THE EYELID (F-EYELID) — the eye closes so the brain can think.

WHAT WAS BROKEN. `run_night()` had never executed once on the founder's real
machine. Not "was unproven" — had literally never been called in production.
The daemon reported it itself:

    night: {slices: 0,
            skipped_because: "capture is live or a turn is held —
                              the night never shares the GPU (O3)"}

One GPU; capture and thinking cannot both hold it. The guard is correct. What
was missing was anything that ever CLOSED the eye, so the whole node half of
the napkin ran zero times while looking merely "unproven" from outside.

THE THREE THINGS THESE TESTS HOLD, each of which was a real way to get this
wrong:

  1. **Parked is not dead.** The supervisor revives anything that is not
     alive. Without a park flag it would revive the eye one beat after the
     eyelid closed it, the night would still get nothing, and the restart
     witnesses would pile up as evidence of an organ that keeps dying.
  2. **The blind interval is witnessed.** The real store holds ZERO
     CLOSED_EYES and ZERO AWAY witnesses, which is why coverage would report
     ~100% watched for days the Mac was asleep. A gap means either "nothing
     happened" or "I could not see", and those are opposite answers. An
     eyelid that closes silently turns an honest hole into a lie.
  3. **A failed sensor read never closes the eye.** Unknown is a hole, not an
     absence. Refusing to think is visible in the digest; going blind on a
     bad read is not.
"""
from tracemem.app.daemon import Supervisor
from tracemem.app.eyelid import EYELID_IDLE_S, Eyelid
from tracemem.app.witness import WitnessKind


class _Organ:
    name = "eye"

    def __init__(self):
        self._alive = False
        self.starts = 0
        self.stops = 0

    def start(self):
        self._alive = True
        self.starts += 1

    def stop(self):
        self._alive = False
        self.stops += 1

    def alive(self):
        return self._alive


class _Clock:
    def __init__(self, t=1_000_000):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, ms):
        self.t += ms
        return self.t


def _rig(idle=0.0, locked=False):
    clock = _Clock()
    sink = []
    sup = Supervisor(sink=sink.append, now_ms=clock)
    organ = _Organ()
    sup.register(organ)
    sup.boot(last_claim_t_ms=None, last_witness_kind=None)
    world = {"idle": idle, "locked": locked}
    lid = Eyelid(sup, sink.append, clock,
                 read_idle=lambda: world["idle"],
                 read_locked=lambda: world["locked"])
    return clock, sink, sup, organ, lid, world


def _kinds(sink):
    return [c.structured.get("witness") for c in sink if c.structured]


# ---- 1. parked is not dead ------------------------------------------------

def test_the_supervisor_does_not_revive_an_eye_that_was_parked_on_purpose():
    """THE ORACLE. This exact failure would have made the eyelid look like it
    simply did not work, while quietly logging the eye as unhealthy."""
    clock, sink, sup, organ, lid, world = _rig(locked=True)
    clock.advance(10_000)

    lid.tick()
    assert organ.alive() is False

    for _ in range(20):                       # twenty supervision beats
        sup.tick()

    assert organ.alive() is False, "a parked organ must not be revived"
    assert organ.starts == 1, "boot only — no restart witnessed"
    assert WitnessKind.ORGAN_RESTART.value not in _kinds(sink)


def test_a_parked_eye_frees_the_gpu_because_health_stops_saying_alive():
    """The night's gate reads `any(state == "alive")`. Parking has to change
    that answer with no change to the night's own code — that is the whole
    reason 'parked' is a distinct health state rather than a private flag."""
    clock, sink, sup, organ, lid, world = _rig(locked=True)
    clock.advance(10_000)

    assert sup.health()["eye"] == "alive"
    lid.tick()

    assert sup.health()["eye"] == "parked"
    assert not any(s == "alive" for s in sup.health().values())


def test_unparking_lets_the_ordinary_restart_path_re_arm_the_eye():
    """Re-arm is not reimplemented — `unpark` clears the flag and the NEXT
    tick revives the organ through the witnessed path that already existed.
    `eyelids.py` recorded that its on_reopen hook had nothing behind it; this
    is what is now behind it."""
    clock, sink, sup, organ, lid, world = _rig(locked=True)
    clock.advance(10_000)
    lid.tick()
    assert organ.alive() is False

    world["locked"] = False
    clock.advance(10_000)
    lid.tick()
    sup.tick()

    assert organ.alive() is True
    assert WitnessKind.ORGAN_RESTART.value in _kinds(sink)


def test_a_night_of_parking_never_benches_the_eye():
    """The bench threshold exists to stop a genuinely sick organ from
    restart-looping. If park/unpark counted toward it, a few nights of
    thinking would bench the eye and capture would stop for good — the
    silent, permanent version of the bug this feature fixes."""
    clock, sink, sup, organ, lid, world = _rig()

    for _ in range(10):
        world["locked"] = True
        clock.advance(10_000)
        lid.tick()
        sup.tick()
        world["locked"] = False
        clock.advance(10_000)
        lid.tick()
        sup.tick()

    assert sup.health()["eye"] == "alive"
    assert WitnessKind.ORGAN_BENCHED.value not in _kinds(sink)


# ---- 2. the blind interval is witnessed ----------------------------------

def test_a_closed_interval_is_written_down_with_both_ends():
    clock, sink, sup, organ, lid, world = _rig(locked=True)
    clock.advance(10_000)
    t_close = clock.t
    lid.tick()

    world["locked"] = False
    t_open = clock.advance(90_000)
    lid.tick()

    closed = [c for c in sink if c.structured
              and c.structured.get("witness") == WitnessKind.CLOSED_EYES.value]
    assert len(closed) == 1
    w = closed[0]
    assert w.coord.t_ms == t_close
    assert w.structured["t2_ms"] == t_open
    assert w.structured["causes"] == ["locked"]


def test_idle_alone_is_AWAY_and_a_locked_machine_is_CLOSED_EYES():
    """Different facts, different kinds. 'The screen was lit and nobody was
    home' and 'the machine was shut' are not the same absence, and a
    distinction that lives only in free text is one no query can make."""
    clock, sink, sup, organ, lid, world = _rig(idle=EYELID_IDLE_S + 1)
    clock.advance(10_000)
    lid.tick()
    world["idle"] = 0.0
    clock.advance(60_000)
    lid.tick()

    assert WitnessKind.AWAY.value in _kinds(sink)
    assert WitnessKind.CLOSED_EYES.value not in _kinds(sink)


def test_locking_while_already_idle_is_one_interval_not_two():
    """The nesting lesson from capture/eyelids.py: a boolean double-counts
    every night, forever, in a way that looks entirely reasonable in the log."""
    clock, sink, sup, organ, lid, world = _rig(idle=EYELID_IDLE_S + 1)
    clock.advance(10_000)
    lid.tick()                                  # closes on idle
    world["locked"] = True
    clock.advance(10_000)
    lid.tick()                                  # deepens, does not re-close
    world["locked"] = False
    world["idle"] = 0.0
    clock.advance(10_000)
    lid.tick()                                  # one interval closes

    intervals = [c for c in sink if c.structured and c.structured.get("witness")
                 in (WitnessKind.AWAY.value, WitnessKind.CLOSED_EYES.value)]
    assert len(intervals) == 1
    assert intervals[0].structured["causes"] == ["idle", "locked"]
    # Both causes contributed, so it is no longer purely an absence of a
    # person — the machine was shut too.
    assert intervals[0].structured["witness"] == WitnessKind.CLOSED_EYES.value


def test_shutting_down_while_shut_closes_the_interval_honestly():
    """Otherwise the store holds an interval that opened and never closed —
    a hole running to the end of time, which reads to every later question
    as 'blind ever since'."""
    clock, sink, sup, organ, lid, world = _rig(locked=True)
    clock.advance(10_000)
    lid.tick()

    lid.force_open(clock.advance(30_000))

    assert WitnessKind.CLOSED_EYES.value in _kinds(sink)


def test_a_backwards_clock_is_witnessed_not_swallowed():
    """NTP correcting on wake is common and a lid close is exactly where it
    happens. A real blind interval must never vanish because the arithmetic
    came out negative."""
    clock, sink, sup, organ, lid, world = _rig(locked=True)
    clock.advance(10_000)
    lid.tick()
    world["locked"] = False
    clock.t -= 5_000                            # the clock moved backwards
    lid._last_poll_ms = None                    # let the poll run
    lid.tick()

    assert WitnessKind.CLOCK_JUMP.value in _kinds(sink)


# ---- 3. an unknown world never closes the eye -----------------------------

def test_an_unreadable_idle_clock_leaves_the_eye_open():
    """Unknown is a hole, never 'nobody is here'. Refusing to think is
    visible in the digest; going blind on a failed read is not."""
    clock, sink, sup, organ, lid, world = _rig()
    world["idle"] = None
    clock.advance(10_000)

    lid.tick()

    assert organ.alive() is True
    assert lid.eyes_open is True


def test_an_unreadable_lock_state_falls_through_to_the_idle_rule():
    """macOS omits the lock key when unlocked, so a None here means the read
    itself failed. The idle clock is the independent second signal that keeps
    a broken lock sensor from silently costing every night."""
    clock, sink, sup, organ, lid, world = _rig(idle=EYELID_IDLE_S + 1)
    world["locked"] = None
    clock.advance(10_000)

    lid.tick()

    assert organ.alive() is False
    assert lid.stats()["closed_because"] == "idle"


def test_a_sensor_that_raises_does_not_close_the_eye_or_kill_the_daemon():
    clock, sink, sup, organ, lid, world = _rig()

    def boom():
        raise OSError("Quartz went away")

    lid._read_locked = boom
    clock.advance(10_000)

    assert lid.tick() is None
    assert organ.alive() is True


# ---- the threshold is the founder's, not a knob ---------------------------

def test_the_threshold_is_the_founder_s_fifteen_minutes():
    """F-EYELID, 2026-07-29. Deliberately not capture_config's 300s AWAY
    threshold — that answers a different question ('has the human left?')
    with a different owner. One constant serving two questions is how a later
    session silently changes an answer the founder gave."""
    assert EYELID_IDLE_S == 900.0

    from tracemem.capture.capture_config import IDLE_AWAY_THRESHOLD_S
    assert EYELID_IDLE_S != IDLE_AWAY_THRESHOLD_S


def test_idle_below_the_threshold_keeps_the_eye_open():
    clock, sink, sup, organ, lid, world = _rig(idle=EYELID_IDLE_S - 1)
    clock.advance(10_000)

    lid.tick()

    assert organ.alive() is True


# ---- the whole chain, through the real daemon loop ------------------------

def test_the_night_actually_gets_a_slice_once_the_eye_is_parked(tmp_path):
    """THE INTEGRATION ORACLE, and the one that matters.

    Every link below was individually true before today and the product still
    got zero nights, because nothing joined them up:

        eyelid closes the eye
          -> supervisor reports it "parked", not "alive"
          -> __main__ signals set_capture(False)
          -> Spotlight.is_uncontended() finally answers True
          -> NightWatchman.tick() stops refusing and the brain thinks

    This drives the REAL `run()` loop with a fake organ and a stubbed world,
    and asserts the night ran. Testing the links separately is exactly how a
    chain that is broken end to end passes a green suite.
    """
    from tracemem.__main__ import run

    organ = _Organ()
    slices = {"n": 0}

    class _Spotlight:
        def __init__(self):
            self.capture_active = True

        def set_capture(self, active):
            self.capture_active = bool(active)

        def is_uncontended(self):
            return not self.capture_active

    class _Night:
        def __init__(self, spot):
            self._spot = spot

        def tick(self, reasoner=None):
            if not self._spot.is_uncontended():
                return None                      # the real guard's behaviour
            slices["n"] += 1
            return None

        def stats(self):
            return {"slices": slices["n"]}

    spot = _Spotlight()
    night = _Night(spot)

    class _Lid:
        """The real Eyelid, wired to a world that is always locked."""
        def __init__(self):
            self.inner = None

        def _build(self, sup, sink, now):
            self.inner = Eyelid(sup, sink, now,
                                read_idle=lambda: 0.0,
                                read_locked=lambda: True)
            return self.inner

    store = str(tmp_path / "s.sqlite3")
    lock = str(tmp_path / "l")

    # Build the eyelid against the run's own supervisor by handing run() a
    # factory-shaped object: run() accepts a ready-made eyelid, so construct
    # it after boot via a tiny shim that mirrors the production wiring.
    holder = {}

    class _LidProxy:
        def tick(self):
            return holder["lid"].tick() if "lid" in holder else None

        def force_open(self, t):
            return holder["lid"].force_open(t) if "lid" in holder else None

        def stats(self):
            return holder["lid"].stats() if "lid" in holder else {}

    import tracemem.app.eyelid as eyelid_mod
    real_eyelid = eyelid_mod.Eyelid

    def _capture(sup, sink, now, **kw):
        holder["lid"] = real_eyelid(sup, sink, now,
                                    read_idle=lambda: 0.0,
                                    read_locked=lambda: True, **kw)
        return holder["lid"]

    eyelid_mod.Eyelid = _capture
    try:
        report = run(store_path=store, seconds=0.6, lock_path=lock,
                     tick_seconds=0.02, organs=[organ],
                     brain=_StubBrain(), night=night, spotlight=spot)
    finally:
        eyelid_mod.Eyelid = real_eyelid

    # NOT asserted: the organ's state AFTER the run. `supervisor.stop()` stops
    # every organ on the way out, so "dead" at the end is the clean shutdown
    # working, not the eyelid failing.
    assert slices["n"] > 0, (
        "the night never got a slice — the chain from eyelid to "
        "is_uncontended is broken somewhere")
    assert organ.stops >= 1, "the eye was never actually closed"


class _StubBrain:
    """Just enough Brain for run()'s loop; the night is faked separately."""
    def absorb(self, claim):
        pass

    def backfill(self, claims, chunk=256, progress=None):
        return {"bound": 0, "episodes": 0}

    def claim_owner(self):
        pass

    def serve(self):
        pass

    def flush(self, force=False):
        pass

    def stats(self):
        return {"bound": 0}
