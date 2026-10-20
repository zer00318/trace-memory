"""OWNS: the grade axis + multiplicative depth degradation (spec 00 §2).

G1 total fixed ordering; G2 degrade strictly decreasing with depth;
G3 a derived claim can never outrank min(parents) — the anti-runaway law.
"""
from __future__ import annotations

from enum import IntEnum
from typing import Sequence


class Grade(IntEnum):
    FLOOR = 0
    DERIVED = 1
    GIST = 2
    SPECIALIST = 3
    VERBATIM = 4


# HOW A GRADE READS TO A PERSON.
#
# `Grade.VERBATIM` means something precise to this codebase and nothing at
# all to the founder, and an answer that shows him "VERBATIM" beside a
# citation has told him the name of an enum, not how much to trust the
# sentence. These are the same five levels said in words someone can act on.
#
# ONE TABLE, HERE, beside the scale itself — the alternative is every face
# inventing its own wording and the product disagreeing with itself about
# what a grade means. That has already happened once with NodeKind.
_IN_WORDS = {
    Grade.VERBATIM: "read",        # characters recovered, not interpreted
    Grade.SPECIALIST: "recognised",
    Grade.GIST: "glanced at",      # a scene described, lossy on purpose
    Grade.DERIVED: "worked out",   # concluded from other claims
    Grade.FLOOR: "unsure",
}


def in_words(grade) -> str:
    """A grade as a person would say it. Never the enum's name."""
    try:
        return _IN_WORDS[Grade(int(grade))]
    except (ValueError, KeyError, TypeError):
        return "unsure"


def degrade(parents: Sequence[Grade], causal: bool = False) -> Grade:
    """One derivation step down from the weakest parent; causal costs extra."""
    if not parents:
        raise ValueError("degrade requires at least one parent grade")
    step = 1 + (1 if causal else 0)
    return Grade(max(Grade.FLOOR, min(parents) - step))
