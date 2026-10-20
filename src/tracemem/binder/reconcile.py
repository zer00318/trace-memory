"""OWNS: versioned boundary sets per (place, day); late boundaries re-key
epochs via full re-projection; tombstone -> successor map (ruling A5).

Because projection is a pure function of (claims, bounds), reconciliation is
NOT a repair algorithm — it is re-running the pure function with the merged
boundary set and diffing the output. Old episode ids that no longer exist are
tombstoned; their successors are exactly the new episodes that now hold their
claims. Raw claims are never touched (the reconciler has read-only access).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from tracemem.binder.projection import project


class BoundaryReconciliationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReconcileResult:
    version: int
    tombstoned: Dict[str, Tuple[str, ...]]   # old episode id -> successor ids
    episodes: Dict = field(default_factory=dict)   # EpisodeKey -> Episode (current)


class BoundaryReconciler:
    """claim_source: callable (place_key, day) -> [Claim] — read-only. The
    reconciler never writes claims; it only recomputes the projection tier."""

    def __init__(self, claim_source=None):
        self._claim_source = claim_source
        self._bounds: Dict[Tuple[Optional[str], str], Tuple[int, ...]] = {}
        self._versions: Dict[Tuple[Optional[str], str], int] = {}
        self._projections: Dict[Tuple[Optional[str], str], Dict] = {}
        self._successors: Dict[str, Tuple[str, ...]] = {}

    def apply_late_boundaries(self, place_key, day, new_boundaries) -> ReconcileResult:
        key = (place_key, day)
        merged = tuple(sorted(set(self._bounds.get(key, ())) | set(new_boundaries)))
        if merged == self._bounds.get(key):           # nothing new: fully idempotent
            return ReconcileResult(version=self._versions[key], tombstoned={},
                                   episodes=self._projections[key])
        self._bounds[key] = merged
        self._versions[key] = self._versions.get(key, 0) + 1

        claims = list(self._claim_source(place_key, day)) if self._claim_source else []
        old = self._projections.get(key, {})
        # salted with the boundary version: a re-keyed epoch ALWAYS mints a
        # fresh id (A5) — an id that silently changed meaning would be the
        # exact silent-corruption class this module exists to kill.
        new = project(claims, list(merged), salt="b%d" % self._versions[key])
        self._projections[key] = new

        claim_owner = {}                              # claim id -> new episode id
        for ep in new.values():
            for cid in ep.claim_ids:
                claim_owner[cid] = ep.id
        tombstoned: Dict[str, Tuple[str, ...]] = {}
        for ep in old.values():
            succ = tuple(sorted({claim_owner[cid] for cid in ep.claim_ids
                                 if cid in claim_owner}))
            tombstoned[ep.id] = succ
            self._successors[ep.id] = succ
        return ReconcileResult(version=self._versions[key],
                               tombstoned=tombstoned, episodes=new)

    def successor_of(self, episode_id: str) -> Tuple[str, ...]:
        """Resolve an episode id through the tombstone map, transitively.
        A live id resolves to itself; a split resolves to every shard."""
        if episode_id not in self._successors:
            return (episode_id,)
        out: List[str] = []
        for s in self._successors[episode_id]:
            out.extend(self.successor_of(s))
        return tuple(dict.fromkeys(out))

    @property
    def successor_map(self) -> Dict[str, Tuple[str, ...]]:
        return dict(self._successors)


def reconcile_pending(graph_store, claim_log) -> dict:
    """Install all quarantined late boundaries as one durable version.

    The raw log is read through its coordinate index only.  Old episodes and
    memberships remain queryable; redirects connect every old citation to
    the new active projection.  The graph store commits the entire version in
    one transaction, so a kill yields either the old projection or the new.
    """
    from tracemem.binder.event_segmenter import boundaries
    from tracemem.binder.projection import project_rows

    pending = list(graph_store.unresolved_boundaries())
    if not pending:
        return {"changed": False, "version": graph_store.boundary_version(),
                "resolved_boundaries": 0, "episodes": 0,
                "successor_links": 0}
    version = graph_store.boundary_version() + 1
    scene = list(graph_store.load_boundaries("scene"))
    end = claim_log.max_event_t_ms() + 1
    merged = sorted(set(scene) | set(pending))
    bounds = boundaries(merged, 0, max(1, end))
    new = project_rows(claim_log.projection_rows(), bounds,
                       salt="b%d" % version)

    owner = {claim_id: ep.id for ep in new.values()
             for claim_id in ep.claim_ids}
    old = graph_store.load_episodes()
    successors = {}
    orphaned = []
    for ep in old.values():
        claim_ids = graph_store.claim_ids_for_episode(ep.id)
        missing = [cid for cid in claim_ids if cid not in owner]
        if missing:
            orphaned.extend(missing[:10])
        successors[ep.id] = tuple(sorted(
            {owner[cid] for cid in claim_ids if cid in owner}))
    if orphaned:
        raise BoundaryReconciliationError(
            "%d sampled episode memberships do not resolve to raw; refusing "
            "to retire the active projection (%s)" %
            (len(orphaned), ", ".join(orphaned[:3])))
    graph_store.commit_boundary_reconciliation(
        version, new, pending, successors)
    return {
        "changed": True,
        "version": version,
        "resolved_boundaries": len(pending),
        "episodes": len(new),
        "successor_links": sum(len(v) for v in successors.values()),
        "claims": len(owner),
    }
