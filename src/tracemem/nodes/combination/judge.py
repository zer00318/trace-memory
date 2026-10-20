"""OWNS: proposal → graded MetaNode | rejection (spec 04 §5, I3).

Depth degradation is the provable anti-runaway: dreams-on-dreams are born at
the floor and prune fastest; only reconfirmed inferences climb.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from tracemem.domain import (
    Attribute, Confidence, Grade, Node, NodeKind, degrade, node_id,
)
from tracemem.nodes.combination.guardrails import (
    GuardrailViolation, assert_citations, assert_meta_write,
)
from tracemem.nodes.combination.proposal import Proposal
from tracemem import __version__ as _CODE_VERSION

# The vintage a derived belief is stamped with (SCHEMA §3.4): which code
# produced it, so an improved organ can rebuild exactly its own output and
# "you used to think X (built by v?)" is answerable.
BUILDER_VERSION = _CODE_VERSION

KIND_PRIOR = {"pattern": 0.9, "transitive": 0.75, "contradiction": 0.8, "causal": 0.5}
KIND_NODE = {"pattern": NodeKind.PATTERN, "transitive": NodeKind.HYPOTHESIS,
             "contradiction": NodeKind.QUESTION, "causal": NodeKind.HYPOTHESIS}


@dataclass(frozen=True)
class Judgment:
    accepted: bool
    node: Optional[Node] = None
    reason: str = ""


def _node_grade(node: Node) -> Grade:
    return max((a.grade for a in node.attributes), default=Grade.GIST)


def judge(proposal: Proposal, graph_store, t_ms: int = 0) -> Judgment:
    if proposal.is_none:
        return Judgment(False, reason="none")
    parents = [graph_store.get_node(pid) for pid in proposal.parent_node_ids]
    if any(p is None for p in parents):
        return Judgment(False, reason="missing parent")
    parent_episodes = set()
    for p in parents:
        for a in p.attributes:
            parent_episodes.update(a.source_episode_ids)
    try:
        assert_citations(proposal, parent_episodes)
    except GuardrailViolation as e:
        return Judgment(False, reason=str(e))

    depth = max(p.derivation_depth for p in parents) + 1
    grade = degrade([_node_grade(p) for p in parents], causal=(proposal.kind == "causal"))
    conf = math.prod(
        [max((a.confidence.value for a in p.attributes), default=0.5) for p in parents]
    ) * KIND_PRIOR[proposal.kind]

    if grade <= Grade.FLOOR and proposal.kind == "causal":
        return Judgment(False, reason="unearned dream: floor-grade causal with no reconfirmation")
    if _duplicate_exists(proposal, graph_store):
        return Judgment(False, reason="duplicate meta")

    meta = Node(
        id=node_id(proposal.kind, proposal.claim_text),
        kind=KIND_NODE[proposal.kind],
        derivation_depth=depth,
        parent_ids=tuple(proposal.parent_node_ids),
        builder_version=BUILDER_VERSION,
        attributes=[Attribute(key="claim", value=proposal.claim_text, grade=grade,
                              confidence=Confidence(min(1.0, conf)),
                              source_episode_ids=tuple(proposal.supported_by) or
                              tuple(sorted(parent_episodes))[:1],
                              t_ms=t_ms)],
    )
    assert_meta_write(meta)
    return Judgment(True, node=meta)


def _duplicate_exists(proposal: Proposal, graph_store) -> bool:
    return graph_store.get_node(node_id(proposal.kind, proposal.claim_text)) is not None
