"""OWNS: schema crystallization — repetition compressing into structure
(the prediction spine's write-path: P5.4 'the graph IS the model').

A (time-slot, place) cell that recurs past MIN_SUPPORT crystallizes into a
SCHEMA node — "weekday 13h: office, usually ramen" — an emergent PATTERN
node whose parents are the place it lives at. Schemas are what make the
routine CHEAP (one node instead of forty episodes) and the deviation LOUD
(surprise scores against the same distribution the schema came from).
Idempotent: support accrues per episode; the node id is stable per cell.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

from tracemem.domain import (
    Attribute, Confidence, Grade, Node, NodeKind, node_id,
)
from tracemem.nodes.accrual import accrue
from tracemem.prediction.model import slot_of, weekday_of

MIN_SUPPORT = 4

# A weekly rhythm ('Mondays at the bakery') is only claimed when a specific
# weekday carries the MAJORITY of a (slot, place) cell's occurrences —
# otherwise the routine is a daily one blurred across the week, and calling it
# a 'Monday' thing would be a confident-wrong. Concentration guards that.
WEEKLY_CONCENTRATION = 0.6

_WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                  "Saturday", "Sunday")


def _dominant_topic(episode_ids, graph_store) -> Optional[str]:
    counts: Counter = Counter()
    for node in graph_store.nodes.values():
        if node.kind != NodeKind.TOPIC:
            continue
        for a in node.attributes:
            hits = len(set(a.source_episode_ids) & set(episode_ids))
            if hits:
                counts[a.value] += hits
    if not counts:
        return None
    value, n = counts.most_common(1)[0]
    return value if n >= max(2, len(episode_ids) // 2) else None


def _crystallize(graph_store, sid: str, place: str, routine_text: str,
                 eps, t_ms: int) -> bool:
    """Create or reinforce one schema node from a cell. Returns True if it
    crystallized (the place it lives at must be a real node)."""
    place_node = graph_store.get_node(node_id("place", place.lower()))
    if place_node is None:
        return False                       # a schema must live somewhere real
    schema = graph_store.get_node(sid)
    if schema is None:
        from tracemem import __version__ as _v
        schema = Node(id=sid, kind=NodeKind.PATTERN, derivation_depth=1,
                      parent_ids=(place_node.id,), builder_version=_v)
    ep_ids = [e.id for e in eps]
    for ep_id in ep_ids:
        accrue(schema, "routine", routine_text, Grade.DERIVED,
               Confidence(min(0.95, 0.5 + 0.1 * len(ep_ids))), ep_id, t_ms)
    topic = _dominant_topic(ep_ids, graph_store)
    if topic:
        for ep_id in ep_ids:
            accrue(schema, "usually", topic, Grade.DERIVED,
                   Confidence(0.7), ep_id, t_ms)
    schema.activation = min(1.0, schema.activation + 0.2)
    schema.last_active_t_ms = t_ms
    graph_store.upsert_node(schema)
    return True


def schema_pass(episodes, graph_store, t_ms: int,
                min_support: int = MIN_SUPPORT,
                changed_episode_ids=None) -> List[str]:
    """Returns ids of schema nodes created or reinforced this pass.

    Two grains, both deterministic (zero LLM), both citing their episodes:
      · DAILY  — a (slot, place) cell recurring regardless of weekday
                 ('around 13:00 you are usually at the office').
      · WEEKLY — a (weekday, slot, place) cell that recurs AND is concentrated
                 on that weekday ('on Mondays around 18:00 you are usually at
                 the bakery') — the calendar the vision asks for. Concentration
                 keeps a blurred daily habit from masquerading as a Monday one.
    """
    daily: Dict[Tuple[int, Optional[str]], List] = defaultdict(list)
    weekly: Dict[Tuple[int, int, Optional[str]], List] = defaultdict(list)
    for ep in episodes:
        slot = slot_of(ep.t_range.start_ms)
        daily[(slot, ep.place_key)].append(ep)
        weekly[(weekday_of(ep.t_range.start_ms), slot, ep.place_key)].append(ep)

    changed = (None if changed_episode_ids is None
               else set(changed_episode_ids))
    touched: List[str] = []
    for (slot, place), eps in daily.items():
        if changed is not None and not any(e.id in changed for e in eps):
            continue
        if place is None or len(eps) < min_support:
            continue
        sid = node_id("schema", "%d|%s" % (slot, place.lower()))
        routine = "around %02d:00 you are usually at %s" % (slot, place)
        if _crystallize(graph_store, sid, place, routine, eps, t_ms):
            touched.append(sid)

    for (wd, slot, place), eps in weekly.items():
        if changed is not None and not any(e.id in changed for e in eps):
            continue
        if place is None or len(eps) < min_support:
            continue
        total = len(daily[(slot, place)])
        if total == 0 or len(eps) / total < WEEKLY_CONCENTRATION:
            continue                       # blurred across weekdays — not weekly
        sid = node_id("schema", "%d|%d|%s" % (wd, slot, place.lower()))
        routine = "on %ss around %02d:00 you are usually at %s" % (
            _WEEKDAY_NAMES[wd], slot, place)
        if _crystallize(graph_store, sid, place, routine, eps, t_ms):
            touched.append(sid)
    return touched
