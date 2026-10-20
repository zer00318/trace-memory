"""OWNS: Node / Attribute / Edge — the META tier, the only mutable one (spec 00 §7).

N1: emergent kinds require parents; entity kinds forbid them (constructor asserts).
N2: provenance is per-Attribute, not per-Node (analyst epistemics).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Tuple

from tracemem.domain.confidence import Confidence
from tracemem.domain.grade import Grade


class NodeKind(Enum):
    ENTITY = "entity"
    PLACE = "place"
    OBJECT = "object"
    SITE = "site"
    APP = "app"
    TOPIC = "topic"
    PATTERN = "pattern"          # emergent
    HYPOTHESIS = "hypothesis"    # emergent
    QUESTION = "question"        # emergent

EMERGENT_KINDS = {NodeKind.PATTERN, NodeKind.HYPOTHESIS, NodeKind.QUESTION}


# WHAT A KIND IS CALLED WHEN A PERSON READS IT.
#
# `NodeKind.ENTITY` is a database type, and it reached the model as evidence:
# `dossier.render` printed "Juno — an entity", the model restated it as a
# fact, and the never-lie checker refused the whole answer — correctly, and
# deterministically, naming that exact sentence on three consecutive runs.
# 5 of 6 answers to "who is Juno?" were destroyed by one leaked word.
#
# Kinds that mean something to a reader keep their own word; only the ones
# that are schema get translated. One table, so the next renderer cannot
# invent its own.
_HUMAN_KIND = {NodeKind.ENTITY: "person"}


def human_kind(kind) -> str:
    """The word for this kind in a sentence a person will read."""
    if isinstance(kind, str):
        try:
            kind = NodeKind(kind)
        except ValueError:
            return kind
    return _HUMAN_KIND.get(kind, kind.value)



class EdgeType(Enum):
    CO_OCCURS = "co_occurs"
    LOCATED_AT = "located_at"
    SAME_AS = "same_as"
    DERIVED_FROM = "derived_from"
    CONTRADICTS = "contradicts"
    PRECEDES = "precedes"
    SEMANTIC = "semantic"        # close in MEANING (embedding space), not just co-seen


@dataclass
class Attribute:
    key: str
    value: str
    grade: Grade
    confidence: Confidence
    source_episode_ids: Tuple[str, ...]
    t_ms: int
    # WHEN THIS WAS FIRST TRUE. `t_ms` is the LAST time it was seen, so an
    # attribute has always carried the end of its life and never the start.
    #
    # That is the whole reason "where did I work in January" could not be
    # answered. The trail keeps both employers — accrue() joins rather than
    # overwrites — but with only a last-seen stamp there is no way to say
    # which one was true THEN. Two values, both real, no way to order a
    # question against them.
    #
    # 0 means unknown, and it is set to `t_ms` on birth so a fresh attribute
    # is honest about spanning a single instant rather than claiming to have
    # been true since the epoch. Attributes written before this field existed
    # deserialize with first == last, which is exactly what was known about
    # them: one sighting time, and no evidence about any other.
    first_t_ms: int = 0

    def __post_init__(self) -> None:
        if not self.source_episode_ids:
            raise ValueError("an Attribute must cite at least one source episode (N2)")


@dataclass
class Node:
    id: str
    kind: NodeKind
    activation: float = 0.0
    last_active_t_ms: int = 0
    salience_ema: float = 0.0
    derivation_depth: int = 0
    attributes: List[Attribute] = field(default_factory=list)
    parent_ids: Tuple[str, ...] = ()
    # Which code version derived this node (SCHEMA §3.4). Empty for entity-tier
    # (depth-0) nodes — they are observed, not derived. Set by the judge when a
    # meta node is minted, so a belief records its own vintage and an improved
    # organ can rebuild exactly what it once produced.
    builder_version: str = ""

    def __post_init__(self) -> None:
        emergent = self.kind in EMERGENT_KINDS
        if emergent and not self.parent_ids:
            raise ValueError("emergent node requires parent_ids (N1)")
        if not emergent and self.parent_ids:
            raise ValueError("depth-0 entity node must not have parents (N1)")


@dataclass
class Edge:
    src: str
    dst: str
    type: EdgeType
    weight: float = 1.0
    evidence_count: int = 1
    last_reinforced_t_ms: int = 0

    def reinforce(self, t_ms: int, delta: float = 1.0) -> None:
        """Hebbian: a productive pairing deepens (spec 04 §5)."""
        self.weight += delta
        self.evidence_count += 1
        self.last_reinforced_t_ms = t_ms
