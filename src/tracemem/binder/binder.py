"""OWNS: orchestrate segmentation + projection over a claim window (spec 03 §6).

B2 is structural: this class has NO reasoner attribute and imports none —
ambiguity ('a person in blue') passes through verbatim as claim text; the
resolver that might name that person lives in nodes/birth, behind the seam.
"""
from __future__ import annotations

from typing import Dict, List

from tracemem.binder.event_segmenter import boundaries_with_gaps
from tracemem.capture.presence import attention_gaps

# How far back to look for absence witnesses that reach INTO this window.
# 36h covers the longest plausible single absence (a weekend away is several
# absences, each with its own witness) without scanning a life. UNCALIBRATED.
GAP_LOOKBACK_MS = 36 * 60 * 60 * 1000
from tracemem.binder.projection import project
from tracemem.domain.episode import Episode


class Binder:
    def __init__(self, claim_store):
        self.claim_store = claim_store

    def run(self, scene_changes, t0_ms: int, t1_ms: int) -> List[Episode]:
        """Deterministic: same window + same scene changes -> same episodes,
        regardless of how many times or in what claim order it runs (B1/B4).

        Attention gaps (away / closed-eyes witnesses) are read from the
        claims themselves and become boundaries — a moment may not span an
        empty chair or a shut screen. Reading them from the store rather
        than taking them as an argument keeps run()'s determinism promise:
        the same window still yields the same episodes.
        """
        claims = self.claim_store.read_range(t0_ms, t1_ms)

        # Witnesses are read from a LOOKBACK window, not the run window.
        # RE-AUDIT 2026-07-21: a gap claim is stamped at the moment the
        # absence STARTED, so an absence running from before t0 into the
        # window had its claim outside [t0, t1) and was invisible — the
        # return edge was lost and the day's first moment silently began
        # inside an absence. That is not an exotic case: it is every
        # morning, where the night's closed-eyes claim is stamped the
        # previous evening.
        witnesses = self.claim_store.read_range(t0_ms - GAP_LOOKBACK_MS, t1_ms)
        gaps = attention_gaps(witnesses)
        bounds = boundaries_with_gaps(scene_changes, gaps, t0_ms, t1_ms)
        projected: Dict = project(claims, bounds)
        return sorted(projected.values(), key=lambda e: (e.t_range.start_ms, e.id))
