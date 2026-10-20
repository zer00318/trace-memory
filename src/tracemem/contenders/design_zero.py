"""Design 0 adapter — TODAY'S ENGINE behind the arena contract.

Wraps the real brain (hub: binder episodes -> birth -> graph -> supplier)
as a Contender. The mouth is NEVER invoked: supply() returns the
supplier's pack (pre-mouth), exactly where the tournament draws the line.

Honesty notes, written before anyone asks:
- marinate() here refreshes the vector index only. Design 0's full night
  (run_night) spends reasoner tokens; comparing that to A/B/C's
  arithmetic marination under one 'budget' int would be a fake fairness.
  The real arena runs each contender's OWN night under the same
  wall-clock/token budget — that wiring is the arena's job, not hidden here.
- Blocks whose sources don't resolve to ingested claim ids are DROPPED
  and counted in .uncited_dropped rather than served as fake receipts
  (node attributes cite claim ids on the real birth path; anything else
  must not reach the mouth).
"""
from __future__ import annotations

from typing import Sequence

from tracemem.app.wiring import build
from tracemem.contenders.base import BaseEngine, EvidenceBlock, EvidencePackage, StorageReport
from tracemem.domain.claim import Claim
from tracemem.supplier.cue import parse_cue
from tracemem.supplier.supplier import supply as d0_supply

SCENE_GAP_MS = 5 * 60 * 1000
SUPPLY_BUDGET_TOKENS = 1500


class DesignZeroAdapter(BaseEngine):
    name = "design_0"

    def __init__(self, embedder) -> None:
        super().__init__()
        # reasoner: wiring refuses None (a silent default would poison answers)
        # but the arena NEVER asks/nights through the hub — a sentinel that
        # explodes on any attribute access keeps that promise structural.
        class _NeverCalled:
            def __getattr__(self, name):
                raise AssertionError("mouth invoked inside the arena — forbidden")
        self._hub = build({"embedder": embedder, "reasoner": _NeverCalled()})
        self._last_t = None
        self.uncited_dropped = 0

    # -- interface ---------------------------------------------------------
    def ingest(self, claims: Sequence[Claim]) -> None:
        fresh = self._register(claims)
        for c in fresh:
            t = c.coord.t_ms
            if self._last_t is None or t - self._last_t > SCENE_GAP_MS:
                self._hub.post_scene_change(t)
            self._last_t = t
        if fresh:
            self._hub.post_claims(fresh)

    def marinate(self, budget: int) -> int:
        self._hub.w.vector_index.rebuild_from(self._hub.w.graph_store)
        return 1

    def supply(self, question: str, k: int = 8) -> EvidencePackage:
        ctx = d0_supply(parse_cue(question), self._hub.w.graph_store,
                        self._hub.w.claim_log, self._hub.w.vector_index,
                        budget_tokens=SUPPLY_BUDGET_TOKENS)
        pkg = EvidencePackage(question=question)
        for b in ctx.blocks[: k * 2]:
            ids = tuple(i for i in b.source_ids if i in self._claims)
            if not ids:
                self.uncited_dropped += 1     # never a fake receipt
                continue
            pkg.blocks.append(EvidenceBlock(
                text=b.text, claim_ids=ids, source=f"d0:{b.kind}", score=0.0))
        return self._validate(pkg)

    def storage(self) -> StorageReport:
        g = self._hub.w.graph_store
        edges = getattr(g, "edges", {})
        return StorageReport(self._approx_bytes(g.nodes, edges),
                             len(g.nodes) + (len(edges) if hasattr(edges, "__len__") else 0))
