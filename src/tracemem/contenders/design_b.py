"""Design B — GROWING FUTURE ANSWERS (specs/node_designs/design_B_*.md).

The primitive is the ANSWER: a materialized view (stored evidence query)
+ a rendered draft + staleness triggers + a prediction ledger. The night
predicts questions and pre-answers them; ask-time is lookup-then-refine.
Budget discipline IS the design: proposals queue, marinate(budget)
drafts the top bids only, the answer set is capped.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Set

from tracemem.contenders.base import BaseEngine, EvidenceBlock, EvidencePackage, StorageReport, cues
from tracemem.domain.claim import Claim

MIN_RECURRENCE = 3       # an entity must recur before it earns a predicted question
MAX_ANSWERS = 200        # the FAQ is capped — infinite drafts is the named death
DRAFT_BULLETS = 6


@dataclass
class FutureAnswer:
    id: str
    question_shape: str              # canonical phrasing
    evidence_cues: Set[str]          # the stored search (the view's query)
    draft: List[tuple] = field(default_factory=list)   # (sentence, claim_id)
    stale: bool = True               # born stale; marination renders
    hits: int = 0
    misses: int = 0                  # ledger: prediction-error made bookkeeping
    refreshed_at: int = 0


class FutureAnswerMemory(BaseEngine):
    name = "design_b"

    def __init__(self) -> None:
        super().__init__()
        self._answers: Dict[str, FutureAnswer] = {}
        self._subs: Dict[str, Set[str]] = {}   # cue -> answer ids (staleness triggers)
        self._entity_count: Dict[str, int] = {}
        self._clock = 0

    # -- interface ---------------------------------------------------------
    def ingest(self, claims: Sequence[Claim]) -> None:
        for c in self._register(claims):
            self._clock += 1
            seen = set(cues(c.text))
            for cue in seen:
                self._entity_count[cue] = self._entity_count.get(cue, 0) + 1
                for aid in self._subs.get(cue, ()):        # invalidation, not rot
                    self._answers[aid].stale = True
            self._propose(seen)

    def marinate(self, budget: int) -> int:
        """Re-run stale views' queries and re-render drafts, best bids first.
        Bid price = expected-ask (hits+recurrence) minus demonstrated misses."""
        stale = [a for a in self._answers.values() if a.stale]
        stale.sort(key=lambda a: -(a.hits * 3 + sum(
            self._entity_count.get(c, 0) for c in a.evidence_cues) - a.misses))
        done = 0
        for a in stale[:budget]:
            self._render(a)
            done += 1
        return done

    def supply(self, question: str, k: int = 8) -> EvidencePackage:
        q = set(cues(question))
        pkg = EvidencePackage(question=question)
        scored = sorted(((len(q & a.evidence_cues | q & set(cues(a.question_shape))), a)
                         for a in self._answers.values()), key=lambda t: -t[0])
        served = 0
        for score, a in scored:
            if score == 0 or served >= k:
                break
            if a.stale or not a.draft:
                a.misses += 1          # wanted but not ready — tonight's shame
                continue
            a.hits += 1                # prediction confirmed
            for sent, cid in a.draft:
                pkg.blocks.append(EvidenceBlock(
                    text=sent, claim_ids=(cid,), source=f"answer:{a.id}", score=float(score)))
            served += 1
        if served == 0:                # long tail: honest raw-claim fallback,
            for c in self._claims.values():   # and the miss becomes tonight's birth
                if q & set(cues(c.text)) and len(pkg.blocks) < k:
                    pkg.blocks.append(EvidenceBlock(
                        text=c.text, claim_ids=(c.id,), source="claim", score=0.1))
            self._propose(q, force=True)
        return self._validate(pkg)

    def storage(self) -> StorageReport:
        return StorageReport(self._approx_bytes(self._answers, self._subs),
                             len(self._answers))

    # -- mechanics ---------------------------------------------------------
    def _propose(self, seen: Set[str], force: bool = False) -> None:
        if len(self._answers) >= MAX_ANSWERS:
            return                     # the cap IS the discipline
        for cue in seen:
            aid = f"about:{cue}"
            if aid in self._answers:
                continue
            if force or self._entity_count.get(cue, 0) >= MIN_RECURRENCE:
                a = FutureAnswer(id=aid, question_shape=f"what do I know about {cue}?",
                                 evidence_cues={cue})
                self._answers[aid] = a
                self._subs.setdefault(cue, set()).add(aid)

    def _render(self, a: FutureAnswer) -> None:
        hits = [c for c in self._claims.values() if a.evidence_cues & set(cues(c.text))]
        a.draft = [(c.text, c.id) for c in hits[-DRAFT_BULLETS:]]
        a.stale = False
        a.refreshed_at = self._clock
