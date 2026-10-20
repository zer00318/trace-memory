"""OWNS: the common arena interface every contender implements.

specs/node_tournament.md §"What every contender must provide":
ingest(claims) incremental / marinate(budget) idempotent /
supply(question) -> cited evidence package / storage report.
Honesty invariant: every EvidenceBlock carries claim ids that exist in
what was ingested — the arena machine-checks this, so the base class
enforces it at construction time rather than trusting engines.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Protocol, Sequence, Tuple

from tracemem.domain.claim import Claim

_WORD = re.compile(r"[A-Za-z0-9_@#'\.]+")
_STOP = frozenset(
    "the a an is are was were be been being to of in on at for with and or "
    "but not no this that these those it its i you he she they we my your "
    "his her their our as by from into over under about after before".split()
)


def cues(text: str) -> List[str]:
    """Deterministic cue tokens — shared by all contenders so no engine
    wins on a smarter tokenizer (the designs fight, not the lexers)."""
    out = []
    for w in _WORD.findall(text):
        lw = w.lower().strip(".'")
        if len(lw) >= 2 and lw not in _STOP:
            out.append(lw)
    return out


@dataclass(frozen=True)
class EvidenceBlock:
    text: str                       # what memory offers the mouth
    claim_ids: Tuple[str, ...]      # receipts — NEVER empty
    source: str                     # engine-specific label (cell/answer/edge id)
    score: float = 0.0

    def __post_init__(self) -> None:
        if not self.claim_ids:
            raise ValueError("EvidenceBlock without citations — never-lie forbids it")


@dataclass
class EvidencePackage:
    question: str
    blocks: List[EvidenceBlock] = field(default_factory=list)
    route_to_store: Tuple[str, ...] = ()   # question classes this engine honestly
                                           # declines (aggregate/negative) — arena
                                           # addendum: routing is legal, guessing is not.
    def cited_ids(self) -> set:
        return {cid for b in self.blocks for cid in b.claim_ids}


@dataclass(frozen=True)
class StorageReport:
    bytes_used: int
    object_count: int


class Contender(Protocol):
    name: str
    def ingest(self, claims: Sequence[Claim]) -> None: ...
    def marinate(self, budget: int) -> int: ...
    def supply(self, question: str, k: int = 8) -> EvidencePackage: ...
    def storage(self) -> StorageReport: ...


class BaseEngine:
    """Shared plumbing: claim registry + citation validation + weight."""

    def __init__(self) -> None:
        self._claims: Dict[str, Claim] = {}

    def _register(self, claims: Iterable[Claim]) -> List[Claim]:
        fresh = []
        for c in claims:
            if c.id not in self._claims:
                self._claims[c.id] = c
                fresh.append(c)
        return fresh

    def _validate(self, pkg: EvidencePackage) -> EvidencePackage:
        for b in pkg.blocks:
            for cid in b.claim_ids:
                if cid not in self._claims:
                    raise ValueError(
                        f"{getattr(self, 'name', '?')} cited unknown claim {cid!r} — "
                        "fabricated receipt")
        return pkg

    def _approx_bytes(self, *objs) -> int:
        return sum(sys.getsizeof(repr(o)) for o in objs)
