"""OWNS: the newness check — is this claim actually new, BY MEANING.

The novelty ledger's pixel tier says "these pixels were served before";
this is the semantic tier: a paraphrase of something already said stays
silent; genuinely new nuance passes. Embedder injected (MiniLM in anger,
HashEmbedder in tests) — CPU-pinned by construction, like all retrieval.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

SIM_BAR = 0.90     # UNCALIBRATED: set from real-week paraphrase pairs when they
                   # exist (calibration atom); until then this is a labeled guess.


def _intersects(a, b) -> bool:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return not (ax1 <= bx0 or bx1 <= ax0 or ay1 <= by0 or by1 <= ay0)


def _cos(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


class SaidThings:
    """Per scene-identity memory of what has already been uttered."""

    def __init__(self, embedder, bar: float = SIM_BAR) -> None:
        self._embedder = embedder
        self._bar = bar
        # entries: (text, vec, bbox-or-None)
        self._said: Dict[object, List[tuple]] = {}

    def is_new(self, scene_key, text: str) -> Tuple[bool, float]:
        """(novel?, closest-similarity). Does NOT record — deciding and
        remembering are separate acts so a refused claim isn't 'said'."""
        vec = self._embedder.embed(text)
        best = 0.0
        for _, said_vec, _bbox in self._said.get(scene_key, ()):
            best = max(best, _cos(vec, said_vec))
            if best >= self._bar:
                return False, best
        return True, best

    def record(self, scene_key, text: str,
               bbox: Optional[tuple] = None) -> None:
        self._said.setdefault(scene_key, []).append(
            (text, self._embedder.embed(text), bbox))

    def check_and_record(self, scene_key, text: str,
                         bbox: Optional[tuple] = None) -> bool:
        novel, _ = self.is_new(scene_key, text)
        if novel:
            self.record(scene_key, text, bbox)
        return novel

    def invalidate_region(self, scene_key, region_bbox: tuple) -> int:
        """The partial-change law (closes the OPEN question): a changed
        region forgets only ITS said-things — the still 90% of a chat
        stays known while the new bubble is news. Entries recorded
        without a bbox are scene-wide and survive region changes.
        Returns how many utterances were forgotten."""
        kept, dropped = [], 0
        for entry in self._said.get(scene_key, ()):
            bbox = entry[2]
            if bbox is not None and _intersects(bbox, region_bbox):
                dropped += 1
            else:
                kept.append(entry)
        if scene_key in self._said:
            self._said[scene_key] = kept
        return dropped

    def said_count(self, scene_key) -> int:
        return len(self._said.get(scene_key, ()))
