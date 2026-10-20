"""OWNS: node/edge/ledger store (spec 04 §1). In-memory impl of ports.GraphStore.

Production sqlite adapter is a later mechanical swap behind the same protocol;
every heart algorithm is written against the protocol, so the swap cannot
change behavior — that is what ports are for.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

from tracemem.domain import Edge, EdgeType, Episode, Node
from tracemem.domain.activation import activation_now


def pair_key(a: str, b: str) -> str:
    return "|".join(sorted((a, b)))


class MemoryGraphStore:
    def __init__(self) -> None:
        self.nodes: Dict[str, Node] = {}
        self.edges: Dict[Tuple[str, str, EdgeType], Edge] = {}
        self.ledger: Dict[str, Tuple[str, str]] = {}  # pair_key -> (outcome, evidence_fingerprint)
        # The graveyard of retired beliefs — the decaying cited HISTORY that IS
        # the moat. Retiring a meta node moves it here (final state + when it
        # died) instead of vaporizing it, so "you used to think X" is
        # answerable. id -> (node at retirement, retired_t_ms).
        self.tombstones: Dict[str, Tuple[Node, int]] = {}
        # WHAT WORK HAS ALREADY BEEN DONE (see the ledger note below).
        self._upkeep: Dict[str, set] = {}                  # episode -> claim ids
        # A claim is binding-complete only after upkeep returns.  This is
        # deliberately separate from episode membership: persistence can
        # succeed and the process can die before derivation does.
        self._binding: set = set()
        self._mining: Dict[Tuple[str, str], str] = {}      # (node,key) -> fingerprint
        # THE WAITING ROOM: mentions seen once, not yet durable. See
        # marination/background.CORROBORATING_EPISODES. key -> {episode: sighting}
        self._pending: Dict[str, Dict[str, dict]] = {}
        self._meta: Dict[str, str] = {}
        self._boundaries: Dict[Tuple[int, str], None] = {}
        self._episodes: Dict[str, Tuple[int, Episode, int]] = {}
        self._episode_claims: Dict[str, set] = {}
        self._retired_episodes: Dict[str, int] = {}
        self._episode_successors: Dict[str, set] = {}
        self._boundary_resolutions: Dict[Tuple[int, str], int] = {}

    # -- durable Binder protocol (ephemeral implementation for tests) -----
    def add_boundary(self, t_ms: int, kind: str = "scene") -> None:
        self._boundaries[(int(t_ms), str(kind))] = None

    def load_boundaries(self, kind: Optional[str] = None) -> List[int]:
        return sorted({t for (t, k) in self._boundaries
                       if kind is None or k == kind})

    def persist_episode(self, epoch_index: int, episode: Episode,
                        claim_ids=(), boundary_version: int = 0) -> None:
        self._episodes[episode.id] = (int(epoch_index), episode,
                                      int(boundary_version))
        self._episode_claims.setdefault(episode.id, set()).update(claim_ids)

    def membership_version(self):
        """Changes whenever membership does; both maps only ever grow."""
        return (sum(len(v) for v in self._episode_claims.values()),
                sum(len(v) for v in self._episode_successors.values()))

    def load_episodes(self):
        return {(ep.place_key, idx): Episode(
                    id=ep.id, place_key=ep.place_key, t_range=ep.t_range,
                    claim_ids=())
                for idx, ep, _version in self._episodes.values()
                if ep.id not in self._retired_episodes}

    def episode_ids(self) -> List[str]:
        return sorted(set(self._episodes) | set(self._episode_claims))

    def claim_ids_for_episode(self, episode_id: str) -> List[str]:
        return sorted(self._episode_claims.get(episode_id, ()))

    def evidence_claim_ids(self, episode_ids, limit: int = 41) -> List[str]:
        """A bounded evidence sample for a UI drill-down.

        This is deliberately separate from ``claim_ids_for_episode``: binder
        and reconciliation need complete membership, while a face that shows
        forty citations must never materialise a lifetime of raw ids first.
        """
        cap = max(0, int(limit))
        out = []
        for episode_id in sorted(set(episode_ids)):
            for claim_id in sorted(self._episode_claims.get(episode_id, ())):
                if claim_id not in out:
                    out.append(claim_id)
                    if len(out) >= cap:
                        return out
        return out

    def boundary_records(self):
        return sorted(self._boundaries)

    def all_episode_records(self):
        return [(idx, ep, version, self._retired_episodes.get(ep.id),
                 self.claim_ids_for_episode(ep.id))
                for idx, ep, version in self._episodes.values()]

    def episode_successor_records(self):
        return [(old, new, self._retired_episodes.get(old, 0))
                for old, news in self._episode_successors.items()
                for new in sorted(news)]

    def boundary_resolution_records(self):
        return [(t, kind, version)
                for (t, kind), version in self._boundary_resolutions.items()]

    def unresolved_boundaries(self) -> List[int]:
        return sorted(t for (t, kind) in self._boundaries
                      if kind == "late_scene_pending"
                      and (t, kind) not in self._boundary_resolutions)

    def boundary_version(self) -> int:
        try:
            return int(self.get_meta("boundary_version", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def resolve_episode_ids(self, episode_id: str) -> List[str]:
        out, frontier, seen = [], [str(episode_id)], set()
        while frontier:
            current = frontier.pop(0)
            if current in seen:
                continue
            seen.add(current)
            successors = sorted(self._episode_successors.get(current, ()))
            if successors:
                frontier.extend(successors)
            else:
                out.append(current)
        return list(dict.fromkeys(out))

    def commit_boundary_reconciliation(self, version: int, episodes,
                                       resolved_boundaries, successors) -> None:
        old = [ep.id for _idx, ep, _v in self._episodes.values()
               if ep.id not in self._retired_episodes]
        for key, ep in episodes.items():
            self.persist_episode(key[1], ep, ep.claim_ids, version)
            completed = self._binding.intersection(ep.claim_ids)
            self.upkeep_mark(ep.id, completed)
        self._retired_episodes.update({eid: int(version) for eid in old})
        for old_id, new_ids in successors.items():
            self._episode_successors.setdefault(old_id, set()).update(new_ids)
        for t_ms in resolved_boundaries:
            self.add_boundary(t_ms, "scene")
            self._boundary_resolutions[(int(t_ms), "late_scene_pending")] = \
                int(version)
        self.set_meta("boundary_version", version)

    def get_meta(self, key: str, default=None):
        return self._meta.get(key, default)

    def set_meta(self, key: str, value) -> None:
        self._meta[key] = str(value)

    def unbirth(self, node_id: str) -> None:
        """Remove an entity-tier node the CURRENT birth rules would not create.

        `delete_meta`/`retire_meta` refuse depth-0 on purpose: that tier is a
        projection of raw and must not be hand-edited. This is not an edit.
        It removes a projection that today's rules would never produce — the
        same thing a full re-derivation from raw would do, done incrementally
        so it does not cost a re-bind of the whole store. Raw is untouched and
        the claims that produced this node are unchanged on disk, so it comes
        back the moment the rules would produce it again.

        No tombstone, deliberately: a tombstone means "believed on evidence,
        then corrected", and that record's whole value is that it is true.
        This node was never believed on evidence — it was proposed by a regex
        that cannot tell Marta from Springfield.
        """
        self.nodes.pop(node_id, None)
        for k in [k for k in self.edges if node_id in (k[0], k[1])]:
            del self.edges[k]

    def pending_by_kind(self, kind: str, min_episodes: int = 1):
        out = []
        for key, per_ep in self._pending.items():
            rows = list(per_ep.values())
            if not rows or rows[0].get("kind") != kind:
                continue
            if len({r.get("episode_id") for r in rows}) >= min_episodes:
                out.append((key, rows[0].get("surface", "")))
        return out

    def max_node_t_ms(self) -> int:
        return max((n.last_active_t_ms for n in self.nodes.values()), default=0)

    # -- processed-work ledgers -------------------------------------------
    #
    # PROMOTED FROM AN INSTANCE ATTRIBUTE TO PART OF THE PROTOCOL (2026-07-29,
    # priority P1). These record which claims an episode has already had
    # upkept and which raw a node has already mined. They used to be created
    # on demand by `hasattr(graph_store, "upkeep_ledger")` inside
    # marination/background.py — which meant they lived only as long as the
    # process.
    #
    # That was harmless ONLY because the graph itself was ephemeral. The
    # moment the graph became durable (SqliteGraphStore in production), an
    # ephemeral ledger turned into a silent corruption: every boot's backfill
    # would see all claims as fresh and re-run upkeep, and `_edge()` calls
    # `edge.reinforce(t_ms, delta=0.5)`, which is NOT idempotent. Co-occurrence
    # weights and evidence_counts would compound on every restart — a graph
    # that looks more and more certain the more often you reboot, with nothing
    # anywhere reporting it. Accrual is idempotent underneath; edge
    # reinforcement is not, and the delta ledger is what stands between them.
    #
    # So the ledgers move onto the store, where a durable backend can persist
    # them, and the delta discipline survives a restart.

    def upkeep_seen(self, episode_id: str) -> set:
        """Claim ids already processed for this episode."""
        return set(self._upkeep.get(episode_id, ()))

    def upkeep_mark(self, episode_id: str, claim_ids) -> None:
        self._upkeep.setdefault(episode_id, set()).update(claim_ids)

    def binding_seen(self, claim_ids=None) -> set:
        if claim_ids is None:
            return set(self._binding)
        return self._binding.intersection(claim_ids)

    def binding_mark(self, claim_ids) -> None:
        self._binding.update(claim_ids)

    # -- the corroboration waiting room ------------------------------------
    #
    # Durable for the same reason the ledgers above are: a mention held back
    # for want of a second sighting must still be waiting after a restart.
    # An ephemeral waiting room would forget every held sighting on boot, so
    # nothing could ever reach two episodes and the person/topic tiers would
    # quietly empty — a silent failure, which is the worst kind.
    #
    # Keyed (key, episode_id) so replay is naturally idempotent: upkeep is
    # delta-driven, but a re-processed episode must not look like a second
    # independent sighting. Corroboration you can manufacture by re-running is
    # not corroboration.

    def record_sighting(self, key: str, episode_id: str, sighting: dict) -> None:
        self._pending.setdefault(key, {}).setdefault(episode_id, dict(sighting))

    def sightings(self, key: str) -> List[dict]:
        """Every recorded sighting of this surface, one per distinct episode."""
        return list(self._pending.get(key, {}).values())

    def mining_fingerprint(self, node_id: str, key: str) -> Optional[str]:
        """Evidence fingerprint this (node, key) was last mined against."""
        return self._mining.get((node_id, key))

    def set_mining_fingerprint(self, node_id: str, key: str,
                               fingerprint: str) -> None:
        self._mining[(node_id, key)] = fingerprint

    # -- nodes --
    def get_node(self, node_id: str) -> Optional[Node]:
        return self.nodes.get(node_id)

    def upsert_node(self, node: Node) -> None:
        self.nodes[node.id] = node

    def delete_meta(self, node_id: str) -> None:
        n = self.nodes.get(node_id)
        if n is None:
            return
        if n.derivation_depth == 0:
            raise ValueError("delete_meta on depth-0 node — the entity tier is not prunable here")
        del self.nodes[node_id]
        for k in [k for k in self.edges if node_id in (k[0], k[1])]:
            del self.edges[k]

    def retire_meta(self, node_id: str, t_ms: int) -> None:
        """Forget a meta node the moat-preserving way: record its final state
        in the tombstone graveyard, THEN remove it from the live graph.

        Same guard as delete_meta (depth-0 is not retired here), same effect
        on live reads (gone from nodes/edges), but the belief and its
        citations survive so the decay trajectory is never lost. Prune calls
        THIS; delete_meta remains for callers that genuinely want to vaporize.
        """
        n = self.nodes.get(node_id)
        if n is None:
            return
        if n.derivation_depth == 0:
            raise ValueError("retire_meta on depth-0 node — the entity tier is not retired here")
        self.tombstones[node_id] = (n, t_ms)
        del self.nodes[node_id]
        for k in [k for k in self.edges if node_id in (k[0], k[1])]:
            del self.edges[k]

    def retired(self) -> List[Tuple[Node, int]]:
        """Every retired belief with the moment it died — the queryable moat."""
        return list(self.tombstones.values())

    def retired_node(self, node_id: str):
        """(node, retired_t_ms) for one tombstone, or None."""
        return self.tombstones.get(node_id)

    def merge_into(self, keep_id: str, absorb_id: str, t_ms: int) -> None:
        """Fold `absorb` into `keep`: rewire absorb's edges onto keep, tombstone
        absorb (a merge is an auditable claim about identity), remove it. Unlike
        prune it MAY remove a depth-0 entity — merging identities is exactly
        that — but it is destructive, so callers invoke it only deliberately on
        strong evidence (identity.merge_ready). Attribute folding is the caller's
        job (identity.merge_identity) so this stays pure graph surgery."""
        absorb = self.nodes.get(absorb_id)
        keep = self.nodes.get(keep_id)
        if absorb is None or keep is None or keep_id == absorb_id:
            return
        for (s, d, ty), e in list(self.edges.items()):
            if absorb_id not in (s, d):
                continue
            del self.edges[(s, d, ty)]
            other = d if s == absorb_id else s
            if other == keep_id:
                continue                              # would be a self-edge
            ns, nd = (keep_id, other) if s == absorb_id else (other, keep_id)
            ex = self.edge_between(ns, nd, ty)
            if ex is None:
                self.upsert_edge(Edge(src=ns, dst=nd, type=ty, weight=e.weight,
                                      evidence_count=e.evidence_count,
                                      last_reinforced_t_ms=t_ms))
            else:
                ex.reinforce(t_ms, delta=e.weight)
                self.upsert_edge(ex)
        self.tombstones[absorb_id] = (absorb, t_ms)
        del self.nodes[absorb_id]

    # -- edges --
    def upsert_edge(self, edge: Edge) -> None:
        self.edges[(edge.src, edge.dst, edge.type)] = edge

    def edges_of(self, node_id: str) -> List[Edge]:
        return [e for e in self.edges.values() if node_id in (e.src, e.dst)]

    def edge_between(self, a: str, b: str,
                     type: Optional[EdgeType] = None) -> Optional[Edge]:
        """INDEXED, not scanned (2026-07-29).

        This was a linear walk of every edge in the graph. `edges` is already
        keyed by (src, dst, type), so the answer was always one dict lookup
        away — the scan was pure cost, and it was quadratic cost, because the
        callers that need it most call it once per PAIR.

        Measured consequence, found by a test that hung: `semantic_pass` over
        400 nodes is 79,800 pair checks, and each one walked the full edge set.
        On the first production night run that set reached 265,577 edges — on
        the order of 10^10 set-comparisons for a single pass. The graph
        explosion made this pathological, but the scan was already the wrong
        shape at any size, and it is on the hot path of `_edge()` during
        upkeep, which runs on every flush.

        Same semantics exactly: an edge is undirected for this query, so both
        orientations are tried; `type=None` still means "any type between
        these two", now by asking the index for each type rather than by
        reading every edge in the graph.
        """
        if type is not None:
            return (self.edges.get((a, b, type))
                    or self.edges.get((b, a, type)))
        for etype in EdgeType:
            found = (self.edges.get((a, b, etype))
                     or self.edges.get((b, a, etype)))
            if found is not None:
                return found
        return None

    def neighbor_ids(self, node_id: str, hops: int = 1) -> set:
        """The ids `neighbors` would return, without decrypting any node."""
        frontier, seen = {node_id}, {node_id}
        for _ in range(hops):
            nxt = set()
            for nid in frontier:
                for e in self.edges_of(nid):
                    other = e.dst if e.src == nid else e.src
                    if other not in seen:
                        nxt.add(other)
                        seen.add(other)
            frontier = nxt
        return seen - {node_id}

    def neighbors(self, node_id: str, hops: int = 1) -> List[Node]:
        return [self.nodes[n] for n in self.neighbor_ids(node_id, hops)
                if n in self.nodes]

    # -- activation-indexed reads --
    def nodes_with_activation_ge(self, floor: float, t_ms: int) -> List[Node]:
        return [n for n in self.nodes.values()
                if activation_now(n.activation, n.last_active_t_ms, t_ms) >= floor]

    # -- combination ledger (I4 idempotence) --
    def ledger_fresh(self, key: str, fingerprint: str = "") -> bool:
        rec = self.ledger.get(key)
        return rec is not None and rec[1] == fingerprint

    def record_ledger(self, key: str, outcome: str, fingerprint: str) -> None:
        self.ledger[key] = (outcome, fingerprint)
