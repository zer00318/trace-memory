"""OWNS: the ONLY LLM spender in combination (spec 04 §4).

Structural precheck (temporal precedence, repetition) gates ONE guarded,
abstention-default reasoner call. 'none' is the expected common output.
"""
from __future__ import annotations

import json

from tracemem.domain import EdgeType
from tracemem.nodes.combination.proposal import NONE, Proposal

MIN_PRECEDENCE_COUNT = 3

PROMPT = (
    "You are a memory consolidation function. Output at most ONE claim that is "
    "TRUE GIVEN BOTH nodes but stated in NEITHER - emergent, not a merge or a "
    "list. You may ONLY use the supplied facts; cite episode ids; never invent. "
    'If nothing emergent follows, output {"kind":"none"}.\n'
    "Respond as JSON: {\"kind\":\"causal\"|\"none\",\"claim\":...,"
    "\"supported_by\":[episode ids],\"rationale\":...}\n\nFACTS:\n%s"
)


def propose(node_a, node_b, graph_store, reasoner) -> Proposal:
    edge = graph_store.edge_between(node_a.id, node_b.id, EdgeType.PRECEDES)
    precedes = edge is not None and edge.evidence_count >= MIN_PRECEDENCE_COUNT
    if not precedes:
        return NONE                                    # structural gate: no GPU spend
    facts = json.dumps({
        "node_a": _facts(node_a), "node_b": _facts(node_b),
        "relation": {"type": "precedes", "count": edge.evidence_count},
    }, sort_keys=True)
    raw = reasoner.complete(PROMPT % facts)            # THE one expensive call
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return NONE                                    # malformed dream: abstain
    if d.get("kind") != "causal" or not d.get("claim"):
        return NONE
    return Proposal(
        kind="causal", claim_text=str(d["claim"]),
        parent_node_ids=(node_a.id, node_b.id),
        supported_by=tuple(d.get("supported_by", ())),
        extras={"rationale": d.get("rationale", "")})


def _facts(node):
    return {"id": node.id, "kind": node.kind.value,
            "attributes": [{"key": a.key, "value": a.value, "grade": int(a.grade),
                            "episodes": list(a.source_episode_ids)}
                           for a in node.attributes]}
