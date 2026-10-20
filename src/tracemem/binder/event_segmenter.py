"""OWNS: episode boundaries = scene-changes + forced max-duration cuts
(spec 03 §2), plus the two rules that decide when a change is REAL:
flicker hysteresis (Flicker window rule atom) and the sleep-derived day
boundary (Day boundaries & sleep atom).

Every threshold lives in boundary_constants.py — none inline here. That
separation is the Boundary constants atom, and it is what makes the later
calibration pass a one-file edit.
"""
from __future__ import annotations

from bisect import bisect_right
from typing import List, Optional, Sequence, Tuple

from tracemem.binder.boundary_constants import (
    FLICKER_WINDOW_MS, MAX_EPISODE_MS, MIN_DAY_MS, MIN_SLEEP_GAP_MS,
)

__all__ = ["boundaries", "boundaries_with_gaps", "epoch_index",
           "collapse_flicker", "day_boundaries", "day_index", "MAX_EPISODE_MS"]


def boundaries(scene_changes: Sequence[int], t_start_ms: int, t_end_ms: int,
               max_episode_ms: int = MAX_EPISODE_MS) -> List[int]:
    out: List[int] = []
    prev = t_start_ms
    for t in sorted(set(list(scene_changes) + [t_end_ms])):
        if t <= prev:
            continue
        while t - prev > max_episode_ms:
            prev += max_episode_ms
            out.append(prev)
        out.append(t)
        prev = t
    return out


def boundaries_with_gaps(scene_changes: Sequence[int],
                         attention_gaps: Sequence[Tuple[int, int]],
                         t_start_ms: int, t_end_ms: int,
                         max_episode_ms: int = MAX_EPISODE_MS) -> List[int]:
    """Boundaries that also break at every attention gap.

    A moment must not span an empty chair (Away witness atom) or a shut
    screen (Closed-eyes witness). Without this, an absence is invisible to
    segmentation: the claims before lunch and the claims after it land in
    ONE moment, and the resulting episode says a person did two unrelated
    things continuously for two hours. Both edges of the gap are cuts —
    the moment ends when they leave and a new one starts when they return,
    with the gap itself owning the middle.

    Gaps are expected pre-merged (capture.presence.attention_gaps does it),
    because an away witness and a closed-eyes witness routinely cover the
    same absence and would otherwise cut twice.
    """
    cuts = list(scene_changes)
    for t1, t2 in attention_gaps:
        cuts.append(int(t1))
        if t2 is not None:
            cuts.append(int(t2))
    return boundaries(cuts, t_start_ms, t_end_ms, max_episode_ms)


def epoch_index(t_ms: int, bounds: Sequence[int]) -> int:
    """Which activity-epoch a timestamp falls in. Pure fn of (t, bounds) —
    late claims land correctly regardless of arrival order (B3)."""
    # ``bounds`` is already a Sequence. Copying it here made a life-scale
    # reconciliation copy ~1,000,000 integers once per claim.
    return bisect_right(bounds, t_ms)


# -- the flicker window rule ------------------------------------------------
def collapse_flicker(switches: Sequence[Tuple[int, object]],
                     flicker_window_ms: int = FLICKER_WINDOW_MS
                     ) -> List[Tuple[int, object]]:
    """Drop context switches that were a detour, not a change of activity.

    Input: (t_ms, context) switch points, in time order. Output: the same
    list with brief round-trips removed, so the segmenter sees ONE moment
    where a naive reading would see three.

    THE RULE, precisely: an excursion collapses only when it both (a) lasts
    less than the window and (b) RETURNS to the context it left. Duration
    alone is not enough — A→B(3s)→C is a genuine change of activity that
    merely passed through B, and collapsing it would silently glue two
    unrelated moments together. Requiring the return is what keeps this a
    hysteresis rule instead of a short-event filter.

    Iterated to a fixpoint, because collapsing one excursion can expose
    another: A B A B A with 1s hops is one moment of A, not three.

    The detour's claims are NOT dropped — this function only removes
    BOUNDARIES. Those claims land inside the moment they interrupted,
    which is what the brief asks for and what makes the moment honest:
    something did happen, it just did not end anything.
    """
    items = [(int(t), c) for t, c in switches]
    if len(items) < 3:
        return list(items)

    changed = True
    while changed and len(items) >= 3:
        changed = False
        for i in range(1, len(items) - 1):
            (t_prev, c_prev), (t_exc, _c_exc), (t_ret, c_ret) = \
                items[i - 1], items[i], items[i + 1]
            if c_ret != c_prev:
                continue                          # never returned: a real change
            if t_ret - t_exc >= flicker_window_ms:
                continue                          # long enough to be its own moment
            del items[i:i + 2]                    # drop the excursion AND the return
            changed = True
            break
    return items


def switch_times(switches: Sequence[Tuple[int, object]]) -> List[int]:
    """The timestamps a switch list implies, for feeding boundaries()."""
    return [int(t) for t, _c in switches]


# -- day boundaries from lived rhythm, never from midnight -----------------
def day_boundaries(timestamps: Sequence[int],
                   min_sleep_gap_ms: int = MIN_SLEEP_GAP_MS,
                   min_day_ms: int = MIN_DAY_MS) -> List[int]:
    """Where the founder's days actually end: at the long quiet gaps.

    Midnight appears nowhere in this function, on purpose. A 2am work
    session belongs to the day that produced it — the day that has not
    ended yet, because the person has not slept. Calendar days would cut
    that session away from its own afternoon and file it under tomorrow,
    which is wrong in the only way that matters: it would answer "what did
    I do Tuesday" by hiding Tuesday night's work under Wednesday.

    The boundary is placed at the START of a qualifying gap — the moment
    activity stopped. Greedy longest-gap-first with a minimum spacing, so
    one restless night cannot shatter a day into fragments.
    """
    ts = sorted(set(int(t) for t in timestamps))
    if len(ts) < 2:
        return []

    gaps = [(ts[i + 1] - ts[i], ts[i]) for i in range(len(ts) - 1)]
    gaps = [(size, start) for size, start in gaps if size >= min_sleep_gap_ms]
    gaps.sort(key=lambda g: (-g[0], g[1]))        # longest first, ties by time

    chosen: List[int] = []
    for _size, start in gaps:
        if all(abs(start - c) >= min_day_ms for c in chosen):
            chosen.append(start)
    return sorted(chosen)


def day_index(t_ms: int, day_bounds: Sequence[int]) -> int:
    """Which lived day a timestamp belongs to. 0 = the first day present.

    A timestamp exactly AT a boundary belongs to the day that is ending —
    the boundary marks when activity stopped, so that last observation is
    part of what came before it, not of the sleep that follows.
    """
    return bisect_right(list(day_bounds), int(t_ms) - 1)


def longest_gap(timestamps: Sequence[int]) -> Optional[Tuple[int, int]]:
    """(start_ms, size_ms) of the largest inactivity gap, or None."""
    ts = sorted(set(int(t) for t in timestamps))
    if len(ts) < 2:
        return None
    size, start = max((ts[i + 1] - ts[i], ts[i]) for i in range(len(ts) - 1))
    return (start, size)
