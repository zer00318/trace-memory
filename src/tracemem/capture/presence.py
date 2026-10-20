"""OWNS: presence — is a human at this machine? (map: World / Focus sense /
Presence-idle; atoms: Idle seconds, Away witness).

Two atoms, one state machine, because they are two halves of one fact:
  Idle seconds — read the OS input-idle clock, flip present/away on a
                 declared threshold (capture_config.IDLE_AWAY_THRESHOLD_S).
  Away witness — pair away→return into ONE interval claim, so the binder
                 can refuse to run a moment across an empty chair.

THE EDGE THAT MATTERS, and it is not obvious: the moment you went away is
NOT the moment the threshold tripped. Idle time is a clock the OS has been
running since your last keystroke — when the sampler sees 300s of idle at
14:05, you actually left at 14:00. Backdating by the idle reading is the
whole difference between "you left at 14:00" (true) and "you left at
14:05" (a lie the size of the threshold, repeated every away period,
silently shifting every gap boundary in the store).

The return edge needs no such correction: idle collapses to ~0 the instant
a key is pressed, so the poll that sees it is within one interval of the
truth, and we cannot know better than that.

An UNKNOWN idle reading (no Quartz) is a hole, never "present" — the
system must not claim a person was here on the strength of a failed read.

THE SLEEP LIE, found by audit 2026-07-21 and fixed here at its birthplace:
CGEventSourceSecondsSinceLastEventType KEEPS COUNTING while the machine is
asleep. So the first poll after an overnight sleep reads ~8 hours of idle,
backdates to before the sleep, and emits "screen lit, nobody home for
28810s" — a durable claim asserting something that never happened (the
screen was off; nobody was home because the Mac was shut). It also
double-counts against the eyelids' closed-eyes claim for the same night.

Hence `eyes_open` and `awake_since_ms` on observe(). The rules:
  - eyes shut  → presence is UNKNOWN, not away. We cannot see, so we say
    nothing; the closed-eyes witness already owns that interval.
  - after wake → the backdated departure is FLOORED at the wake time. The
    idle clock's memory of the sleep is not evidence about a person.
An absence is only ours to claim while the eyes were open.

SPEC: 01 §6
ORACLE: tests/unit/test_presence.py — away@t1 + return@t2 yields one claim
        covering [t1, t2], with t1 backdated to the true departure; and a
        sleep produces NO away claim.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from tracemem.app.witness import WitnessKind, witness
from tracemem.capture.capture_config import IDLE_AWAY_THRESHOLD_S

PRESENT = "present"
AWAY = "away"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class PresenceEdge:
    t_ms: int
    state: str                  # PRESENT / AWAY / UNKNOWN
    previous: Optional[str]
    idle_s: Optional[float]


def presence_from_idle(idle_s: Optional[float],
                       threshold_s: float = IDLE_AWAY_THRESHOLD_S) -> str:
    """The pure rule. None in → UNKNOWN out; never a cheerful default."""
    if idle_s is None:
        return UNKNOWN
    return AWAY if idle_s >= threshold_s else PRESENT


class PresenceSense:
    """Edge-detector over the idle clock, with the backdating correction.

    Feed it `observe(idle_s, t_ms)` on every poll; it returns the edges (0
    or 1 per poll) and accumulates away intervals ready to be witnessed.
    Pure state machine — no clock, no Quartz, no I/O — so the correction
    arithmetic is testable to the millisecond.
    """

    def __init__(self, threshold_s: float = IDLE_AWAY_THRESHOLD_S) -> None:
        self.threshold_s = threshold_s
        self.state: str = UNKNOWN
        self._away_since_ms: Optional[int] = None
        self._pending_close_ms: Optional[int] = None
        self.edges: List[PresenceEdge] = []

    def observe(self, idle_s: Optional[float], t_ms: int,
                eyes_open: bool = True,
                awake_since_ms: Optional[int] = None) -> Optional[PresenceEdge]:
        """One poll. `eyes_open` False (locked/asleep) makes presence UNKNOWN
        rather than away — the closed-eyes witness owns that interval, and
        claiming "screen lit, nobody home" about a sleeping Mac is a lie.
        `awake_since_ms` floors the backdating so the idle clock's memory of
        a sleep cannot be mistaken for evidence about a person."""
        new = (UNKNOWN if not eyes_open
               else presence_from_idle(idle_s, self.threshold_s))
        if new == self.state:
            return None                       # stillness emits nothing
        prev, self.state = self.state, new
        if new == AWAY and idle_s is not None:
            # Backdate to the true departure: t_ms minus the idle already run,
            # but never past the moment the eyes reopened.
            departed = t_ms - int(idle_s * 1000)
            if awake_since_ms is not None:
                departed = max(departed, awake_since_ms)
            self._away_since_ms = departed
        elif new == UNKNOWN and not eyes_open:
            # RE-AUDIT FIX 2026-07-21: the eyes closing on an OPEN absence
            # used to drop it silently — trading a false claim for a silent
            # hole, which is the same failure wearing better manners. We do
            # know how long that absence ran: from the departure until the
            # moment the eyes shut. Close it there; the closed-eyes witness
            # owns everything after.
            self._pending_close_ms = t_ms
        elif new == UNKNOWN:
            # The idle sense went dark (no Quartz) — a different case. Here
            # we genuinely cannot bound the end, so drop rather than invent.
            self._away_since_ms = None
        edge = PresenceEdge(t_ms=t_ms, state=new, previous=prev, idle_s=idle_s)
        self.edges.append(edge)
        return edge

    # -- the Away witness atom ---------------------------------------------
    def close_away_interval(self, return_t_ms: int) -> Optional[object]:
        """Called on the away→present edge: one claim for the whole absence.

        Returns None when there is no open away interval to close — closing
        nothing must be a no-op, not a zero-length claim littering the log.
        """
        if self._away_since_ms is None:
            return None
        t1, self._away_since_ms = self._away_since_ms, None
        if return_t_ms <= t1:
            return None                       # clock jumped; witness nothing
        return witness(
            WitnessKind.AWAY, t_ms=t1, span_t2_ms=return_t_ms,
            detail=f"screen lit, nobody home for "
                   f"{(return_t_ms - t1) / 1000:.0f}s",
            structured_extra={"threshold_s": self.threshold_s})

    def observe_and_witness(self, idle_s: Optional[float], t_ms: int,
                            eyes_open: bool = True,
                            awake_since_ms: Optional[int] = None):
        """The sampler's one call: returns (edge, claim-or-None)."""
        edge = self.observe(idle_s, t_ms, eyes_open=eyes_open,
                            awake_since_ms=awake_since_ms)
        claim = None
        if edge is not None and edge.state == PRESENT and edge.previous == AWAY:
            claim = self.close_away_interval(t_ms)
        elif self._pending_close_ms is not None:
            # The eyes shut while they were away: close the absence at the
            # moment we stopped being able to see it.
            claim = self.close_away_interval(self._pending_close_ms)
            self._pending_close_ms = None
        return edge, claim


def _spans_of(claims, kinds) -> List[tuple]:
    out = []
    for c in claims:
        s = getattr(c, "structured", None) or {}
        if s.get("witness") in kinds and "t2_ms" in s:
            out.append((c.coord.t_ms, s["t2_ms"]))
    return sorted(out)


def away_spans(claims) -> List[tuple]:
    """(t1, t2) for every AWAY witness — the empty-chair intervals only."""
    return _spans_of(claims, {WitnessKind.AWAY.value})


def attention_gaps(claims) -> List[tuple]:
    """MERGED (t1, t2) intervals a moment may not span, from both causes.

    Merging is not tidiness — it is correctness. A lid closed at lunch
    produces an away witness (the idle clock) AND a closed-eyes witness
    (the lid), covering the same wall-clock hour. Returning both would make
    the binder count one absence twice, and the double looks entirely
    reasonable in a log. Overlapping and adjoining spans collapse into one.
    """
    # CLOCK_JUMP is included (re-audit 2026-07-21): its span is zero-length
    # because the blind interval's true length is unknown, but a zero-length
    # gap still yields a CUT — and a moment must not run straight through a
    # point where the eyes were shut for an unknown time. Labelling the hole
    # without cutting at it left the binder spanning it anyway.
    spans = _spans_of(claims, {WitnessKind.AWAY.value,
                               WitnessKind.CLOSED_EYES.value,
                               WitnessKind.CLOCK_JUMP.value})
    merged: List[tuple] = []
    for t1, t2 in spans:
        if merged and t1 <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], t2))
        else:
            merged.append((t1, t2))
    return merged
