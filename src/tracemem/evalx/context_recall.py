"""OWNS: the coverage/selection/cost split metric (spec X §3, 05 §6, P6.1).

Coverage is retrieval's job; selection is packing's job. Scored separately by
construction so a packing bug can never hide inside a retrieval number — the
design finding of spec 05.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Set


@dataclass(frozen=True)
class RecallRow:
    query_id: str
    coverage: float       # |relevant ∩ retrieved| / |relevant|
    selection: float      # |relevant ∩ packed| / |relevant ∩ retrieved|
    cost: float           # packed tokens / budget


@dataclass(frozen=True)
class RecallReport:
    rows: tuple
    coverage: float
    selection: float
    cost: float


def evaluate(queries, retrieve_fn, pack_fn, budget: int) -> RecallReport:
    """queries: iterable of dicts {id, cue, relevant: set of ids}.
    retrieve_fn(cue) -> iterable of candidate ids.
    pack_fn(candidate_ids, cue, budget) -> (packed ids, tokens_spent)."""
    rows: List[RecallRow] = []
    for q in queries:
        rel: Set[str] = set(q["relevant"])
        retrieved = list(retrieve_fn(q["cue"]))
        packed, tokens = pack_fn(retrieved, q["cue"], budget)
        got_r = rel & set(retrieved)
        got_p = rel & set(packed)
        rows.append(RecallRow(
            query_id=q["id"],
            coverage=len(got_r) / len(rel) if rel else 1.0,
            selection=len(got_p) / len(got_r) if got_r else (1.0 if not rel else 0.0),
            cost=tokens / budget if budget else 0.0))
    n = len(rows) or 1
    return RecallReport(
        rows=tuple(rows),
        coverage=sum(r.coverage for r in rows) / n,
        selection=sum(r.selection for r in rows) / n,
        cost=sum(r.cost for r in rows) / n)
