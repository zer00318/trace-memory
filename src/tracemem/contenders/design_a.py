"""Design A — THE UNIT OF UNDERSTANDING (specs/node_designs/design_A_*.md).

One primitive for any shape of knowing. Birth is COMPRESSION-GATED: a
cell exists only if it says more with less than its raw claims do.
Touch = reconsolidation with a trail. Shapes are emergent labels.
No LLM in this build: gists/bodies are deterministic extractive
compositions; the mouth-assisted rewrite is a night hook the arena may
enable later — the DESIGN's structure is what competes here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

from tracemem.contenders.base import BaseEngine, EvidenceBlock, EvidencePackage, StorageReport, cues
from tracemem.domain.claim import Claim

MIN_SUPPORT = 3          # a cue seen in fewer claims stays a claim, never a cell
WORTH_FLOOR = 1.2        # compression gate: covered*avg_len/body_len must beat this
BODY_BULLETS = 5         # extractive body size — the compression denominator


@dataclass
class Understanding:
    id: str
    shape: str                       # emergent label — NEVER an enum
    gist: str
    body: List[Tuple[str, str]]      # (bullet text, claim_id)
    claim_ids: List[str]
    worth: float
    confidence: float = 0.5
    hunger: str = ""
    touched: int = 0
    trail: List[str] = field(default_factory=list)   # old gists, never deleted


class UnderstandingMemory(BaseEngine):
    name = "design_a"

    def __init__(self) -> None:
        super().__init__()
        self._cells: Dict[str, Understanding] = {}
        self._cue_index: Dict[str, List[str]] = {}     # cue -> claim ids (arrival order)

    # -- interface ---------------------------------------------------------
    def ingest(self, claims: Sequence[Claim]) -> None:
        for c in self._register(claims):
            for cue in set(cues(c.text)):
                self._cue_index.setdefault(cue, []).append(c.id)
        self._propose_births()

    def marinate(self, budget: int) -> int:
        """Reconsolidate the most-touched cells; kill worth-decayed ones.
        Deterministic and idempotent: same state -> same bodies -> no trail growth."""
        done = 0
        for cell in sorted(self._cells.values(), key=lambda u: -u.touched)[:budget]:
            old_gist = cell.gist
            self._recompose(cell)
            if cell.gist != old_gist:
                cell.trail.append(old_gist)
            done += 1
        for cid in [i for i, u in self._cells.items() if u.worth < WORTH_FLOOR]:
            del self._cells[cid]      # meta dies; claims are eternal
        return done

    def supply(self, question: str, k: int = 8) -> EvidencePackage:
        q = set(cues(question))
        pkg = EvidencePackage(question=question)
        scored = []
        for cell in self._cells.values():
            overlap = q & self._cell_cues(cell)
            if overlap:
                scored.append((len(overlap) * cell.worth, cell))
        for score, cell in sorted(scored, key=lambda t: -t[0])[:k]:
            cell.touched += 1         # touch is real: reconsolidation will visit
            pkg.blocks.append(EvidenceBlock(
                text=cell.gist + " | " + " ; ".join(t for t, _ in cell.body),
                claim_ids=tuple(cell.claim_ids), source=f"cell:{cell.id}", score=score))
        # claims second — understandings first, raw evidence as backstop
        for c in self._claims.values():
            if q & set(cues(c.text)) and len(pkg.blocks) < k * 2:
                pkg.blocks.append(EvidenceBlock(
                    text=c.text, claim_ids=(c.id,), source="claim", score=0.1))
        return self._validate(pkg)

    def storage(self) -> StorageReport:
        return StorageReport(self._approx_bytes(self._cells, self._cue_index),
                             len(self._cells))

    # -- mechanics ---------------------------------------------------------
    def _propose_births(self) -> None:
        for cue, ids in self._cue_index.items():
            if len(ids) < MIN_SUPPORT or cue in self._cells:
                continue
            cell = Understanding(id=cue, shape="emergent:" + cue, gist="", body=[],
                                 claim_ids=list(dict.fromkeys(ids)), worth=0.0)
            self._recompose(cell)
            if cell.worth >= WORTH_FLOOR:              # the compression gate
                self._cells[cue] = cell
            # else: no cell — the claims simply remain claims

    def _recompose(self, cell: Understanding) -> None:
        cell.claim_ids = list(dict.fromkeys(self._cue_index.get(cell.id, cell.claim_ids)))
        texts = [self._claims[i].text for i in cell.claim_ids if i in self._claims]
        cell.body = [(t, i) for t, i in
                     zip(texts[-BODY_BULLETS:], cell.claim_ids[-BODY_BULLETS:])]
        cell.gist = (f"{cell.id}: figures in {len(cell.claim_ids)} claims; "
                     f"latest: {texts[-1][:80]}" if texts else cell.id)
        covered = sum(len(t) for t in texts)
        body_len = len(cell.gist) + sum(len(t) for t, _ in cell.body)
        cell.worth = covered / body_len if body_len else 0.0

    def _cell_cues(self, cell: Understanding) -> set:
        out = {cell.id}
        for t, _ in cell.body:
            out |= set(cues(t))
        return out
