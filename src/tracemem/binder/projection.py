"""OWNS: the append-only episode projection (spec 03 §3).

B1 deterministic; B3 order-independent + monotonic growth; B4 idempotent.
No reasoner import exists in this package — the constraint is structural.
"""
from __future__ import annotations

import uuid
from typing import Dict, Iterable, Optional, Sequence, Tuple

from tracemem.binder.event_segmenter import epoch_index
from tracemem.domain.claim import Claim
from tracemem.domain.episode import Episode
from tracemem.domain.time import TimeRange

_NS = uuid.uuid5(uuid.NAMESPACE_URL, "tracemem-episode")

EpisodeKey = Tuple[Optional[str], int]


def episode_key(claim: Claim, bounds: Sequence[int]) -> EpisodeKey:
    return (claim.coord.world.place_key, epoch_index(claim.coord.t_ms, bounds))


def episode_id(key: EpisodeKey, salt: str = "",
               start_ms: Optional[int] = None) -> str:
    """salt: boundary-set version (ruling A5). Live projection uses "" — ids
    are stable across incremental runs. The reconciler salts with its version
    so a RE-KEYED epoch always mints a fresh id: an episode id may grow its
    claims but never silently change what time-slice it means."""
    # V2 names an episode by its actual lower boundary, not its ordinal in a
    # process-local boundary list.  A restarted process may have a shorter
    # list; reusing an old ordinal then reused an old UUID for a distant new
    # moment.  Existing stored IDs are never recomputed.  ``start_ms=None``
    # retains the legacy function for explicit audits and old callers.
    base = ("v2:%s:%d" % (key[0], start_ms) if start_ms is not None
            else "%s:%d" % (key[0], key[1]))
    return str(uuid.uuid5(_NS, (salt + "|" + base) if salt else base))


def _epoch_range(idx: int, bounds: Sequence[int], t_min: int, t_max: int) -> TimeRange:
    b = list(bounds)
    start = t_min if idx == 0 else b[idx - 1]
    end = b[idx] if idx < len(b) else t_max
    return TimeRange(min(start, end), max(start, end))


def project(claims: Iterable[Claim], bounds: Sequence[int],
            existing: Optional[Dict[EpisodeKey, Episode]] = None,
            salt: str = "") -> Dict[EpisodeKey, Episode]:
    """Reference projection. Feed claims in ANY order, any number of times:
    the result is identical (B1/B3/B4). `existing` enables incremental use."""
    episodes: Dict[EpisodeKey, Episode] = dict(existing or {})
    claim_list = list(claims)
    if not claim_list:
        return episodes
    t_min = min(c.coord.t_ms for c in claim_list)
    t_max = max(c.coord.t_ms for c in claim_list)
    for c in sorted(claim_list, key=lambda c: (c.coord.t_ms, c.id)):  # canonical order → determinism
        k = episode_key(c, bounds)
        epoch_range = _epoch_range(k[1], bounds, t_min, t_max)
        stable_start = 0 if k[1] == 0 else int(bounds[k[1] - 1])
        ep = episodes.get(k)
        if ep is None:
            ep = Episode(id=episode_id(k, salt, start_ms=stable_start),
                         t_range=epoch_range, place_key=k[0])
        elif (epoch_range.start_ms < ep.t_range.start_ms
              or epoch_range.end_ms > ep.t_range.end_ms):
            ep = Episode(
                id=ep.id, place_key=ep.place_key,
                t_range=TimeRange(min(ep.t_range.start_ms,
                                      epoch_range.start_ms),
                                 max(ep.t_range.end_ms,
                                     epoch_range.end_ms)),
                claim_ids=ep.claim_ids)
        episodes[k] = ep.with_claims([c.id])
    return episodes


def project_rows(rows, bounds: Sequence[int], salt: str = ""):
    """Project lightweight ``(claim_id, t_ms, place_key)`` rows.

    Boundary reconciliation changes coordinate grouping, never claim
    semantics.  Reading and retaining hundreds of thousands of decrypted
    Claim objects for that operation is both unnecessary and a privacy-risky
    memory spike; these three plaintext index fields are sufficient.
    """
    episodes = {}
    for claim_id, t_ms, place_key in rows:
        idx = epoch_index(int(t_ms), bounds)
        key = (place_key, idx)
        stable_start = 0 if idx == 0 else int(bounds[idx - 1])
        ep = episodes.get(key)
        if ep is None:
            ep = Episode(id=episode_id(key, salt, start_ms=stable_start),
                         t_range=TimeRange(int(t_ms), int(t_ms)),
                         place_key=place_key)
        elif t_ms < ep.t_range.start_ms or t_ms > ep.t_range.end_ms:
            ep = Episode(
                id=ep.id, place_key=ep.place_key,
                t_range=TimeRange(min(ep.t_range.start_ms, int(t_ms)),
                                 max(ep.t_range.end_ms, int(t_ms))),
                claim_ids=ep.claim_ids)
        episodes[key] = ep.with_claims([str(claim_id)])
    return episodes
