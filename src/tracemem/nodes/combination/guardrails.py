"""OWNS: the hard walls, built BEFORE the combiner (spec 04 §5, I2/I3).

Meta-only writes; no fabricated citations; verbatim never overridden. These
raise — they are not advisory.
"""
from __future__ import annotations

from typing import Iterable

from tracemem.domain import Claim, Episode, Grade, Node
from tracemem.nodes.combination.proposal import Proposal


class GuardrailViolation(Exception):
    pass


def assert_meta_write(target) -> None:
    """I2: the combiner may only ever write META (emergent Nodes)."""
    if isinstance(target, (Claim, Episode)):
        raise GuardrailViolation("attempted write to RAW tier: %s" % type(target).__name__)
    if isinstance(target, Node) and target.derivation_depth == 0:
        raise GuardrailViolation("attempted combiner write to a depth-0 entity node")


def assert_citations(proposal: Proposal, parent_episode_ids: Iterable[str]) -> None:
    """No fabricated citations: everything cited must come from the parents."""
    allowed = set(parent_episode_ids)
    rogue = set(proposal.supported_by) - allowed
    if rogue:
        raise GuardrailViolation("proposal cites episodes outside its parents: %s" % sorted(rogue))


def assert_grade_ceiling(derived_grade: Grade, parent_grades: Iterable[Grade]) -> None:
    """I3: a derived claim can never outrank its weakest parent."""
    weakest = min(parent_grades)
    if derived_grade >= weakest and weakest != Grade.FLOOR:
        raise GuardrailViolation(
            "derived grade %s would not be below weakest parent %s" % (derived_grade, weakest))
