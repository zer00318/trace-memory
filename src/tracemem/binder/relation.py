"""OWNS: the relation record and its revision trail (map: Binder / Relation
confidence; RULING round four).

The ruling, restated: every relation carries live confidence that new
evidence updates; the current best is what nodes see; reversals leave a
VISIBLE TRAIL. History survives for the honest witness.

WHY A TRAIL AND NOT AN OVERWRITE. A relation like "the headphones are worn
(not just on the desk)" is a belief the system will get wrong sometimes.
Overwriting it on new evidence produces a system that has always been
right — which is the exact texture of a liar. Worse, it is unfalsifiable:
with no trail, "why do you think that?" has no answer, and a wrong belief
cannot be traced to the evidence that caused it. Product truth #4 (never
lies) is not only about the final answer; it is about being able to show
the road.

THE FLIP RULE: confidence updates in place, silently, WHILE the belief
holds. Crossing 0.5 — the point where the system would tell you something
different — appends a trail entry. Cheap where nothing changed, loud
exactly where a reader's understanding would change. Same no-silent-flip
guard that beliefs carry elsewhere.

Readers: nodes and the Supplier see `current` only. The trail is queryable
for "why do you think that", and is never consulted for the answer itself.

SPEC: 03 (binder), ruling round four
ORACLE: tests/unit/test_relation.py — contradictory evidence flips a
        relation; the old belief survives; trail length grows.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, List, Optional, Sequence, Tuple

# Below this, the system believes NOT; above, it believes SO. Crossing it is
# what makes a trail entry, because it is what changes the answer a person
# would be given. UNCALIBRATED — 0.5 is the definition of "more likely than
# not", not a measured operating point.
BELIEF_LINE = 0.5

# The band where a relation is worth spending a look to resolve (the Binder
# bids atom reads this). UNCALIBRATED.
AMBIGUOUS_BAND = (0.35, 0.65)


@dataclass(frozen=True)
class Evidence:
    t_ms: int
    source: str                 # which helper/organ supplied it
    delta: float                # signed push on confidence
    detail: str = ""


@dataclass(frozen=True)
class Revision:
    t_ms: int
    was: float                  # confidence before
    now: float                  # confidence after
    was_believed: bool
    now_believed: bool
    cause: Evidence


@dataclass(frozen=True)
class Relation:
    subject: str
    predicate: str
    object: str
    confidence: float
    evidence: Tuple[Evidence, ...] = ()
    trail: Tuple[Revision, ...] = ()

    # -- what readers see --------------------------------------------------
    @property
    def believed(self) -> bool:
        return self.confidence > BELIEF_LINE

    @property
    def ambiguous(self) -> bool:
        lo, hi = AMBIGUOUS_BAND
        return lo <= self.confidence <= hi

    @property
    def key(self) -> Tuple[str, str, str]:
        return (self.subject, self.predicate, self.object)

    def __str__(self) -> str:
        return (f"{self.subject} {self.predicate} {self.object} "
                f"({self.confidence:.2f}{'' if self.believed else ', doubted'})")


def _clamp(v: float) -> float:
    return min(1.0, max(0.0, v))


def observe(relation: Relation, ev: Evidence) -> Relation:
    """Fold one piece of evidence in. Returns a NEW relation — the old
    object is never mutated, so a caller holding a reference to yesterday's
    belief still holds yesterday's belief.

    Evidence always accumulates. The TRAIL grows only when the belief
    itself flips, which keeps the trail readable: it is a list of times the
    system changed its mind, not a log of every observation.
    """
    was = relation.confidence
    now = _clamp(was + ev.delta)
    was_believed = was > BELIEF_LINE
    now_believed = now > BELIEF_LINE

    trail = relation.trail
    if was_believed != now_believed:
        trail = trail + (Revision(t_ms=ev.t_ms, was=was, now=now,
                                  was_believed=was_believed,
                                  now_believed=now_believed, cause=ev),)
    return replace(relation, confidence=now,
                   evidence=relation.evidence + (ev,), trail=trail)


def why(relation: Relation) -> List[str]:
    """The answer to "why do you think that" — the trail in plain words."""
    lines = []
    for r in relation.trail:
        verb = "came to believe" if r.now_believed else "stopped believing"
        lines.append(f"t={r.t_ms}: {verb} it ({r.was:.2f} → {r.now:.2f}) "
                     f"on {r.cause.source}"
                     + (f": {r.cause.detail}" if r.cause.detail else ""))
    if not lines:
        lines.append(f"never changed its mind (confidence {relation.confidence:.2f}"
                     f" from {len(relation.evidence)} observations)")
    return lines


class RelationSet:
    """The binder's live relations, keyed by (subject, predicate, object)."""

    def __init__(self) -> None:
        self._rel = {}

    def observe(self, subject: str, predicate: str, object: str,
                ev: Evidence, prior: float = BELIEF_LINE) -> Relation:
        key = (subject, predicate, object)
        rel = self._rel.get(key) or Relation(subject, predicate, object, prior)
        rel = observe(rel, ev)
        self._rel[key] = rel
        return rel

    def current(self, subject: str, predicate: str,
                object: str) -> Optional[Relation]:
        return self._rel.get((subject, predicate, object))

    def believed(self) -> List[Relation]:
        """What nodes and the Supplier read: current best only."""
        return [r for r in self._rel.values() if r.believed]

    def ambiguous(self) -> List[Relation]:
        """What the Binder would spend a look on (the bidding atom)."""
        return [r for r in self._rel.values() if r.ambiguous]

    def all(self) -> List[Relation]:
        return list(self._rel.values())
