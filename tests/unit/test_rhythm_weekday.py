"""The weekday rhythm — the calendar the vision asks for (analysis gap #4).

A (slot, place) cell can only ever say 'around 18:00'; adding the weekday axis
lets 'on Mondays around 18:00 at the bakery' crystallize — deterministically,
citing its episodes, with a concentration guard so a daily habit is never
mislabeled as a weekday one.
"""
from __future__ import annotations

from tracemem.domain import Node, NodeKind, node_id
from tracemem.domain.episode import Episode
from tracemem.domain.time import TimeRange
from tracemem.nodes.accrual import values_of
from tracemem.nodes.graph_store import MemoryGraphStore
from tracemem.nodes.marination.schemas import schema_pass
from tracemem.prediction.model import weekday_of

MS_H = 3_600_000
MS_DAY = 86_400_000
FIRST_MONDAY_DAY = 4          # 1970-01-05 was a Monday (epoch day 0 = Thursday)


def _episode(i, day, slot_h, place):
    start = day * MS_DAY + slot_h * MS_H
    return Episode(id="ep-%d" % i, t_range=TimeRange(start, start + 1000),
                   place_key=place)


def _routines(graph_store, touched):
    out = []
    for sid in touched:
        out += values_of(graph_store.get_node(sid), "routine")
    return out


def test_weekday_helper_marks_mondays():
    assert weekday_of(FIRST_MONDAY_DAY * MS_DAY) == 0            # Monday
    assert weekday_of((FIRST_MONDAY_DAY + 1) * MS_DAY) == 1      # Tuesday
    assert weekday_of((FIRST_MONDAY_DAY + 6) * MS_DAY) == 6      # Sunday


def test_four_mondays_at_the_bakery_crystallize_a_weekly_rhythm():
    g = MemoryGraphStore()
    g.upsert_node(Node(id=node_id("place", "wifi:bakery"), kind=NodeKind.PLACE))
    eps = [_episode(w, FIRST_MONDAY_DAY + 7 * w, 18, "wifi:bakery")
           for w in range(4)]                                   # four Mondays, 18:00
    touched = schema_pass(eps, g, t_ms=99 * MS_DAY)
    routines = _routines(g, touched)
    assert any("on Mondays around 18:00" in r and "wifi:bakery" in r
               for r in routines), routines


def test_a_daily_habit_is_not_mislabeled_as_a_weekday_rhythm():
    """Same slot+place across four DIFFERENT weekdays is a DAILY rhythm; the
    concentration guard must refuse to call it a Monday (or any weekday) thing."""
    g = MemoryGraphStore()
    g.upsert_node(Node(id=node_id("place", "wifi:office"), kind=NodeKind.PLACE))
    eps = [_episode(i, FIRST_MONDAY_DAY + i, 13, "wifi:office")
           for i in range(4)]                                   # Mon..Thu, 13:00
    touched = schema_pass(eps, g, t_ms=99 * MS_DAY)
    routines = _routines(g, touched)
    assert any("around 13:00 you are usually at wifi:office" in r
               for r in routines)
    assert not any(r.startswith("on ") for r in routines), routines
