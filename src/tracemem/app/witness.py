"""OWNS: the witness vocabulary — every gap in the product's honesty,
as a closed enum of claim kinds (08 "Honest holes").

LAW: no organ builds a witness by hand. Import WitnessKind + witness()
from here or your gap is a silent hole — the grep oracle enforces it
(no ad-hoc '"witness"' strings outside this module).

A witness IS a claim: memory-scoped (claim_match A4 — witnesses are not
frame extractions), VERBATIM grade (the system witnessed its own state),
structured carrying the machine-readable fields the coverage audit and
the Negative question class consume.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from tracemem.domain import (
    CaptureCoord, Claim, Confidence, CoordinateStack, Grade, Modality,
    Provenance, SpanCoord, WorldCoord,
)
from tracemem.domain.grading import grade_for


class WitnessKind(Enum):
    START = "start"                      # capture/daemon came alive
    CLEAN_STOP = "clean-stop"            # deliberate shutdown
    KILL_DETECTED = "kill-detected"      # found on next boot: no clean stop before gap
    GAP = "gap"                          # boot-time hole: last claim long ago
    FOUNDER_PAUSE = "founder-pause"      # the sacred OFF — YOUR choice, recorded
    FOUNDER_RESUME = "founder-resume"
    PERMISSION_LOSS = "permission-loss"
    PERMISSION_REGAIN = "permission-regain"
    SLEEP = "sleep"
    WAKE = "wake"
    # SOMEBODY ELSE IS USING THIS MAC. Fast user switching leaves every
    # logged-in session running, so the daemon keeps its process and its
    # capture stream while a DIFFERENT PERSON is at the keyboard. Every other
    # wall in this product is the founder choosing not to record something of
    # his own; this one is about somebody else's screen, and it is the only
    # hole where the person harmed never agreed to any of it.
    OTHER_USER = "other-user"            # this session is not on the console
    LOCKED = "locked"                    # screen locked/screensaver: eyes closed
    UNLOCKED = "unlocked"
    CLOSED_EYES = "closed-eyes"          # the PAIRED interval: could not see t1..t2
    AWAY = "away"                        # screen lit, nobody home (paired interval)
    CRASH = "crash"
    ORGAN_RESTART = "organ-restart"      # supervisor revived a dead organ
    ORGAN_BENCHED = "organ-benched"      # repeatedly dying organ set aside
    # An organ that never got off the ground, as opposed to one that ran and
    # died. Its own kind rather than a RESTART with an explanatory string,
    # because a coverage audit asking "was the eye ever open in this window?"
    # must be able to tell "it started and crashed" from "it was never able
    # to start at all" — the second means the machine could not see for
    # structural reasons (asleep display, permission withdrawn), and a
    # distinction that lives only in free text is one no query can make.
    # Added 2026-07-29 after a sleeping display took the whole daemon down.
    ORGAN_START_FAILED = "organ-start-failed"
    OVERWHELM = "overwhelm"              # backpressure shed (what, how much)
    HELPER_FAILURE = "helper-failure"      # sandbox/contract/timeout failed
    HELPER_DEFERRED = "helper-deferred"    # heavy turn unavailable; replay owed
    DOWNSHIFT = "downshift"              # thermal/battery governor slowed the eye
    RECOVERY = "recovery"                # store restored from snapshot
    EXCLUDED = "excluded"                # blocklist/secure-input interval (coarse, no content)
    CLOCK_JUMP = "clock-jump"            # wall/monotonic divergence
    # Resource pressure (added 2026-07-22 by the disk/memory pressure atoms,
    # 11_machine). These are NOT variants of DOWNSHIFT: a thermal or battery
    # downshift means the eye ran SLOWER, while pressure means a rung was
    # switched OFF and a specific capability stopped existing for a while.
    # Coverage math and any absence answer have to tell those apart, so they
    # get their own kinds rather than a shared one with a detail string —
    # a distinction that lives only in free text is a distinction no query
    # can make.
    DISK_PRESSURE = "disk-pressure"      # free space crossed a declared threshold
    MEMORY_PRESSURE = "memory-pressure"  # the OS said warn/critical
    # Added 2026-07-22 by the self-metrics atom (08_running). Justified as
    # its own kind rather than folded into DOWNSHIFT: downshift records what
    # the system DID about a cost, this records the cost itself. The user's
    # real question is "what did TRACE cost me on Tuesday", and an answer
    # assembled from responses cannot see an overrun that was never
    # responded to — which is exactly the case worth catching.
    OVERBUDGET = "overbudget"            # sustained self-cost above the declared budget


def witness(kind: WitnessKind, t_ms: int, detail: str = "",
            device_id: str = "mac-1", span_t2_ms: Optional[int] = None,
            structured_extra: Optional[dict] = None) -> Claim:
    """Build the one legal shape of witness claim. `span_t2_ms` marks an
    interval witness (gap/overwhelm/excluded): the hole it brackets."""
    structured = {"witness": kind.value}
    if span_t2_ms is not None:
        structured["t2_ms"] = span_t2_ms
    if structured_extra:
        structured.update(structured_extra)
    text = f"[witness:{kind.value}] {detail}".strip()
    coord = CoordinateStack(
        world=WorldCoord(place_key="system"),
        capture=CaptureCoord(frame_id=f"witness-{kind.value}-{t_ms}",
                             device_id=device_id, app="trace"),
        span=SpanCoord(bbox=(0.0, 0.0, 0.0, 0.0)),
        t_ms=t_ms,
    )
    return Claim(
        text=text, coord=coord,
        provenance=Provenance(perceiver="witness", modality=Modality.SCREEN),
        # THE table, not a literal (Grading rules per source atom): a witness
        # is the system observing ITSELF, which is maximally faithful — but
        # that judgement lives in domain/grading.py with all the others.
        grade=grade_for("witness"),
        confidence=Confidence(1.0),
        structured=structured,
        bidder="system:witness", bid_id=f"witness:{kind.value}:{t_ms}",
        bid_predicted_value=0.0, actual_cost=0.0,
        scope="memory",                  # A4: never precision-graded as a frame read
    )
