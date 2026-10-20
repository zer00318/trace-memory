"""OWNS: attribute accrual — how a node's body grows without lying (spec 04 §1, N2).

Laws:
- Accrual is idempotent per (key, value, episode): replaying a claim can only
  merge evidence, never duplicate it. This is what makes hub re-upkeep safe.
- Nothing is overwritten: a conflicting value is a NEW attribute; the
  'current' view is a read-time choice (grade, then recency), so history and
  provenance survive every disagreement (analyst epistemics).
- Stats are DERIVED, never stored: sightings = distinct source episodes.
  Derived state cannot go stale.
"""
from __future__ import annotations

from typing import List, Optional, Set

from tracemem.domain import Attribute, Confidence, Grade, Node, NodeKind


def accrue(node: Node, key: str, value: str, grade: Grade,
           confidence: Confidence, episode_id: str, t_ms: int) -> bool:
    """Returns True if this added NEW evidence (new attr or new episode)."""
    for a in node.attributes:
        if a.key == key and a.value == value:
            grew = False
            if episode_id not in a.source_episode_ids:
                a.source_episode_ids = tuple((*a.source_episode_ids, episode_id))
                grew = True
            if grade > a.grade:                    # better-witnessed same fact
                a.grade = grade
                grew = True
            if confidence.value > a.confidence.value:
                a.confidence = confidence
                grew = True
            # THE SPAN GROWS AT BOTH ENDS. Seeing the same fact earlier than
            # anything recorded so far is evidence about when it STARTED, and
            # claims do not always arrive in order — a backfill, a late
            # phone sync, a re-read of an old file all deliver the past.
            if a.first_t_ms == 0 or t_ms < a.first_t_ms:
                a.first_t_ms = t_ms
                grew = True
            a.t_ms = max(a.t_ms, t_ms)
            return grew
    node.attributes.append(Attribute(
        key=key, value=value, grade=grade, confidence=confidence,
        source_episode_ids=(episode_id,), t_ms=t_ms, first_t_ms=t_ms))
    return True


def label_of(node: Node) -> str:
    """The text that STANDS FOR this node wherever a human or an embedder
    must read it: its most faithful attribute value (highest grade, recency
    breaks ties). Empty for an unlabelled ghost, which is not nothing — it is
    the honest answer, and callers fall back to the id rather than invent one.

    PROMOTED HERE 2026-07-29 (was private to marination/semantic.py) after the
    first production night run showed why it is load-bearing in two places at
    once. The combination kinds were building their claim text out of raw node
    IDS, producing 727 beliefs that all read

        "cb450235-… and 94efc3b5-…: both repeatedly present with acaa327f-…"

    which is unreadable to the founder who is supposed to GRADE it — and,
    worse, embeds almost identically for every pair, because the only varying
    tokens are UUIDs. `semantic_pass` then wired those near-identical labels
    into 263,931 edges across 781 nodes: 87% of a complete graph, which is
    information-free, since spreading activation over a graph where everything
    touches everything reaches everything and ranks nothing.

    One function, so a node reads the same way to the founder, to the embedder,
    and to the mouth.
    """
    best = None
    for a in node.attributes:
        if best is None or (a.grade, a.t_ms) > (best.grade, best.t_ms):
            best = a
    return best.value if best else ""


def name_or_id(node, node_id: str = "") -> str:
    """`label_of`, falling back to a short id — what derived CLAIM TEXT uses.

    Never returns an empty string: a belief that names nothing cannot be
    judged, deduped, or read aloud.
    """
    if node is None:
        return (node_id or "?")[:8]
    return label_of(node) or (node.id or node_id or "?")[:8]


def current_value(node: Node, key: str) -> Optional[str]:
    """The read-time 'current' answer: highest grade wins; recency breaks ties."""
    best = None
    for a in node.attributes:
        if a.key != key:
            continue
        if best is None or (a.grade, a.t_ms) > (best.grade, best.t_ms):
            best = a
    return best.value if best else None


def values_of(node: Node, key: str) -> List[str]:
    return [a.value for a in node.attributes if a.key == key]


def names_of(node: Node) -> List[str]:
    return [v for v in values_of(node, "seen_as") if v.lower() != "a person"]


def episodes_of(node: Node) -> Set[str]:
    out: Set[str] = set()
    for a in node.attributes:
        out.update(a.source_episode_ids)
    return out


def sightings(node: Node) -> int:
    return len(episodes_of(node))


_AGENDA_BY_KIND = {
    NodeKind.ENTITY: [("seen_as", "who is this?"),
                      ("relationship", "how do they relate to you?")],
    NodeKind.OBJECT: [("color", "what color is it?"),
                      ("located_at", "where does it live?")],
    NodeKind.SITE:   [("title", "what is this site for you?")],
    NodeKind.PLACE:  [("label", "what do you call this place?")],
}


def agenda_keys(node: Node) -> List[str]:
    """The attribute keys this node still hungers for — the mining loop's
    input. Same logic as agenda(), machine-shaped."""
    out = []
    for key, _question in _AGENDA_BY_KIND.get(node.kind, []):
        have = values_of(node, key)
        if not have or (key == "seen_as" and not names_of(node)):
            out.append(key)
    return out


def agenda(node: Node) -> List[str]:
    """The node's open questions — DERIVED from what it lacks (the napkin's
    'first thought that can be marinated on'). An anonymous person's agenda
    is maximal: it doesn't even know who it is."""
    keys = set(agenda_keys(node))
    return [q for key, q in _AGENDA_BY_KIND.get(node.kind, []) if key in keys]


def true_at(node, key: str, t_ms: int):
    """What this node's `key` was at a given moment — or None.

    `Facts that expire`. A person changes jobs, cities, phones. The trail
    already keeps both values, because accrue() joins rather than overwrites;
    what was missing was any way to ORDER a question against them. "Where did
    I work in January" has a correct answer and the graph could not reach it.

    TRUE-FOR-THEN, NOT TRUE-FOR-NOW. The value returned is the one whose
    observed span contains that moment. A value first seen in March is not
    the answer for January, however confident the machine is about it today,
    and answering with it would be the memory rewriting someone's history to
    match its present.

    NONE IS A REAL ANSWER. Asked about a time before anything was observed,
    this says so instead of reaching for the nearest value. "I did not know
    you yet" is true; "you worked at Globex" is an invention with a date on
    it, which is worse than a plain one because it looks checkable.

    A RETRACTED VALUE NEVER WINS. See grading.retracted(): something the
    machine took back is still in the trail to be read, not to be answered
    with.
    """
    from tracemem.domain.grading import retracted

    best = None
    for a in node.attributes:
        if a.key != key or a.grade == retracted():
            continue
        start = a.first_t_ms or a.t_ms
        if start <= t_ms <= a.t_ms:
            # Ties go to the better-witnessed value, then the later one — two
            # overlapping spans mean two things were observed in the same
            # window, and the more faithful observation is the answer.
            if best is None or (a.grade, a.t_ms) > (best.grade, best.t_ms):
                best = a
    return best.value if best is not None else None


def spans(node, key: str):
    """Every value this node's `key` has held, with when it was observed.

    The shape a person would want back from "what have I called this?" — and
    what a face needs to show a history rather than a single answer.
    """
    from tracemem.domain.grading import retracted

    out = [(a.value, a.first_t_ms or a.t_ms, a.t_ms,
            a.grade == retracted())
           for a in node.attributes if a.key == key]
    out.sort(key=lambda r: (r[1], r[2]))
    return out
