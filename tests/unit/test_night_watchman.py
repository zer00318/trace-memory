"""ORACLE for P2 — the brain is allowed to think, and only when it may.

Before this atom `run_night()` had ZERO production callers and had never
executed outside a test. These tests pin the two properties that decision
rests on: the night never competes with perception, and a night that runs out
of time yields instead of blocking the loop that answers questions.
"""
from __future__ import annotations

import pytest

from tracemem.app.night_watchman import (
    NightWatchman, _SlicedLease, DEFAULT_INTERVAL_MS, STUCK_SLICES,
)
from tracemem.app.scheduler import Spotlight


@pytest.fixture
def logged():
    """Collects what the watchman said. A fault the watchman notices but does
    not SAY is a fault nobody acts on — which is the whole finding of
    2026-08-10 — so the sentence is under test, not just the counter."""
    return []


class FakeClock:
    def __init__(self, t=0):
        self.t = t

    def now_ms(self):
        return self.t


class FakePower:
    def __init__(self, on_battery=False, charging=False):
        self.on_battery = on_battery
        self.charging = charging


class FakeBrain:
    """Records how it was asked to think and what lease it was handed."""

    def __init__(self, report=None, raises=None):
        self.report = report or _Report()
        self.raises = raises
        self.calls = 0
        self.leases = []

    def night(self, reasoner=None, lease=None):
        self.calls += 1
        self.leases.append(lease)
        if self.raises is not None:
            raise self.raises
        return self.report


class _Report:
    def __init__(self, status="COMPLETE", attempted=0, accepted=0):
        self.status = status
        self.attempted = attempted
        self.accepted = accepted
        self.rejected = 0
        self.mined = []
        self.pruned = []
        self.semantic_edges = 0


def _watchman(brain, clock, spotlight=None, **kw):
    return NightWatchman(brain, spotlight or Spotlight({}, clock),
                         clock.now_ms, **kw)


# -- the slice ------------------------------------------------------------

def test_sliced_lease_reports_preempted_once_the_slice_is_spent():
    clock = FakeClock(0)
    real = Spotlight({}, clock)
    lease = _SlicedLease(real, deadline_ms=100, now_ms=clock.now_ms)

    assert lease.preempted() is False
    assert lease.expired is False
    clock.t = 100                                   # deadline reached
    assert lease.preempted() is True
    assert lease.expired is True


def test_sliced_lease_delegates_everything_else_to_the_real_arbiter():
    """The proxy must not become a second implementation of the one-model
    law — it only adds a deadline."""
    clock = FakeClock(0)
    real = Spotlight({}, clock)
    lease = _SlicedLease(real, deadline_ms=10_000, now_ms=clock.now_ms)

    assert lease.is_uncontended() is True
    real.set_capture(True)
    assert lease.is_uncontended() is False          # delegated, not reimplemented
    with pytest.raises(Exception):
        with lease.acquire("m", role="DREAM", priority=0):
            pass


def test_a_night_that_overruns_its_slice_still_returns_and_is_recorded():
    """The real integration property: run_night polls preempted() and
    returns RESUMABLE, so a slice bounds wall-clock rather than work."""
    clock = FakeClock(0)

    class SlowBrain:
        def night(self, reasoner=None, lease=None):
            clock.t += 10_000                        # the night took too long
            return _Report(status="RESUMABLE" if lease.preempted() else "COMPLETE")

    w = _watchman(SlowBrain(), clock, slice_ms=2_000)
    report = w.tick()
    assert report.status == "RESUMABLE"
    assert w.slices == 1
    assert w.completed == 0                          # not counted as finished


# -- the gates ------------------------------------------------------------

def test_the_night_never_runs_while_the_eye_is_open():
    """THE load-bearing gate. Two models on Metal is a silent OOM and capture
    goes dark with no error, so thinking must lose to seeing."""
    clock = FakeClock(0)
    spotlight = Spotlight({}, clock)
    brain = FakeBrain()
    w = _watchman(brain, clock, spotlight=spotlight)

    spotlight.set_capture(True)
    assert w.tick() is None
    assert brain.calls == 0
    assert "capture is live" in w.last_skip

    spotlight.set_capture(False)
    assert w.tick() is not None
    assert brain.calls == 1


def test_the_night_does_not_run_on_battery_but_does_when_charging():
    clock = FakeClock(0)
    brain = FakeBrain()
    w = _watchman(brain, clock, power_fn=lambda: FakePower(on_battery=True))
    assert w.tick() is None
    assert w.last_skip == "on battery"

    clock.t += DEFAULT_INTERVAL_MS + 60_000          # past throttle + power cache
    w._power_fn = lambda: FakePower(on_battery=True, charging=True)
    assert w.tick() is not None                      # charging is not discharging


def test_an_unreadable_power_sense_never_blocks_thinking():
    """machine_sense's own stance: a failed read degrades to plugged-in,
    because the cost of being wrong is a night on battery, not lost memory."""
    clock = FakeClock(0)

    def boom():
        raise OSError("pmset went away")

    w = _watchman(FakeBrain(), clock, power_fn=boom)
    assert w.tick() is not None


def test_the_night_is_throttled_so_it_cannot_own_every_beat():
    clock = FakeClock(0)
    brain = FakeBrain()
    w = _watchman(brain, clock)

    assert w.tick() is not None
    assert w.tick() is None                          # immediately again: no
    assert w.last_skip == "throttled"
    clock.t += DEFAULT_INTERVAL_MS
    assert w.tick() is not None
    assert brain.calls == 2


def test_a_night_that_raises_does_not_take_the_daemon_with_it():
    """Perception outranks reflection: promise 1 ('it saw') is above promise 3
    ('it noticed') in the failure hierarchy, so a broken brain must not stop
    the eye or the socket."""
    clock = FakeClock(0)
    w = _watchman(FakeBrain(raises=RuntimeError("graph exploded")), clock)
    assert w.tick() is None                          # swallowed, not raised
    assert "RuntimeError" in w.last_skip
    assert "graph exploded" in w.last_skip


def test_totals_accumulate_across_slices_so_a_night_is_observable():
    clock = FakeClock(0)
    brain = FakeBrain(report=_Report(attempted=3, accepted=2))
    w = _watchman(brain, clock)
    w.tick()
    clock.t += DEFAULT_INTERVAL_MS
    w.tick()
    assert w.stats()["totals"]["attempted"] == 6
    assert w.stats()["totals"]["accepted"] == 4
    assert w.stats()["slices"] == 2


# -- the guard that was inert until today ---------------------------------

def test_set_capture_is_idempotent_so_a_per_beat_caller_cannot_flood_the_log():
    """The daemon signals capture state every tick (1 Hz). Before this, each
    call appended a log line to an unbounded list — 86,400 entries a day."""
    clock = FakeClock(0)
    s = Spotlight({}, clock)
    before = len(s.log)
    for _ in range(100):
        s.set_capture(True)
    assert len(s.log) == before + 1                  # ONE transition, not 100
    s.set_capture(False)
    assert len(s.log) == before + 2


def test_the_arbiter_log_is_bounded():
    from tracemem.app.scheduler import LOG_TAIL
    clock = FakeClock(0)
    s = Spotlight({}, clock)
    for i in range(LOG_TAIL + 500):
        s._log("event", str(i))
    assert len(s.log) <= LOG_TAIL
    assert s.log[-1][2] == str(LOG_TAIL + 499)       # the TAIL is what survives


# ---- convergence: a settled world is not re-thought ------------------------
#
# FOUND BY WATCHING THE FIRST REAL NIGHT. With the eyelid landed the brain
# finally got turns, and then took them forever: 52 slices on the founder's
# locked machine produced attempted 7,585 / accepted 44 / rejected 7,541 —
# fifty consecutive passes deriving nothing. The pair loop is ledger-guarded
# (I4); the pattern scan and semantic_pass are not, and semantic_pass
# re-embeds the hot set on the CPU every slice. Silent, looks like diligence
# in the log, and bills a locked laptop in heat for hours.

class _ConvBrain:
    def __init__(self, bound=100, episodes=5, nodes=10, status="COMPLETE"):
        self._s = {"bound": bound, "episodes": episodes, "nodes": nodes}
        self.status = status
        self.nights = 0

    def stats(self):
        return dict(self._s)

    def grow(self, claims=1):
        self._s["bound"] += claims

    def night(self, reasoner=None, lease=None):
        self.nights += 1
        return _ConvReport(status=self.status)


class _ConvReport:
    def __init__(self, status="COMPLETE"):
        self.status = status
        self.attempted = 3
        self.accepted = 0
        self.rejected = 3


def _conv_watchman(brain, clock):
    from tracemem.app.night_watchman import NightWatchman
    return NightWatchman(brain, _ConvSpot(), clock, require_ac=False,
                         interval_ms=0)


class _ConvSpot:
    def __init__(self):
        self.capture_active = False

    def is_uncontended(self):
        return not self.capture_active

    def acquire(self, role=None):
        return None


def test_a_second_pass_over_an_unchanged_world_is_skipped():
    """THE ORACLE. With no new claims, no new episodes and no new nodes,
    there is nothing the next pass could derive that the last one did not
    already consider and reject."""
    t = [1_000_000]
    brain = _ConvBrain()
    w = _conv_watchman(brain, lambda: t[0])

    w.tick()
    assert brain.nights == 1

    t[0] += 60_000
    w.tick()

    assert brain.nights == 1, "the world stood still; thinking again is waste"
    assert "settled" in (w.last_skip or "")


def test_one_new_claim_wakes_the_night_again():
    """The skip must never become a stall. Anything new is a reason to think."""
    t = [1_000_000]
    brain = _ConvBrain()
    w = _conv_watchman(brain, lambda: t[0])
    w.tick()

    brain.grow(1)
    t[0] += 60_000
    w.tick()

    assert brain.nights == 2


def test_an_unfinished_pass_never_declares_the_world_settled():
    """A RESUMABLE pass was cut off mid-slice and has unfinished work whether
    or not anything new arrived. Marking it settled would strand it."""
    t = [1_000_000]
    brain = _ConvBrain(status="RESUMABLE")
    w = _conv_watchman(brain, lambda: t[0])

    w.tick()
    t[0] += 60_000
    w.tick()

    assert brain.nights == 2
    assert w.stats()["settled"] is False


def test_the_mark_is_taken_before_the_pass_not_after():
    """Claims arrive while a pass runs. Marking the world settled at a state
    the pass never saw would skip exactly the work those claims created."""
    t = [1_000_000]

    class _ConvRacy(_ConvBrain):
        def night(self, reasoner=None, lease=None):
            self.nights += 1
            self.grow(5)                 # claims land DURING the pass
            return _ConvReport()

    brain = _ConvRacy()
    w = _conv_watchman(brain, lambda: t[0])
    w.tick()

    t[0] += 60_000
    w.tick()

    assert brain.nights == 2, "work created during a pass must not be skipped"


def test_settled_is_visible_because_idle_and_broken_look_alike():
    """'The night is doing nothing' and 'the night has nothing left to do'
    are opposite facts that look identical from outside."""
    t = [1_000_000]
    brain = _ConvBrain()
    w = _conv_watchman(brain, lambda: t[0])
    assert w.stats()["settled"] is False

    w.tick()

    assert w.stats()["settled"] is True


def test_an_unreadable_brain_does_not_freeze_the_night():
    """If the cheap signal cannot be read, the night must run rather than
    silently stop — a convergence check that fails closed is a night that
    never happens again."""
    t = [1_000_000]

    class _ConvMute(_ConvBrain):
        def stats(self):
            raise RuntimeError("no")

    brain = _ConvMute()
    w = _conv_watchman(brain, lambda: t[0])
    w.tick()
    t[0] += 60_000
    w.tick()

    assert brain.nights == 2


# ---- the livelock: a pass that can never finish and never derives ----------
#
# FOUND ON THE LIVE DAEMON 2026-08-10, twelve days after the settle mark
# shipped and believed itself to have fixed this. The settle mark is set only
# by a COMPLETE pass. Once the real store outgrew what one 2s slice can walk,
# no pass ever completed again, so `_settled_on` became unreachable and the
# skip could never fire. The founder's machine ran 16,828 slices, completed
# 10, attempted 15,590,330, accepted 409 — and past slice ~3,040 it stopped
# even attempting, expiring inside the scan. 196.7 CPU-hours of heat.
#
# The lesson these tests encode: "unfinished" was the wrong invariant.
# FORWARD PROGRESS is the invariant. Attempts are not progress.

class _StuckReport:
    """What the real night returned, slice after slice: cut off, enormous
    effort, nothing accepted."""

    def __init__(self, attempted=7159):
        self.status = "RESUMABLE"
        self.attempted = attempted
        self.accepted = 0
        self.rejected = attempted
        self.mined = []
        self.pruned = []
        self.semantic_edges = 0


class _StuckBrain(_ConvBrain):
    def night(self, reasoner=None, lease=None):
        self.nights += 1
        return _StuckReport()


def test_a_pass_that_can_never_finish_does_not_run_forever():
    """THE ORACLE. A RESUMABLE pass deriving nothing from an unchanged world
    must stand down. Before this, it ran every 30s until the machine died."""
    t = [1_000_000]
    brain = _StuckBrain()
    w = _conv_watchman(brain, lambda: t[0])

    for _ in range(50):
        t[0] += 60_000
        w.tick()

    assert brain.nights == STUCK_SLICES, (
        "the night kept thinking after %d barren passes over a world that "
        "never moved — this is the 16,828-slice livelock" % brain.nights)
    assert w.stats()["stuck"] is True


def test_stuck_is_not_reported_as_settled():
    """They call for the same action and mean opposite things. Settled is the
    night succeeding; stuck is the night broken. Collapsing them into one flag
    is how a defect gets read as rest for twelve days."""
    t = [1_000_000]
    brain = _StuckBrain()
    w = _conv_watchman(brain, lambda: t[0])

    for _ in range(10):
        t[0] += 60_000
        w.tick()

    s = w.stats()
    assert s["stuck"] is True
    assert s["settled"] is False, "a night that never finished is not settled"
    assert "STUCK" in (w.last_skip or "")
    assert "settled" not in (w.last_skip or "").lower().replace("not settled", "")


def test_anything_new_frees_a_stuck_night_immediately():
    """The stall must never outlive the condition. One new claim and the night
    thinks again — otherwise this fix is a worse bug than the livelock."""
    t = [1_000_000]
    brain = _StuckBrain()
    w = _conv_watchman(brain, lambda: t[0])

    for _ in range(10):
        t[0] += 60_000
        w.tick()
    stalled_at = brain.nights

    brain.grow(1)
    t[0] += 60_000
    w.tick()

    assert brain.nights == stalled_at + 1
    assert w.stats()["stuck"] is False


def test_attempts_alone_are_never_counted_as_progress():
    """15,590,330 attempted / 409 accepted is the failure, not the work. A
    guard that scored attempts would call the livelock the busiest night ever
    recorded and skip nothing."""
    t = [1_000_000]
    brain = _StuckBrain()
    w = _conv_watchman(brain, lambda: t[0])

    for _ in range(10):
        t[0] += 60_000
        w.tick()

    assert w.stats()["stuck"] is True
    assert w.totals["attempted"] > 0, "it really did burn effort"
    assert w.totals["accepted"] == 0


def test_a_pass_that_derives_something_is_left_alone():
    """Slow but productive is not stuck. A night chewing through a large store
    a slice at a time is the DESIGN, and must not be stood down."""
    t = [1_000_000]

    class _SlowButWorking(_ConvBrain):
        def night(self, reasoner=None, lease=None):
            self.nights += 1
            r = _StuckReport()
            r.accepted = 1               # one real belief per slice
            return r

    brain = _SlowButWorking()
    w = _conv_watchman(brain, lambda: t[0])

    for _ in range(20):
        t[0] += 60_000
        w.tick()

    assert brain.nights == 20
    assert w.stats()["stuck"] is False


# ---- the slice that was never a bound -------------------------------------
#
# `_SlicedLease` stops the night only WHERE THE NIGHT POLLS. On a large store
# a phase with no poll inside it runs to completion regardless of the
# deadline, so DEFAULT_SLICE_MS was a request, not a limit — and nothing
# measured the gap. Because the night shares the daemon's loop thread with
# `brain.serve()`, that gap is precisely how long the product is mute.
#
# WITNESSED 2026-08-10: `./trace "what was I doing?"` returned BrainNotServing
# after 120s while `sample` showed the main thread executing Python inside a
# night slice for the entire sampling window. The log said "expired=True",
# which reads like the limit worked.

def test_a_slice_that_overruns_its_budget_is_counted_and_said(logged):
    """THE ORACLE. An overrun must produce a number and a sentence. It was
    invisible, and invisible is why it ran for twelve days."""
    t = [1_000_000]

    class _Hog(_ConvBrain):
        def night(self, reasoner=None, lease=None):
            self.nights += 1
            t[0] += 120_000          # holds the loop for two minutes
            return _ConvReport()

    brain = _Hog()
    from tracemem.app.night_watchman import NightWatchman
    w = NightWatchman(brain, _ConvSpot(), lambda: t[0], require_ac=False,
                      interval_ms=0, slice_ms=2_000, log=logged.append)
    w.tick()

    s = w.stats()
    assert s["overruns"] == 1
    assert s["worst_slice_ms"] >= 120_000
    assert s["slice_budget_ms"] == 2_000
    assert any("OVERRAN" in m for m in logged), (
        "a slice held the answering thread for 2 minutes and said nothing")


def test_a_slice_inside_its_budget_is_not_an_overrun(logged):
    """The mechanism working as designed must not register as a fault, or the
    counter becomes noise and stops being read."""
    t = [1_000_000]

    class _Polite(_ConvBrain):
        def night(self, reasoner=None, lease=None):
            self.nights += 1
            t[0] += 1_500            # under the 2s budget
            return _ConvReport()

    from tracemem.app.night_watchman import NightWatchman
    w = NightWatchman(_Polite(), _ConvSpot(), lambda: t[0], require_ac=False,
                      interval_ms=0, slice_ms=2_000, log=logged.append)
    w.tick()

    assert w.stats()["overruns"] == 0
    assert not any("OVERRAN" in m for m in logged)


def test_the_pattern_scan_does_not_redo_itself_on_an_unchanged_node():
    """THE LIVELOCK'S THIRD MECHANISM, measured 2026-08-20.

    Every slice reported `attempted=1553 accepted=0`, identically. All 1,553
    attempts were the pattern scan re-detecting patterns that already exist
    as nodes, so the judge rejected each one as a duplicate. That consumed
    the entire slice BEFORE `select_candidates` was reached, so no pair was
    ever processed, the pair ledger never advanced, and the next slice
    repeated exactly the same work. The pairs had a ledger since July; the
    pattern scan never did.
    """
    from tracemem.domain import Confidence, Grade, Node, NodeKind
    from tracemem.domain.node import Attribute
    from tracemem.nodes.graph_store import MemoryGraphStore
    from tracemem.nodes.marination.nightly import run_night

    g = MemoryGraphStore()
    for i in range(6):
        g.upsert_node(Node(
            id="n%d" % i, kind=NodeKind.SITE, activation=0.9,
            last_active_t_ms=1000,
            attributes=[Attribute(key="site", value="site%d.com" % i,
                                  grade=Grade.VERBATIM,
                                  confidence=Confidence(0.9),
                                  source_episode_ids=("e1", "e2"),
                                  t_ms=1000)]))

    from tests.fakes import FakeClock as _FC, FakeLease

    lease = FakeLease()
    clock = _FC(2000)
    first = run_night(g, None, None, lease, clock)
    second = run_night(g, None, None, lease, clock)

    assert second.attempted == 0, (
        "the pattern scan repeated itself over an unchanged world — this is "
        "the loop that burned 358 CPU-hours across 34,799 slices")
    assert first.status == "COMPLETE" and second.status == "COMPLETE"


# ---- convergence, part two: the ENGINE is part of the world ---------------
#
# FOUND 2026-08-28 by reading the founder's live graph against the code that
# was supposed to have repaired it. `pattern.py` gained a table that says
# beliefs in English and `marination/relabel.py` was written to re-say the
# ones already minted; both landed after the last night that ran. The store
# then went quiet, `_world_mark` (claims, episodes, nodes) stopped moving,
# and `_settled_on` answered True for ever — so the repair never executed and
# 78 beliefs stayed database rows in the one graph they were written for.
# Running the same code against a snapshot re-said 50 and retired 28 in one
# pass: the work was reachable the whole time, behind a check about data.

def test_new_derivation_code_is_a_reason_to_think_again(monkeypatch):
    """THE ORACLE. A store with no new claims still has work the moment the
    engine that derives from it changes."""
    import tracemem.app.night_watchman as nw

    t = [1_000_000]
    brain = _ConvBrain()
    w = _conv_watchman(brain, lambda: t[0])

    w.tick()
    assert brain.nights == 1

    t[0] += 60_000
    w.tick()
    assert brain.nights == 1, "unchanged world, unchanged engine: correctly idle"

    # The derivation engine is edited. Nothing about the evidence moved.
    monkeypatch.setattr(nw, "_CODE_VERSION", "a-different-engine")
    t[0] += 60_000
    w.tick()

    assert brain.nights == 2, (
        "a changed engine derives differently from the same evidence; a "
        "stand-down keyed only on data makes every derivation fix invisible "
        "on an existing graph")


def test_the_engine_fingerprint_changes_when_a_deriving_module_changes(tmp_path):
    """It must be the BYTES, not a version anyone has to remember to bump."""
    from tracemem.nodes import engine_version as ev

    ev._CACHED = None
    first = ev.engine_fingerprint()
    assert len(first) == 12

    ev._CACHED = None
    second = ev.engine_fingerprint()
    assert second == first, "same source must fingerprint the same"

    # Swap one module out of the set and the fingerprint must move.
    ev._CACHED = None
    monkey = tuple(m for m in ev._ENGINE if "pattern" not in m)
    real, ev._ENGINE = ev._ENGINE, monkey
    try:
        assert ev.engine_fingerprint() != first
    finally:
        ev._ENGINE = real
        ev._CACHED = None


def test_the_chain_audit_gets_its_share_even_when_the_night_uses_all_of_its():
    """WITNESSED 2026-10-14: the store's chain audit sat at row 320,000 for
    every slice of a busy night. It ran AFTER the night against the same
    hard deadline, and a night with work always spends its whole lease, so
    the audit's first poll found the deadline already gone. Integrity work
    that only runs when thinking is idle never runs while there is anything
    to think about, which is exactly when the store is changing."""
    clock = FakeClock(10_000_000)

    class GreedyBrain(FakeBrain):
        def night(self, reasoner=None, lease=None):
            clock.t += 60_000                       # spends the whole slice
            return super().night(reasoner=reasoner, lease=lease)

    w = _watchman(GreedyBrain(_Report(status="RESUMABLE")), clock)
    had_time = []
    w._audit_slice = lambda stop=None: had_time.append(not stop()) or {
        "ok": True, "cycle_complete": False}
    w.tick()
    assert had_time == [True], "the audit was handed an already-spent slice"


def test_the_audit_keeps_pace_while_the_night_cannot_run():
    """WITNESSED 2026-10-15 (independent review): the chain audit covered
    365k of 1.26M rows and fell further behind every active day, because it
    ran only inside night slices and the night never runs while capture is
    live. While the night is refused, a bounded audit share still runs, at
    most once per interval."""
    clock = FakeClock(10_000_000)
    w = _watchman(FakeBrain(), clock)
    w.why_not = lambda: "capture is live"
    budgets = []

    def audit(stop=None):
        started = clock.t
        budgets.append(stop)
        clock.t += 50
        return {"ok": True, "cycle_complete": False, "started": started}

    w._audit_slice = audit
    assert w.tick() is None
    assert len(budgets) == 1, "no daytime audit while the night was refused"
    clock.t += 1_000
    w.tick()
    assert len(budgets) == 1, "the daytime share ran more than once an interval"
    clock.t += 60_000
    w.tick()
    assert len(budgets) == 2
    # the share is bounded: its stop fires within the declared budget
    stop = budgets[-1]
    clock.t += 10_000
    assert stop() is True
