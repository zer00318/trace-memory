"""OWNS: follow tombstone->successor map after late-boundary re-keying
(ruling A5). Attribute source_episode_ids are remapped in place (a split maps
one id to every shard); affected meta is activation-boosted and returned as
the stale set — the next night re-examines exactly that list.

Raw is out of reach by construction: this function holds a graph_store only.
"""
from __future__ import annotations

from typing import Dict, List, Tuple


def remap(graph_store, successor_map: Dict[str, Tuple[str, ...]]) -> List[str]:
    """Returns the ids of affected nodes — the stale set for re-examination."""
    if not successor_map:
        return []
    affected: List[str] = []
    for node in list(graph_store.nodes.values()):
        touched = False
        for a in node.attributes:
            if not any(sid in successor_map for sid in a.source_episode_ids):
                continue
            resolved = []
            for sid in a.source_episode_ids:
                resolved.extend(successor_map.get(sid, (sid,)))
            a.source_episode_ids = tuple(dict.fromkeys(resolved))
            touched = True
        if touched:
            node.activation = min(1.0, node.activation + 0.3)   # stale → hot → re-examined
            graph_store.upsert_node(node)
            affected.append(node.id)
    return affected
