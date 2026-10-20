"""OWNS: verbatim-vs-verbatim conflict → a Question, never a silent overwrite
(spec 04 §4, risk R2: non-verbatim conflicts defer rather than accuse)."""
from __future__ import annotations

from tracemem.domain import Grade
from tracemem.nodes.combination.proposal import NONE, Proposal

# Contradiction is only meaningful on FUNCTIONAL keys — attributes where a
# node holding two values is impossible. Observational keys (topic, seen_as,
# app, page, ...) are multi-valued by NATURE: two topics are a life, not a
# conflict. Without this cut the detector fired on every multi-valued key and
# stole the causal kind's turn (witnessed, 2026-07-19 marination gauntlet).
FUNCTIONAL_KEYS = frozenset({"color", "label", "title", "relationship",
                             "phone", "email", "address", "birthday"})


def detect(node_a, node_b, graph_store) -> Proposal:
    attrs_a = {a.key: a for a in node_a.attributes}
    for b_attr in node_b.attributes:
        if b_attr.key not in FUNCTIONAL_KEYS:
            continue
        a_attr = attrs_a.get(b_attr.key)
        if a_attr is None or a_attr.value == b_attr.value:
            continue
        if a_attr.grade == Grade.VERBATIM and b_attr.grade == Grade.VERBATIM:
            return Proposal(
                kind="contradiction",
                claim_text="conflict on '%s': %r vs %r — which is right?" % (
                    b_attr.key, a_attr.value, b_attr.value),
                parent_node_ids=(node_a.id, node_b.id),
                supported_by=tuple(sorted(
                    set(a_attr.source_episode_ids) | set(b_attr.source_episode_ids))),
                extras={"key": b_attr.key, "values": (a_attr.value, b_attr.value)})
        # non-verbatim conflict: defer — an OCR guess contradicting a gist is
        # noise, not a contradiction worth the user's attention (R2).
    return NONE
