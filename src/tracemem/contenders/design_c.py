"""Design C — MEANING IN THE WIRING (specs/node_designs/design_C_*.md).

Near-empty anchors; ALL meaning in weighted CITED edges. Hebbian day
(pure arithmetic, no LLM in the write path), replay night (shortcuts +
decay), answers by spreading activation — the lit subgraph WITH its
citations is the pack. Aggregate/negative questions are honestly routed
to store tools: activation cannot count.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Set, Tuple

from tracemem.contenders.base import BaseEngine, EvidenceBlock, EvidencePackage, StorageReport, cues
from tracemem.domain.claim import Claim

MAX_PAIRS_PER_CLAIM = 15      # hub-explosion cap (kill condition 2's first defense)
HOPS = 2
DECAY_PER_HOP = 0.5
STRENGTHEN = 1.0
REPLAY_DECAY = 0.9            # unfired edges cool at night; floor prunes
PRUNE_FLOOR = 0.3
AGGREGATE_MARKERS = ("how many", "how often", "count", "did i ever", "never", "times did")


@dataclass
class Edge:
    weight: float = 0.0
    claim_ids: List[str] = field(default_factory=list)   # every increment cited
    fire_count: int = 0
    fired_this_day: bool = False


class WiringMemory(BaseEngine):
    name = "design_c"

    def __init__(self) -> None:
        super().__init__()
        self._anchors: Dict[str, List[str]] = {}            # label -> claim ids
        self._edges: Dict[Tuple[str, str], Edge] = {}       # (a<b) -> Edge

    # -- interface ---------------------------------------------------------
    def ingest(self, claims: Sequence[Claim]) -> None:
        for c in self._register(claims):
            labels = sorted(set(cues(c.text)))
            for lab in labels:
                self._anchors.setdefault(lab, []).append(c.id)
            pairs = [(a, b) for i, a in enumerate(labels) for b in labels[i + 1:]]
            for a, b in pairs[:MAX_PAIRS_PER_CLAIM]:        # Hebbian day, capped
                e = self._edges.setdefault((a, b), Edge())
                e.weight += STRENGTHEN
                e.claim_ids.append(c.id)
                e.fire_count += 1
                e.fired_this_day = True

    def marinate(self, budget: int) -> int:
        """Replay night: decay unfired edges, prune the cold, reset the day.
        Budget = edges processed; idempotent within one night (fired flags gate)."""
        done = 0
        for key, e in list(self._edges.items()):
            if done >= budget:
                break
            if not e.fired_this_day:
                e.weight *= REPLAY_DECAY
                if e.weight < PRUNE_FLOOR:
                    del self._edges[key]          # meta dies; citations kept claims safe
            e.fired_this_day = False
            done += 1
        return done

    def supply(self, question: str, k: int = 8) -> EvidencePackage:
        ql = question.lower()
        pkg = EvidencePackage(question=question)
        if any(m in ql for m in AGGREGATE_MARKERS):
            pkg.route_to_store = ("aggregate/negative — activation cannot count honestly",)
            return pkg
        energy: Dict[str, float] = {c: 1.0 for c in cues(question) if c in self._anchors}
        lit: Dict[Tuple[str, str], float] = {}
        frontier = dict(energy)
        for _ in range(HOPS):
            nxt: Dict[str, float] = {}
            for (a, b), e in self._edges.items():
                for src, dst in ((a, b), (b, a)):
                    if src in frontier:
                        flow = frontier[src] * e.weight * DECAY_PER_HOP
                        if flow > 0:
                            lit[(a, b)] = lit.get((a, b), 0.0) + flow
                            nxt[dst] = nxt.get(dst, 0.0) + flow
                            energy[dst] = energy.get(dst, 0.0) + flow
            frontier = nxt
        for (a, b), bright in sorted(lit.items(), key=lambda t: -t[1])[:k]:
            e = self._edges[(a, b)]
            top = list(dict.fromkeys(e.claim_ids))[-3:]     # nuance arrives verbatim
            texts = " ; ".join(self._claims[i].text for i in top if i in self._claims)
            pkg.blocks.append(EvidenceBlock(
                text=f"[{a}—{b} w={e.weight:.1f}] {texts}",
                claim_ids=tuple(top), source=f"edge:{a}—{b}", score=bright))
        return self._validate(pkg)      # strict: no uncited light leaves this engine

    def storage(self) -> StorageReport:
        return StorageReport(self._approx_bytes(self._anchors, self._edges),
                             len(self._anchors) + len(self._edges))
