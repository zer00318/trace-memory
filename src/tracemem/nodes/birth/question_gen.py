"""OWNS: uncertainty → a clarifying question, gated by the attention budget
(spec 04 birth / 08 §5, upgraded for birth v2).

Info gain now scales with RECURRENCE: a ghost seen once is not worth an
interruption; a ghost seen five times is exactly the "is this the same
person you spoke with yesterday?" moment from the founder's own framing.
Ambiguous named merges (two plausible Sams) name both candidates so the
answer can actually settle the fork.
"""
from __future__ import annotations

import uuid
from typing import Optional

from tracemem.domain.question import Question, QuestionStatus
from tracemem.nodes.accrual import names_of, sightings

CONF_THRESHOLD = 0.6
GAIN_FLOOR = 0.25


def info_gain(resolution, graph_store=None) -> float:
    base = 1.0 - resolution.confidence
    leverage = 1.0 if getattr(resolution.mention, "kind", "") == "person" else 0.4
    recurrence = 1.0
    if graph_store is not None and resolution.node is not None:
        node = graph_store.get_node(resolution.node.id) or resolution.node
        recurrence = 1.0 + 0.2 * min(sightings(node), 5)
    return base * leverage * recurrence


def _text(resolution) -> str:
    surface = getattr(resolution.mention, "surface", "this")
    if resolution.node is None or resolution.runner_up:
        return ("You mentioned '%s' — is this the same %s as before, "
                "or a different one?" % (surface, surface))
    node = resolution.node
    if not names_of(node):
        n = sightings(node)
        if n >= 2:
            return ("Someone has appeared with you %d times recently — "
                    "who is this?" % n)
        return "Who was the person with you just now?"
    return "Is '%s' the same person you interacted with before?" % surface


def maybe_question(resolution, budget, t_ms: int,
                   graph_store=None) -> Optional[Question]:
    if resolution.confidence >= CONF_THRESHOLD:
        return None
    gain = info_gain(resolution, graph_store)
    if gain < GAIN_FLOOR:
        return None
    about = (resolution.candidate_ids if resolution.node is None
             else tuple(filter(None, (resolution.node.id, resolution.runner_up))))
    q = Question(
        id=str(uuid.uuid4()),
        text=_text(resolution),
        about_node_ids=about,
        info_gain=gain, created_t_ms=t_ms,
        status=QuestionStatus.QUEUED)
    if budget is not None and budget.may_ask(t_ms):
        q.status = QuestionStatus.ASKED
        budget.record_ask(t_ms)
    return q
