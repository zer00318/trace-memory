"""A bounded, read-only projection of the living graph for a person.

This module does not open databases and cannot mutate the graph.  The daemon's
owner thread hands it already-decrypted domain objects, and it returns a small
JSON-safe topology plus drill-down evidence.  Keeping the boundary here means
the native face never needs the graph key and never reaches around the signed
helper to inspect SQLite directly.
"""
from __future__ import annotations

from typing import Iterable

from tracemem.domain.grade import in_words
from tracemem.domain.node import human_kind
from tracemem.nodes.accrual import label_of


MAX_NODES = 160
MAX_EDGES = 480
MAX_EVIDENCE = 40
MAX_TEXT = 320


def _retired(graph):
    try:
        return list(graph.retired())
    except Exception:                              # noqa: BLE001
        return []


def _live_nodes(graph):
    iterator = getattr(graph, "iter_nodes", None)
    if iterator is not None:
        return list(iterator())
    all_nodes = getattr(graph, "all_nodes", None)
    if all_nodes is not None:
        return list(all_nodes())
    return list(getattr(graph, "nodes", {}).values())


def _edge_rows(graph):
    all_edges = getattr(graph, "all_edges", None)
    if all_edges is not None:
        return list(all_edges())
    return list(getattr(graph, "edges", {}).values())


def _matches(node, terms) -> bool:
    if not terms:
        return True
    haystack = " ".join(
        [node.id, node.kind.value, label_of(node)] +
        ["%s %s" % (a.key, a.value) for a in node.attributes]
    ).lower()
    return all(term in haystack for term in terms)


def _summary(node, retired_t_ms=None) -> dict:
    return {
        "id": node.id,
        "label": label_of(node) or node.id[:8],
        "kind": human_kind(node.kind),
        "retired": retired_t_ms is not None,
        "retired_t_ms": retired_t_ms,
        "activation": round(float(node.activation), 4),
        "depth": int(node.derivation_depth),
        "last_active_t_ms": int(node.last_active_t_ms),
        "evidence_count": len({ep for a in node.attributes
                               for ep in a.source_episode_ids}),
    }


def snapshot(graph, query: str = "", include_retired: bool = True,
             limit: int = MAX_NODES) -> dict:
    """A searchable node/edge map small enough for one local-socket reply."""
    terms = [term for term in str(query or "").lower().split() if term]
    all_live = _live_nodes(graph)
    all_dead = _retired(graph)
    live = [(n, None) for n in all_live if _matches(n, terms)]
    dead = ([(n, int(t)) for n, t in all_dead if _matches(n, terms)]
            if include_retired else [])
    live.sort(key=lambda item: (
        -float(item[0].activation), -int(item[0].last_active_t_ms),
        label_of(item[0]).lower(), item[0].id))
    dead.sort(key=lambda item: (
        -int(item[1]), -float(item[0].activation),
        -int(item[0].last_active_t_ms), label_of(item[0]).lower(), item[0].id))
    cap = max(1, min(int(limit or MAX_NODES), MAX_NODES))
    # A large live graph must not push the moat entirely outside the cap.
    # Reserve up to one quarter for retired matches whenever they exist.
    dead_cap = min(len(dead), max(1, cap // 4)) if dead else 0
    live_cap = cap - dead_cap
    chosen = live[:live_cap] + dead[:dead_cap]
    ids = {node.id for node, _ in chosen}

    edges = []
    for edge in _edge_rows(graph):
        if edge.src in ids and edge.dst in ids:
            edges.append({
                "src": edge.src, "dst": edge.dst,
                "type": edge.type.value,
                "weight": round(float(edge.weight), 4),
                "evidence_count": int(edge.evidence_count),
            })
    edges.sort(key=lambda e: (-e["weight"], -e["evidence_count"],
                              e["type"], e["src"], e["dst"]))
    edges = edges[:MAX_EDGES]

    # Retiring removes live edges by design, but a derived belief retains its
    # parents.  Draw those historic derivation links when both endpoints are
    # in this view; otherwise a retired belief appears falsely ungrounded.
    known = {(e["src"], e["dst"], e["type"]) for e in edges}
    for node, retired_t in chosen:
        if retired_t is None:
            continue
        for parent in node.parent_ids:
            key = (node.id, parent, "derived_from")
            if parent in ids and key not in known and len(edges) < MAX_EDGES:
                edges.append({"src": node.id, "dst": parent,
                              "type": "derived_from", "weight": 1.0,
                              "evidence_count": 0, "historic": True})

    total_live = len(all_live)
    total_retired = len(all_dead)
    return {
        "nodes": [_summary(node, retired_t) for node, retired_t in chosen],
        "edges": edges,
        "total_live": total_live,
        "total_retired": total_retired,
        "shown": len(chosen),
        "truncated": len(live) + len(dead) > len(chosen),
        "query": str(query or ""),
    }


def _find(graph, node_id: str):
    node = graph.get_node(node_id)
    if node is not None:
        return node, None
    # One row, not the graveyard: `detail` asks this about every neighbour,
    # and an edge whose far end no longer exists used to decrypt every
    # tombstone once per such edge (2.0-5.5s per drill on the live graph).
    one = getattr(graph, "retired_node", None)
    if one is not None:
        hit = one(node_id)
        return (hit[0], int(hit[1])) if hit else (None, None)
    for retired_node, retired_t in _retired(graph):
        if retired_node.id == node_id:
            return retired_node, int(retired_t)
    return None, None


def detail(graph, claim_log, node_id: str) -> dict:
    """One belief, its relationships, and exact raw supporting moments."""
    node, retired_t = _find(graph, str(node_id))
    if node is None:
        raise KeyError("no current or retired belief %r" % node_id)

    attributes = []
    episode_ids = set()
    for attr in sorted(node.attributes,
                       key=lambda a: (a.key, -int(a.grade), -a.t_ms, a.value)):
        episodes = list(attr.source_episode_ids)
        episode_ids.update(episodes)
        attributes.append({
            "key": attr.key, "value": attr.value,
            "grade": in_words(attr.grade),
            "confidence": round(float(attr.confidence.value), 4),
            "first_t_ms": int(attr.first_t_ms or attr.t_ms),
            "last_t_ms": int(attr.t_ms),
            "episode_ids": episodes,
        })

    # Fetch one more than the face can show, and no more.  Truncating only
    # after ``claim_log.get`` used to decrypt every claim in a large episode
    # (633k on the production graph) on the daemon's single owner thread.
    # That made the cockpit's own evidence probe mute the product for minutes.
    candidate_cap = MAX_EVIDENCE + 1
    bounded = getattr(graph, "evidence_claim_ids", None)
    if bounded is not None:
        claim_ids = list(bounded(episode_ids, candidate_cap))
    else:
        claim_ids = []
        membership = getattr(graph, "claim_ids_for_episode", None)
        if membership is not None:
            for episode_id in sorted(episode_ids):
                for claim_id in membership(episode_id):
                    if claim_id not in claim_ids:
                        claim_ids.append(claim_id)
                    if len(claim_ids) >= candidate_cap:
                        break
                if len(claim_ids) >= candidate_cap:
                    break
    seen, evidence = set(), []
    for claim_id in claim_ids:
        if claim_id in seen:
            continue
        seen.add(claim_id)
        claim = claim_log.get(claim_id)
        if claim is None:
            continue
        evidence.append({
            "claim_id": claim.id,
            "text": claim.text[:MAX_TEXT],
            "t_ms": int(claim.coord.t_ms),
            "source": claim.coord.capture.source,
            "app": claim.coord.capture.app,
            "provenance": claim.provenance.klass.value,
            "grade": in_words(claim.grade),
        })
    evidence.sort(key=lambda item: (-item["t_ms"], item["claim_id"]))

    neighbours = []
    if retired_t is None:
        for edge in graph.edges_of(node.id):
            other_id = edge.dst if edge.src == node.id else edge.src
            other, other_retired = _find(graph, other_id)
            neighbours.append({
                "id": other_id,
                "label": label_of(other) if other is not None else other_id[:8],
                "kind": human_kind(other.kind) if other is not None else "belief",
                "edge": edge.type.value,
                "weight": round(float(edge.weight), 4),
                "retired": other_retired is not None,
            })
    else:
        for parent_id in node.parent_ids:
            parent, parent_retired = _find(graph, parent_id)
            neighbours.append({
                "id": parent_id,
                "label": label_of(parent) if parent is not None else parent_id[:8],
                "kind": human_kind(parent.kind) if parent is not None else "belief",
                "edge": "derived_from", "weight": 1.0,
                "retired": parent_retired is not None,
            })
    neighbours.sort(key=lambda n: (-n["weight"], n["label"], n["id"]))

    return dict(_summary(node, retired_t), attributes=attributes,
                neighbours=neighbours, evidence=evidence[:MAX_EVIDENCE],
                evidence_truncated=len(claim_ids) > MAX_EVIDENCE,
                parent_ids=list(node.parent_ids),
                builder_version=node.builder_version)
