"""OWNS: apply the pure activation math to the graph (spec 04 §2).

Event-driven only: touches the node + its 1-hop neighborhood, never the graph.
"""
from __future__ import annotations

from tracemem.domain.activation import activation_now, boost, spread_deltas


def on_event(graph_store, node_id: str, event: str, surprise: float, t_ms: int) -> None:
    node = graph_store.get_node(node_id)
    if node is None:
        return
    node.activation = boost(node.activation, node.last_active_t_ms, t_ms, event, surprise)
    node.last_active_t_ms = t_ms
    node.salience_ema = 0.9 * node.salience_ema + 0.1 * surprise
    graph_store.upsert_node(node)

    neighbor_weights = []
    for e in graph_store.edges_of(node_id):
        other = e.dst if e.src == node_id else e.src
        neighbor_weights.append((other, e.weight))
    for nid, delta in spread_deltas(node.activation, neighbor_weights).items():
        n = graph_store.get_node(nid)
        if n is None:
            continue
        n.activation = min(1.0, activation_now(n.activation, n.last_active_t_ms, t_ms) + delta)
        n.last_active_t_ms = t_ms
        graph_store.upsert_node(n)
