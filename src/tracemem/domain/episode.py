"""OWNS: Episode — append-only projection unit (spec 00 §6).

E1: with_claims returns a NEW episode; the old claim set is a strict subset of
the new — grows-only, the late-arrival guarantee. E2: unique + order-stable.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Optional, Tuple

from tracemem.domain.time import TimeRange


@dataclass(frozen=True)
class Episode:
    id: str
    t_range: TimeRange
    place_key: Optional[str]
    claim_ids: Tuple[str, ...] = ()

    def with_claims(self, more: Iterable[str]) -> "Episode":
        merged = dict.fromkeys(self.claim_ids)
        for cid in more:
            merged.setdefault(cid, None)
        return replace(self, claim_ids=tuple(merged.keys()))
