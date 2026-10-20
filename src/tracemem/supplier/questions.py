"""OWNS: the questions the memory has for YOU, and what happens when you
answer one.

`It asks you / Surfacing`. The napkin's upper arc: the brain steers the eyes,
and part of steering is admitting what it cannot work out alone. TRACE has
been generating these since July (`nodes/birth/question_gen.py`) — two people
who might be one person, a name it cannot place — and they accumulated in
`hub.pending_questions` where nothing ever read them. A memory that notices
its own confusion and has no way to mention it is just a memory that stays
confused.

ONE PLACE, NEVER A NOTIFICATION. The brief is explicit and it is right:
questions wait to be looked at. A memory that interrupts to ask whether two
people are the same person has misunderstood its position in someone's day.

EVERY QUESTION SHOWS ITS EVIDENCE. "Is this the same Sam as before?" is
unanswerable without seeing which Sams. Answering has to take seconds, and
that means the reason is on screen next to the question, not one click away.

AN ANSWER IS EVIDENCE, NOT A SETTING. What comes back is testimony no sensor
could produce, so it lands as an OWNED claim at VERBATIM (app/founder.
answer_claim) and the night re-derives against it. Nothing is edited, no
threshold moves — F-GRADE, the same law that governs corrections.

HIGHEST INFORMATION GAIN FIRST. The generator already scores each question by
what settling it would buy; a list in arrival order would spend the founder's
patience on the cheapest questions first and leave the expensive ones unasked.
"""
from __future__ import annotations

from typing import List, Optional

# How many to show at once. The brief's interruption budget is about
# frequency; this is about the page. Twenty questions is not a list a person
# answers, it is a list they close.
MAX_SHOWN = 5


def _gain(q) -> float:
    return float(getattr(q, "info_gain", 0.0) or 0.0)


def open_questions(questions, limit: int = MAX_SHOWN) -> List:
    """Unanswered questions, most valuable first."""
    from tracemem.domain.question import QuestionStatus

    out = [q for q in (questions or [])
           if getattr(q, "status", None) in
           (QuestionStatus.QUEUED, QuestionStatus.ASKED)]
    out.sort(key=lambda q: -_gain(q))
    return out[:limit]


def evidence_for(question, graph_store) -> List[str]:
    """Why it is asking — the nodes it is confused between."""
    from tracemem.nodes.accrual import label_of

    out = []
    for nid in getattr(question, "about_node_ids", ()) or ():
        try:
            node = graph_store.get_node(nid)
        except Exception:                                    # noqa: BLE001
            node = None
        if node is None:
            continue
        label = label_of(node)
        if label:
            out.append("%s  [%s]" % (label, str(nid)[:8]))
    return out


def render(questions, graph_store) -> Optional[str]:
    """The panel, or None when it has nothing to ask.

    None rather than an empty page, for the same reason the morning surface
    returns None: a heading with nothing under it reads as a malfunction, and
    having no questions is a perfectly good state.
    """
    qs = open_questions(questions)
    if not qs:
        return None
    out = ["WHAT TRACE CANNOT WORK OUT ALONE", "=" * 44]
    for q in qs:
        out.append("")
        out.append("  %s" % q.text)
        for line in evidence_for(q, graph_store):
            out.append("      %s" % line)
        out.append("      answer:  ./trace ask answer %s \"...\""
                   % str(q.id)[:8])
    out.append("")
    out.append("-" * 44)
    out.append("Your answer is recorded as evidence in your own words. "
               "Nothing is edited.")
    return "\n".join(out)


def find(questions, qid: str):
    """The one question this id names, or None. Prefix-matched, never
    guessed between two — the same rule the moment view follows."""
    qid = str(qid or "").strip().lower()
    if not qid:
        return None
    hits = [q for q in (questions or [])
            if str(q.id).lower().startswith(qid)]
    return hits[0] if len(hits) == 1 else None
