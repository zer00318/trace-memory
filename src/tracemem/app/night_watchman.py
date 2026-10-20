"""OWNS: when the brain is allowed to think, and for how long at a stretch
(priority P2, 2026-07-29).

THE GAP THIS CLOSES. `run_night()` — succession, schema crystallization,
re-marination mining, semantic wiring, all four combination kinds, preference
derivation, identity linking, pruning, reconfirmation — had **zero production
callers**. It has never executed once outside a test. Every claim the napkin
makes about the node tier ("a physical living breathing cell that can combine
with fellow nodes to derive new things — 1+1=4") is downstream of this
function, so the product's entire differentiator had no evidence for or
against it.

THE FOUNDER'S INSTRUCTION was "continuous, but low power" — background
metabolism rather than a nightly batch. That phrase has a precise technical
meaning here, and it is not about watts:

  · The GPU is the scarce thing. Two models resident on Metal is a silent
    OOM, so the night must never hold the reasoner while capture or a
    question needs it.
  · The GIL is the second scarce thing. The eye once pinned a core at 99.7%
    and starved the loop that answers questions — capture kept working while
    the product stopped responding. The night runs on that SAME loop thread
    (the one-owner store law, see app/brain.py), so a long night call is a
    long blindness.

Hence SLICES. A night is not run to completion; it is run for a couple of
seconds and abandoned, resumed on a later tick. That is safe because the
night was already built to be abandoned: it returns RESUMABLE, every write it
makes is committed inline, and the processed-work ledgers (graph_store's
upkeep/mining ledgers) mean resumed work is never redone.

WHAT WAS BROKEN AND IS FIXED HERE (found 2026-07-29 by grepping for the
guard's callers): `Spotlight.set_capture()` was called ONLY in tests. So
`capture_active` was permanently False in production, `is_uncontended()`
always said yes, and `acquire(role="DREAM")` — the guard whose own error
message reads "capture goes dark silently otherwise (O3)" — could never
refuse. Wiring the night on top of an inert guard would have shipped exactly
the failure the guard exists to prevent. `__main__` now signals capture state
every tick, and this class refuses to think while the eye is open.

SPEC: 04 §7 (the night), 08 §1-3 (the arbiter)
ORACLE: tests/unit/test_night_watchman.py — a night that overruns its slice
        yields RESUMABLE and resumes where it left off; a night never starts
        while capture is live or the machine is on battery.
"""
from __future__ import annotations

from typing import Callable, Optional

from tracemem.nodes.engine_version import engine_fingerprint as _engine_fp

# The vintage of the machinery that derives. Read once — see
# nodes/engine_version.py for why this is a fingerprint of the engine's source
# rather than a hand-bumped version string.
_CODE_VERSION = _engine_fp()

# One abandonable slice. Short on purpose: this runs on the daemon's own loop
# thread, so it is time the product cannot answer a question or drain a frame.
# Two seconds is well under the ask socket's patience and far under the
# supervisor's restart window, and the night resumes on the very next tick —
# so a short slice costs throughput, never progress.
# THE SLICE, raised from 2s on 2026-08-20 because the constraint it was
# protecting no longer exists. A 2s budget existed so the night could not
# starve the loop that answers questions — but a question about a period is
# now served by app/window_answer.py, which reads the time-indexed claim table
# on its own connection and never touches the brain at all.
#
# Meanwhile 2s was measurably too short to do ANY work: `select_candidates`
# alone costs 1.85s on the founder's graph (851 nodes, two-hop expansion from
# 137 hot ones), so every slice spent its entire budget building a work list,
# hit `preempted()` on the pair loop's first iteration, and threw the list
# away. A slice that cannot outlast its own setup is not a small unit of
# work; it is zero units of work, repeated forever.
DEFAULT_SLICE_MS = 10_000

# How often a slice may be attempted. The night is CONTINUOUS (founder
# ruling F-NIGHT), not nocturnal — this is a throttle that keeps the brain
# from occupying every tick, not a schedule.
DEFAULT_INTERVAL_MS = 30_000

# `pmset -g batt` is a subprocess. At one tick per second that would be 86,400
# subprocesses a day to answer a question that changes when someone touches a
# cable. capture_config declares 30s as the power poll interval; this matches
# it rather than inventing a second number.
POWER_CACHE_MS = 30_000

# How many consecutive slices may derive NOTHING from an unchanged world
# before the night is declared stuck rather than merely unfinished.
#
# WHY THIS NUMBER EXISTS AT ALL (found 2026-08-10 on the live daemon, 12 days
# after the settle-mark shipped). The settle mark is only set by a COMPLETE
# pass — deliberately, because a RESUMABLE pass has unfinished work. That is
# sound only while a pass can still FINISH. Once the store grew past what one
# 2s slice can walk, every pass returned RESUMABLE forever, `_settled_on` went
# permanently unreachable, and the skip that this whole mechanism exists to
# perform could never fire again. Measured on the founder's machine:
#
#     slices 16,828 · completed 10
#     attempted 15,590,330 · accepted 409 · rejected 15,589,921  (0.0026%)
#
# — 196.7 CPU-hours, and past slice ~3,040 it stopped even attempting: the
# slice expired inside the scan, before the first combination. So the fix that
# landed for 52 slices came back at 16,828, because it keyed on the wrong
# thing. "Unfinished" is not the invariant that matters; FORWARD PROGRESS is.
# A pass that derives nothing, twice running, from a world that has not moved
# is not mid-flight — it is looping, and every further slice is heat.
#
# Three rather than two: one slice can legitimately spend itself entirely on
# the scan that sets up the next one, and a two-strike rule would call that
# stuck. Three costs one extra slice and removes that whole class of false
# positive. Anything NEW in the world clears the streak instantly, so this can
# stall the night only for exactly as long as nothing is happening.
STUCK_SLICES = 3

# How far past its budget a slice may run before it is reported as an overrun.
# Three, not one: the deadline is checked at poll points, so a slice that ends
# slightly late is the mechanism working as designed, and flagging that would
# make the counter noise. Three times the budget is not lateness, it is a
# phase that never polled at all.
OVERRUN_FACTOR = 3

# Leave a small part of the advertised slice for integrity and graph-health
# maintenance.  The reasoning lease gets the rest; the hard deadline still
# covers everything run on the one owner/answer thread.
MAINTENANCE_RESERVE_MS = 1_000

# The audit's share of the owner thread while the night is refused: 300ms at
# most once a minute (an ask waits at most that long behind it). UNCALIBRATED
# shape; sized so audit throughput exceeds ingest (see _daytime_audit).
DAYTIME_AUDIT_MS = 300
DAYTIME_AUDIT_EVERY_MS = 60_000


def _phase_ms(report) -> str:
    """'N0=12 N2=9840 N3=40' — where the slice's time went."""
    import time as _t
    starts = list(getattr(report, "phase_starts", None) or [])
    if not starts:
        return "-"
    ends = [t for _n, t in starts[1:]] + [_t.monotonic()]
    return " ".join("%s=%d" % (n, max(0, (e - t0) * 1000))
                    for (n, t0), e in zip(starts, ends))


class _SlicedLease:
    """The Spotlight, wearing a deadline.

    `run_night` already yields wherever it polls `lease.preempted()`, and
    those poll points were chosen by the person who wrote the night to be the
    places it is safe to stop. So a proxy that answers "preempted" once the
    slice's time is spent turns the EXISTING preemption machinery into a time
    slicer — with zero changes to the night's own code, and no new yield
    points for anyone to get wrong later.

    Everything except `preempted` delegates to the real Spotlight, so the
    turn, the residency and the one-model law are all still enforced by the
    one object that owns them.
    """

    def __init__(self, lease, deadline_ms: int,
                 now_ms: Callable[[], int]) -> None:
        self._lease = lease
        self._deadline_ms = deadline_ms
        self._now_ms = now_ms
        self.expired = False

    def preempted(self) -> bool:
        if self._now_ms() >= self._deadline_ms:
            # Recorded rather than merely returned: a caller needs to tell
            # "I ran out of time" apart from "a question arrived", because
            # only the first one means the slice should be longer.
            self.expired = True
            return True
        return self._lease.preempted()

    def interrupted(self) -> bool:
        """A PERSON is waiting (not merely: the slice ran out). Model calls
        poll this between chunks; the deadline alone must not cancel a
        generation that cannot fit in any slice, or the kinds that need the
        model would never finish one."""
        return self._lease.preempted()

    def __getattr__(self, name):
        # Only reached for attributes this proxy does not define, so acquire /
        # is_uncontended / signal_query all land on the real arbiter.
        return getattr(self._lease, name)


class NightWatchman:
    """Decides whether the brain may think right now, and gives it one slice.

    Owned by the daemon's loop thread. `tick()` is called every beat and is
    cheap when the answer is no — which is most beats.
    """

    def __init__(self, brain, spotlight, now_ms: Callable[[], int],
                 power_fn: Optional[Callable] = None,
                 slice_ms: int = DEFAULT_SLICE_MS,
                 interval_ms: int = DEFAULT_INTERVAL_MS,
                 require_ac: bool = True,
                 log: Optional[Callable[[str], None]] = None,
                 model_ready: Optional[Callable[[], bool]] = None) -> None:
        self._brain = brain
        # Is the model server up? A night started against a dead server
        # spent each slice inside the reasoner's retry backoff (2026-10-09).
        self._model_ready = model_ready
        self._spotlight = spotlight
        self._now_ms = now_ms
        self._power_fn = power_fn
        self._slice_ms = slice_ms
        self._interval_ms = interval_ms
        # A BOOL, OR SOMETHING THAT KNOWS THE CURRENT ANSWER. `night_on_
        # battery` was a setting nobody read — this came from a CLI flag and
        # the file was ignored. Accepting a callable lets the answer change
        # between nights without a restart, which is what the leaf's oracle
        # asks for ("change it → the scheduler honors it that night").
        self._require_ac_src = require_ac
        self._log = log or (lambda m: None)

        self._last_attempt_ms: Optional[int] = None
        self._power = None
        self._power_read_ms: Optional[int] = None

        # Observability. A night nobody can see is a night nobody can grade,
        # and grading it is the whole point (F-GRADE).
        self.slices = 0
        self.completed = 0
        self.last_report = None
        self.last_audit = None
        self.last_vitals = None
        self.last_skip: Optional[str] = None
        self.totals = {"attempted": 0, "accepted": 0, "rejected": 0,
                       "mined": 0, "hunger_completed": 0,
                       "pruned": 0, "semantic_edges": 0}
        # THE SETTLED MARK (2026-07-29). The state of the world as of the last
        # night that ran to COMPLETION. See `_settled_on` for why this exists.
        self._settled_at: Optional[tuple] = None
        # THE STUCK MARK (2026-08-10). The state of the world that consecutive
        # passes have failed to derive anything from. Distinct from the settled
        # mark on purpose: settled means "finished and there is nothing left",
        # stuck means "cannot finish and is producing nothing". They call for
        # the same action (stop burning the loop) and must NOT be reported as
        # the same fact — one is success, the other is a bug report.
        self._exhausted_at: Optional[tuple] = None
        self._stuck_mark: Optional[tuple] = None
        self._stuck_streak = 0
        # Slice cost, measured rather than assumed. See `tick`.
        self.last_slice_ms = 0
        self.worst_slice_ms = 0
        self.overruns = 0

    # -- the gates ---------------------------------------------------------

    @property
    def _require_ac(self) -> bool:
        src = self._require_ac_src
        if callable(src):
            try:
                return bool(src())
            except Exception:                                # noqa: BLE001
                return True          # when unsure, never on a cell
        return bool(src)

    def _on_battery(self) -> bool:
        """True only when this machine is DEMONSTRABLY running off a cell.

        A failed read, an unknown pmset dialect, or a desktop with no battery
        all answer False — matching machine_sense.parse_power's own stance,
        where "no battery" is the reading that causes the least downstream
        harm. The cost of being wrong here is a night that runs on battery,
        not a memory that loses something.
        """
        if self._power_fn is None:
            return False
        now = self._now_ms()
        if (self._power_read_ms is None
                or now - self._power_read_ms >= POWER_CACHE_MS):
            try:
                self._power = self._power_fn()
            except Exception:            # noqa: BLE001 — a sense, not a law
                self._power = None
            self._power_read_ms = now
        p = self._power
        if p is None:
            return False
        return bool(getattr(p, "on_battery", False)
                    and not getattr(p, "charging", False))

    def _world_mark(self) -> Optional[tuple]:
        """A cheap signature of everything the night could derive FROM.

        Claims bound, episodes, nodes — AND THE CODE THAT WOULD DERIVE THEM.
        Counts only — no scan, no hash of the graph — because this runs on
        every beat and a convergence check that costs as much as the work it
        saves saves nothing.

        THE CODE VERSION IS PART OF THE WORLD (2026-08-28). Without it this
        mark answers "has new evidence arrived?" and the guard reads it as
        "is there anything left to think about?" — two different questions
        that agree only while the derivation engine is frozen.

        MEASURED on the founder's real graph. `pattern.py` gained a table
        that says beliefs in English instead of printing `recurring:
        page=/watch observed in 230 distinct moments`, and
        `marination/relabel.py` was written to re-say the ones already
        minted. Both landed on 2026-08-26, after the last night that ran.
        The store then went quiet, so the mark stopped moving, `_settled_on`
        answered True for ever, and the repair could never execute: 78
        database rows stayed database rows in the one graph they were written
        for. Running the current code against a snapshot re-said 50 beliefs
        and retired 28 in a single pass — work that was reachable the whole
        time and gated behind a check about data.

        A store with no new claims still has work to do the moment the engine
        changes. `builder_version` already stamps every derived node with the
        code that made it, for exactly this reason; this makes the night's own
        stand-down honour the same fact.
        """
        try:
            s = self._brain.stats()
        except Exception:                # noqa: BLE001 — a check, not a law
            return None
        try:
            return (int(s.get("bound", -1)), int(s.get("episodes", -1)),
                    int(s.get("nodes", -1)), _CODE_VERSION)
        except (TypeError, ValueError):
            return None

    def _settled_on(self, mark) -> bool:
        """Has the world stood still since the last night that FINISHED?

        FOUND BY WATCHING THE FIRST REAL NIGHT (2026-07-29). With the eyelid
        landed, the brain finally got turns — and then kept taking them
        forever. After 52 slices on the founder's locked machine:

            attempted 7,585 · accepted 44 · rejected 7,541

        Fifty consecutive slices produced ZERO new beliefs. The pair loop is
        already guarded by the combination ledger (I4), but the pattern scan
        and `semantic_pass` are not: both re-run over the whole hot set every
        slice, and `semantic_pass` re-embeds it on the CPU each time. The
        cumulative semantic-edge counter read 988 against 19 edges actually in
        the graph — the same work, done fifty times, thrown away fifty times.

        Nobody would have seen this. It is silent, it looks like diligence in
        the logs, and it bills the founder's laptop in heat and battery for
        hours every night. It also only became reachable BECAUSE the eyelid
        started working, so it is this feature's own bill to pay.

        WHY SKIPPING IS SOUND, and not merely cheap: a node's activation only
        RISES on a sighting, and a sighting requires a new claim. Decay only
        ever removes nodes from the hot set. So with no new claims, no new
        episodes and no new nodes, there is nothing the next pass could
        derive that the last one did not already consider and reject.

        Deliberately NOT a reason to skip: a RESUMABLE pass. A night that was
        cut off mid-slice has unfinished work regardless of whether the world
        moved, so only a COMPLETE pass sets the mark.
        """
        return mark is not None and self._settled_at == mark

    # The fields of a NightReport that mean the graph actually CHANGED. Counted
    # rather than inspected, and deliberately NOT including `attempted` or
    # `rejected`: attempting a million combinations and accepting none is
    # exactly the failure this guard exists to catch, and a guard that counts
    # attempts as progress would score that as the busiest night on record.
    _PROGRESS_FIELDS = ("accepted", "scanned", "mined", "hunger_completed",
                        "hunger_chunks",
                        "pruned", "semantic_edges",
                        "identity_links", "merges", "succession_observed",
                        "reconfirmed", "preferences", "schemas_touched",
                        "people_judged", "people_unbirthed")

    def _audit_slice(self, stop=None):
        """One round-robin slice of the hash chain. See text/chain_audit."""
        from tracemem.text.chain_audit import audit_slice

        log = getattr(getattr(self._brain, "_hub", None), "w", None)
        log = getattr(log, "claim_log", None)
        if log is None:
            return None
        r = audit_slice(log, stop=stop)
        if not r.get("ok"):
            # LOUD, AND NOT ONLY IN A COUNTER. This is the one thing the
            # never-lie promise rests on.
            self._log("!! CHAIN AUDIT FOUND A BREAK at claim %s (%s), rows "
                      "%s-%s of %s" % (r.get("broken_at"), r.get("reason"),
                                       r.get("from"), r.get("to"),
                                       r.get("total")))
        elif r.get("cycle_complete"):
            self._log("chain audit: a full cycle completed clean — every "
                      "claim in the store has now been verified once.")
        return r

    def _daytime_audit(self) -> None:
        """A bounded audit share while the night is refused (2026-10-15).

        The audit ran only in night slices, and the night never runs while
        capture is live: on the founder's store it had covered 365k of 1.26M
        rows and fell further behind every active day. At most once per
        DAYTIME_AUDIT_EVERY_MS it gets DAYTIME_AUDIT_MS on the owner thread.
        Measured 10-14: ~1.5k rows per second of audit, so this is ~450 rows a
        minute against ~160k claims a day of ingest.
        """
        now = self._now_ms()
        if now - getattr(self, "_last_day_audit_ms", -10**15) < DAYTIME_AUDIT_EVERY_MS:
            return
        self._last_day_audit_ms = now
        until = now + DAYTIME_AUDIT_MS
        try:
            self.last_audit = self._audit_slice(
                stop=lambda: self._now_ms() >= until)
        except Exception as e:                               # noqa: BLE001
            self._log("chain audit (daytime) failed: %s: %s"
                      % (type(e).__name__, e))

    def _forward_progress(self, report) -> int:
        """How much this pass actually added to the graph. Zero means the
        slice cost time and left the world exactly as it found it."""
        total = 0
        for name in self._PROGRESS_FIELDS:
            v = getattr(report, name, None)
            if isinstance(v, bool):          # before int: bool IS an int
                total += int(v)
            elif isinstance(v, int):
                total += v
            elif isinstance(v, (list, tuple, dict, set)):
                total += len(v)
        return total

    def _stuck_on(self, mark) -> bool:
        """Have consecutive passes failed to derive anything from THIS world?

        Keyed on the mark, so the moment a claim, an episode or a node arrives
        the streak is void and the night is free to think again. A stall that
        outlives the condition that caused it would be a worse bug than the
        livelock it replaces.
        """
        return mark is not None and self._exhausted_at == mark

    def why_not(self) -> Optional[str]:
        """The reason the brain may not think right now, or None.

        Returned as a sentence rather than a bool so `--stats` can say WHY
        the night is not running. "It just isn't" is the kind of answer that
        costs an afternoon.
        """
        now = self._now_ms()
        if (self._last_attempt_ms is not None
                and now - self._last_attempt_ms < self._interval_ms):
            return "throttled"
        # The one-model law, asked of the object that owns it. This is the
        # check that was inert until capture state started being signalled.
        if not self._spotlight.is_uncontended():
            return ("capture is live or a turn is held — the night never "
                    "shares the GPU (O3)")
        if self._require_ac and self._on_battery():
            return "on battery"
        if self._model_ready is not None and not self._model_ready():
            return ("the model server is down — waiting for it to be "
                    "restarted")
        mark = self._world_mark()
        if self._settled_on(mark):
            return ("settled — nothing new to think about since the last "
                    "completed pass")
        # Said as a DEFECT, not as rest. "settled" is the night reporting
        # success; this sentence is the night reporting that it cannot finish
        # a pass on a store this size and is producing nothing while trying.
        # Whoever reads `--stats` needs to be able to tell those apart without
        # reading the log, which is the mistake that let this run for 12 days.
        if self._stuck_on(mark):
            return ("STUCK — %d consecutive slices derived nothing from an "
                    "unchanged world; the pass cannot finish inside a slice. "
                    "Not settled: this is a defect, not rest."
                    % self._stuck_streak)
        return None

    # -- the beat ----------------------------------------------------------

    def tick(self, reasoner=None):
        """One opportunity to think. Returns a NightReport, or None if the
        brain was not allowed to (or had nothing to do)."""
        reason = self.why_not()
        if reason is not None:
            self.last_skip = reason
            self._daytime_audit()
            return None
        self.last_skip = None
        self._last_attempt_ms = self._now_ms()
        # Read BEFORE the pass. Claims can arrive while it runs, and marking
        # the world as settled at a state the pass never actually saw would
        # skip the very work those claims created.
        mark_before = self._world_mark()

        slice_started_ms = self._now_ms()
        hard_deadline_ms = slice_started_ms + self._slice_ms
        reserve = min(MAINTENANCE_RESERVE_MS, max(0, self._slice_ms // 4))

        # THE STORE AUDITS ITSELF WHILE IT THINKS, AND IT GOES FIRST.
        #
        # Boot verifies a tail; that leaves everything older unaudited for
        # ever, and everything older is where a memory's value lives. This
        # walks a slice per night, round-robin, with the cursor kept IN the
        # store — a position held in memory restarts at zero whenever the
        # daemon does, so a daemon that restarts more often than the cycle
        # length would re-audit the same first rows for ever while claiming
        # to sweep the whole thing.
        #
        # FIRST, with its own share (2026-10-14). It used to run after the
        # night against the same hard deadline; a night with work always
        # spends its whole lease, so on the live store the audit's first
        # poll found the deadline gone and the cursor sat at row 320,000 for
        # every slice of a busy night. Its share comes out of the maintenance
        # reserve, and the night's own lease is unchanged.
        #
        # Guarded and never fatal: a memory that stops THINKING because its
        # integrity check stumbled has traded the thing it is for a check on
        # the thing it is.
        audit_until_ms = slice_started_ms + reserve
        try:
            self.last_audit = self._audit_slice(
                stop=lambda: self._now_ms() >= audit_until_ms)
        except Exception as e:                               # noqa: BLE001
            self._log("chain audit slice failed: %s: %s"
                      % (type(e).__name__, e))

        lease = _SlicedLease(
            self._spotlight, hard_deadline_ms - reserve, self._now_ms)
        try:
            report = self._brain.night(reasoner=reasoner, lease=lease)
        except Exception as e:                       # noqa: BLE001
            # A night that raises must not take the daemon with it. The eye
            # keeps watching and the socket keeps answering even if thinking
            # is broken — perception outranks reflection in the failure
            # hierarchy (product truth: 1 "it saw" beats 3 "it noticed").
            self.last_skip = "night raised %s: %s" % (type(e).__name__, e)
            self._log("night failed: %s" % self.last_skip)
            return None

        self.slices += 1
        self.last_report = report
        # RE-MARINATION, WHICH IS WHY RAW IS KEPT FOR EVER (F-RAW). Beliefs
        # written before a grammar existed are re-read with it. Runs before
        # the vitals so tonight's sayable-belief rate reflects tonight's
        # repair rather than yesterday's damage.
        try:
            from tracemem.nodes.marination.relabel import relabel_pass
            g0 = getattr(getattr(self._brain, "_hub", None), "w", None)
            g0 = getattr(g0, "graph_store", None)
            if g0 is not None and self._now_ms() < hard_deadline_ms:
                r = relabel_pass(g0, self._now_ms())
                if r["resaid"] or r["retired"]:
                    self._log("re-said %d belief(s) in English, retired %d "
                              "that cannot be said at all"
                              % (len(r["resaid"]), len(r["retired"])))
        except Exception as e:                               # noqa: BLE001
            self._log("relabel pass failed: %s: %s" % (type(e).__name__, e))

        try:
            from tracemem.nodes.health import vitals
            g = getattr(getattr(self._brain, "_hub", None), "w", None)
            g = getattr(g, "graph_store", None)
            if g is not None and self._now_ms() < hard_deadline_ms:
                self.last_vitals = vitals(g, self._now_ms())
        except Exception as e:                               # noqa: BLE001
            self._log("graph vitals failed: %s: %s" % (type(e).__name__, e))

        # WHAT THE WHOLE OWNER-THREAD SLICE ACTUALLY COST.  This measurement
        # belongs after relabel, vitals and the chain audit: those used to be
        # omitted, so production reported a 10-second slice while status was
        # blocked for 26 seconds. The audit now polls the same hard deadline.
        elapsed = max(0, self._now_ms() - slice_started_ms)
        self.last_slice_ms = elapsed
        self.worst_slice_ms = max(self.worst_slice_ms, elapsed)
        if elapsed > self._slice_ms * OVERRUN_FACTOR:
            self.overruns += 1
            self._log(
                "night slice OVERRAN its budget: %.1fs against a %.1fs slice "
                "(%.0fx). The loop that answers questions was held for that "
                "whole time — the night cannot be stopped between its poll "
                "points, only at them."
                % (elapsed / 1000.0, self._slice_ms / 1000.0,
                   elapsed / float(self._slice_ms or 1)))
        status = getattr(report, "status", "?")
        if status == "COMPLETE":
            self.completed += 1
            # Only a pass that RAN TO THE END may declare the world settled.
            # A RESUMABLE pass was cut off mid-slice and has unfinished work
            # whether or not anything new arrived.
            self._settled_at = mark_before
        else:
            self._settled_at = None

        # THE FORWARD-PROGRESS CHECK. Runs on the mark taken BEFORE the pass,
        # for the same reason the settle mark does: work created during a pass
        # must count as the world moving, not as this pass failing.
        progressed = self._forward_progress(report)
        if status == "COMPLETE" or progressed:
            self._stuck_mark = None
            self._stuck_streak = 0
            self._exhausted_at = None
        elif mark_before is not None and mark_before == self._stuck_mark:
            self._stuck_streak += 1
            if self._stuck_streak >= STUCK_SLICES:
                self._exhausted_at = mark_before
                self._log(
                    "night STUCK: %d consecutive slices derived nothing from "
                    "an unchanged world (attempted=%s accepted=0). Standing "
                    "down until something new arrives — this is a defect in "
                    "the pass, not a settled world."
                    % (self._stuck_streak, getattr(report, "attempted", 0)))
        else:
            # A different world than the last barren slice saw: start counting
            # again from this one rather than carrying a stale streak forward.
            # The old exhaustion is cleared HERE and not only in the gate —
            # `why_not` keys on the mark and so was already correct, but
            # `stats()` reads the field directly and would have gone on
            # reporting "stuck" about a world that no longer exists. A status
            # line that stays red after the fault clears is how the next
            # twelve-day outage gets ignored.
            self._exhausted_at = None
            self._stuck_mark = mark_before
            self._stuck_streak = 1
        for k in self.totals:
            v = getattr(report, k, None)
            if isinstance(v, int):
                self.totals[k] += v
            elif isinstance(v, list):
                self.totals[k] += len(v)
        if status != "COMPLETE" or self.totals["attempted"]:
            # SAY WHICH FIELD COUNTED AS PROGRESS, not just that something
            # did. The 2026-07-29 livelock was fixed by keying the stand-down
            # on forward progress; it came back on 2026-08-20 at 55 slices
            # because a pass can be barren where it matters (accepted=0) and
            # still report movement in a field that repeats every single
            # slice. A guard that cannot be watched cannot be trusted, and
            # this line is what makes the next recurrence visible in one
            # `./trace log` instead of another CPU-week.
            moved = {k: (len(v) if isinstance(v, (list, tuple, dict, set))
                         else v)
                     for k in self._PROGRESS_FIELDS
                     for v in (getattr(report, k, None),)
                     if v not in (None, 0, [], (), {}, set(), False)}
            self._log("night slice %d: status=%s attempted=%s accepted=%s "
                      "(expired=%s) progress=%s streak=%d mark=%s phases=%s"
                      % (self.slices, status,
                         getattr(report, "attempted", 0),
                         getattr(report, "accepted", 0),
                         lease.expired, moved or "NONE",
                         self._stuck_streak, mark_before,
                         _phase_ms(report)))
        return report

    def stats(self) -> dict:
        r = self.last_report
        return {
            "slices": self.slices,
            "completed": self.completed,
            # THE INTEGRITY SWEEP, VISIBLE. A check nobody can see the result
            # of is a check nobody trusts, and this is the one thing the
            # never-lie promise rests on.
            "chain_audit": dict(self.last_audit) if self.last_audit else None,
            # NUMBERS WITH DATES, NO INTERPRETATION. See nodes/health — the
            # sayable-belief rate is the one that would have caught a graph
            # of perfectly healthy shape whose every belief was a schema row.
            "vitals": dict(self.last_vitals) if self.last_vitals else None,
            "skipped_because": self.last_skip,
            "totals": dict(self.totals),
            "last_status": getattr(r, "status", None) if r else None,
            "hunger": ({
                "grammar": getattr(r, "hunger_grammar", ""),
                "examined": getattr(r, "hunger_examined", 0),
                "completed": getattr(r, "hunger_completed", 0),
                "model_calls": getattr(r, "hunger_model_calls", 0),
                "unresolved": getattr(r, "hunger_unresolved", 0),
            } if r else None),
            # Visible, because "the night is doing nothing" and "the night has
            # nothing left to do" look identical from outside and are opposite
            # facts — the first is a bug report, the second is success.
            "settled": self._settled_at is not None,
            # The third state, which for 12 days had no name and so could not
            # be seen: running constantly, finishing never, deriving nothing.
            "stuck": self._exhausted_at is not None,
            "stuck_streak": self._stuck_streak,
            # A slice budget nobody measured is a slice budget nobody has.
            "last_slice_ms": self.last_slice_ms,
            "worst_slice_ms": self.worst_slice_ms,
            "slice_budget_ms": self._slice_ms,
            "overruns": self.overruns,
        }
