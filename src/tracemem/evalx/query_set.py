"""OWNS: typed question set with the PROVENANCE RULE (spec X §2, F2/F3).

Every question carries authored_from: 'life' (written from lived truth BEFORE
store inspection) or 'store' (written by looking at what was captured).
Store-authored questions are useful for debugging but are EXCLUDED from the
decision gate — they can only ever flatter the system. Gap questions (gold
truth never captured) are first-class: they measure capture, not memory.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Tuple

TYPES = {"factual", "emergent", "verbatim"}
SOURCES = {"life", "store"}


@dataclass(frozen=True)
class EvalQuestion:
    id: str
    text: str
    qtype: str
    authored_from: str
    gold: Tuple[str, ...]      # answer is correct iff ANY gold substring appears
    gap: bool                  # truth was never captured; correct answer = abstain


def _parse(d: dict) -> EvalQuestion:
    if d.get("type") not in TYPES:
        raise ValueError("question %r: type must be one of %s" % (d.get("id"), sorted(TYPES)))
    if d.get("authored_from") not in SOURCES:
        raise ValueError("question %r: authored_from must be life|store (F2)" % d.get("id"))
    return EvalQuestion(id=str(d["id"]), text=str(d["text"]), qtype=d["type"],
                        authored_from=d["authored_from"],
                        gold=tuple(d.get("gold", ())), gap=bool(d.get("gap", False)))


def parse_all(items) -> List[EvalQuestion]:
    qs = [_parse(d) for d in items]
    if len({q.id for q in qs}) != len(qs):
        raise ValueError("duplicate question ids")
    return qs


def load(path: str) -> List[EvalQuestion]:
    with open(path) as f:
        text = f.read().strip()
    items = json.loads(text) if text.startswith("[") else \
        [json.loads(line) for line in text.splitlines() if line.strip()]
    return parse_all(items)


def gate_eligible(questions) -> List[EvalQuestion]:
    """The only questions the decision gate may see: life-authored, non-gap."""
    return [q for q in questions if q.authored_from == "life" and not q.gap]


def gap_rate(questions) -> float:
    life = [q for q in questions if q.authored_from == "life"]
    return sum(1 for q in life if q.gap) / len(life) if life else 0.0


def emergent_validity(questions, claims) -> List[str]:
    """F2-adjacent guard, learned from the 2026-07-19 rehearsal: an 'emergent'
    question whose gold appears verbatim inside ANY single claim is not
    emergent — a top-k retriever answers it without combining anything, and
    the decision gate reads falsely as SHED_SUFFICES. Returns the ids of
    mislabeled questions; a valid set returns []."""
    texts = [c.text.lower() for c in claims]
    bad = []
    for q in questions:
        if q.qtype != "emergent" or q.gap:
            continue
        if any(g.lower() in t for g in q.gold for t in texts):
            bad.append(q.id)
    return bad
