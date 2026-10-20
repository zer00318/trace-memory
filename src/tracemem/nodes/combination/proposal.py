"""OWNS: the Proposal — what any combination kind emits for judgment (spec 04 §4-5)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple


@dataclass(frozen=True)
class Proposal:
    kind: str                      # pattern|transitive|contradiction|causal|none
    claim_text: str = ""
    parent_node_ids: Tuple[str, ...] = ()
    supported_by: Tuple[str, ...] = ()   # episode ids — citations, never fabricated
    extras: dict = field(default_factory=dict)

    @property
    def is_none(self) -> bool:
        return self.kind == "none"


NONE = Proposal(kind="none")
