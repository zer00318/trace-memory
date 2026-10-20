"""ORACLE for Boundary constants / Flicker window rule / Alt-tab storm test /
Day boundaries & sleep (04_binder).
"""
import re
from pathlib import Path

from tracemem.binder import boundary_constants as K
from tracemem.binder.event_segmenter import (
    boundaries, collapse_flicker, day_boundaries, day_index, longest_gap,
    switch_times,
)

S = 1_000
MIN = 60 * S
HOUR = 60 * MIN


# -- Boundary constants: the atom's PROOF ----------------------------------
def test_no_inline_gap_literals_left_in_the_segmenter():
    """PROOF: grep finds no inline gap-number literals in the segmenter."""
    src = (Path(__file__).resolve().parents[2] / "src" / "tracemem" /
           "binder" / "event_segmenter.py").read_text()
    body = "\n".join(l for l in src.splitlines()
                     if not l.lstrip().startswith("#"))
    body = re.sub(r'""".*?"""', "", body, flags=re.S)      # docstrings are prose
    # Any arithmetic building a duration (60 * 1000, 30 * 60 * 1000, 5_000)
    offenders = re.findall(r"\b\d[\d_]*\s*\*\s*\d[\d_]*|\b\d{4,}\b", body)
    assert not offenders, f"inline duration literals in the segmenter: {offenders}"


def test_every_constant_is_importable_from_one_place():
    for name in ("MAX_EPISODE_MS", "FLICKER_WINDOW_MS", "MIN_SLEEP_GAP_MS",
                 "MIN_DAY_MS"):
        assert isinstance(getattr(K, name), int)


def test_the_constants_declare_themselves_uncalibrated():
    """A tuned-LOOKING constant with no provenance is the failure mode."""
    text = (Path(K.__file__)).read_text()
    assert text.count("UNCALIBRATED") >= 4


def test_behaviour_is_unchanged_by_the_extraction():
    """The atom says: preparation only, no behaviour change."""
    assert boundaries([600_000], 0, 5_400_000, max_episode_ms=1_800_000) == \
        [600_000, 2_400_000, 4_200_000, 5_400_000]


# -- Flicker window rule: the atom's PROOF ---------------------------------
def test_a_three_second_detour_does_not_end_the_moment():
    """PROOF verbatim: A(60s) → B(3s) → A(60s) yields ONE moment of A."""
    switches = [(0, "A"), (60 * S, "B"), (63 * S, "A")]
    collapsed = collapse_flicker(switches, flicker_window_ms=5 * S)
    assert collapsed == [(0, "A")]
    assert len(boundaries(switch_times(collapsed), 0, 123 * S)) == 1


def test_a_long_excursion_is_a_real_moment():
    switches = [(0, "A"), (60 * S, "B"), (120 * S, "A")]
    assert collapse_flicker(switches, flicker_window_ms=5 * S) == switches


def test_a_brief_pass_through_is_not_collapsed():
    """A→B(3s)→C never returned to A. Collapsing it would glue two
    unrelated activities into one moment — silently."""
    switches = [(0, "A"), (60 * S, "B"), (63 * S, "C")]
    assert collapse_flicker(switches, flicker_window_ms=5 * S) == switches


def test_repeated_flicker_collapses_to_one_moment():
    """A B A B A with 1s hops is one moment, not five. Needs the fixpoint
    iteration: collapsing one excursion exposes the next."""
    switches = [(0, "A"), (30 * S, "B"), (31 * S, "A"),
                (60 * S, "B"), (61 * S, "A")]
    assert collapse_flicker(switches, flicker_window_ms=5 * S) == [(0, "A")]


def test_the_window_boundary_is_exclusive():
    exactly = [(0, "A"), (10 * S, "B"), (15 * S, "A")]     # 5s excursion
    assert collapse_flicker(exactly, flicker_window_ms=5 * S) == exactly


def test_short_lists_pass_through_untouched():
    assert collapse_flicker([], 5 * S) == []
    assert collapse_flicker([(0, "A")], 5 * S) == [(0, "A")]
    assert collapse_flicker([(0, "A"), (1, "B")], 5 * S) == [(0, "A"), (1, "B")]


# -- Alt-tab storm: the adversarial fixture --------------------------------
def test_alt_tab_storm_holds_steady():
    """PROOF: 50 context switches in 2 minutes → ≤5 moments, and zero
    sub-second confetti moments."""
    switches = [(0, "work")]
    t = 5 * S
    for i in range(50):                     # 50 switches, mixed short durations
        switches.append((t, f"app{i % 4}" if i % 2 else "work"))
        t += 800 + (i % 3) * 400            # 0.8-1.6s apart, all inside the window
    assert t <= 125 * S, "fixture must stay inside ~2 minutes"

    collapsed = collapse_flicker(switches, flicker_window_ms=5 * S)
    bounds = boundaries(switch_times(collapsed), 0, t)
    assert len(bounds) <= 5, f"storm shattered into {len(bounds)} moments"

    durations = [b - a for a, b in zip([0] + bounds, bounds)]
    assert not [d for d in durations if 0 < d < S], "sub-second confetti moments"


def test_a_storm_that_settles_keeps_the_real_change():
    """Hysteresis must not swallow the moment the storm resolves into."""
    switches = [(0, "work")]
    t = 5 * S
    for i in range(20):
        switches.append((t, "chat" if i % 2 else "work"))
        t += S
    switches.append((t, "email"))                    # the real change
    switches.append((t + 30 * MIN, "work"))
    collapsed = collapse_flicker(switches, flicker_window_ms=5 * S)
    assert any(c == "email" for _t, c in collapsed)


# -- Day boundaries & sleep: the atom's PROOF ------------------------------
def _day(start_h, hours):
    return [int(start_h * HOUR + h * HOUR) for h in hours]


def test_a_2am_session_belongs_to_the_previous_day():
    """PROOF verbatim. Midnight appears nowhere in the logic — the day
    ends at the sleep, so 2am work is still Tuesday's."""
    # Tuesday: work 09:00-23:00, then a 2am session, then sleep 03:00-11:00.
    tuesday = _day(0, [9, 12, 15, 18, 21, 23])
    small_hours = _day(0, [26, 27])                 # 02:00, 03:00 next morning
    wednesday = _day(0, [35, 38, 41])               # 11:00 onward
    ts = tuesday + small_hours + wednesday

    bounds = day_boundaries(ts)
    assert len(bounds) == 1, f"expected one day boundary, got {bounds}"
    assert bounds[0] == 27 * HOUR                   # the 8h sleep gap starts here

    assert day_index(26 * HOUR, bounds) == 0        # 2am work: previous day
    assert day_index(23 * HOUR, bounds) == 0        # so is the evening before it
    assert day_index(35 * HOUR, bounds) == 1        # and the morning after is not


def test_midnight_is_not_a_boundary():
    ts = _day(0, [20, 22, 23, 24, 25, 26])          # working straight through
    assert day_boundaries(ts) == []


def test_a_lunch_break_does_not_end_a_day():
    ts = _day(0, [9, 10, 11, 14, 15, 16])           # a 3h gap, under the floor
    assert day_boundaries(ts) == []


def test_one_restless_night_does_not_shatter_a_day():
    """Two long gaps six hours apart is one night, not two days."""
    ts = [0, 5 * HOUR, 10 * HOUR, 11 * HOUR, 16 * HOUR]
    bounds = day_boundaries(ts, min_sleep_gap_ms=4 * HOUR, min_day_ms=12 * HOUR)
    assert len(bounds) == 1


def test_several_real_days_each_get_a_boundary():
    ts = []
    for d in range(3):
        ts += [d * 24 * HOUR + h * HOUR for h in (9, 12, 15, 18)]
    bounds = day_boundaries(ts)
    assert len(bounds) == 2
    assert [day_index(t, bounds) for t in ts] == [0] * 4 + [1] * 4 + [2] * 4


def test_a_timestamp_at_the_boundary_belongs_to_the_day_that_ended():
    bounds = [10 * HOUR]
    assert day_index(10 * HOUR, bounds) == 0
    assert day_index(10 * HOUR + 1, bounds) == 1


def test_too_little_data_claims_no_boundary():
    assert day_boundaries([]) == []
    assert day_boundaries([5]) == []


def test_longest_gap_reports_where_the_quiet_was():
    assert longest_gap([0, HOUR, 10 * HOUR]) == (HOUR, 9 * HOUR)
    assert longest_gap([5]) is None


# -- RE-AUDIT 2026-07-21 findings on the Space tracker ---------------------
def test_a_small_space_losing_one_window_keeps_its_id():
    """The 0.6 threshold over-split badly: a two-window Space losing one
    scores 1/3, so every churn minted a new id (measured: 12 ids for 2
    Spaces). Small Spaces are the common case."""
    from tracemem.capture.focus_context import SpaceTracker
    from tracemem.capture.window_stack import WindowInfo

    def w(i):
        return WindowInfo(window_id=i, owner="A", pid=i, title="t", layer=0,
                          rect=(0, 0, 10, 10), z=i)

    t = SpaceTracker()
    a = t.observe([w(1), w(2)])
    assert t.observe([w(1), w(3)]) == a
    assert t.observe([w(1), w(4)]) == a


def test_two_alternating_spaces_do_not_multiply_ids():
    from tracemem.capture.focus_context import SpaceTracker
    from tracemem.capture.window_stack import WindowInfo

    def w(i):
        return WindowInfo(window_id=i, owner="A", pid=i, title="t", layer=0,
                          rect=(0, 0, 10, 10), z=i)

    t = SpaceTracker()
    ids = set()
    for round_ in range(5):
        ids.add(t.observe([w(1), w(2), w(100 + round_)]))     # Space A + churn
        ids.add(t.observe([w(50), w(51), w(200 + round_)]))   # Space B + churn
    assert len(ids) == 2, f"2 Spaces produced {len(ids)} ids"


def test_the_subset_bug_stays_fixed_at_the_new_threshold():
    from tracemem.capture.focus_context import SpaceTracker
    from tracemem.capture.window_stack import WindowInfo

    def w(i):
        return WindowInfo(window_id=i, owner="A", pid=i, title="t", layer=0,
                          rect=(0, 0, 10, 10), z=i)

    t = SpaceTracker()
    a = t.observe([w(1), w(2), w(3), w(4), w(5)])
    assert t.observe([w(1)]) != a


def test_remembered_spaces_are_bounded():
    """Unbounded growth leaked for the life of the process and made
    observe() an O(n) scan on a 2-second poll."""
    from tracemem.capture.focus_context import (
        MAX_REMEMBERED_SPACES, SpaceTracker,
    )
    from tracemem.capture.window_stack import WindowInfo

    t = SpaceTracker()
    for i in range(MAX_REMEMBERED_SPACES * 3):
        t.observe([WindowInfo(window_id=1000 * i + j, owner="A", pid=1,
                              title="t", layer=0, rect=(0, 0, 10, 10), z=j)
                   for j in range(3)])
    live = [s for s in t._spaces if s]
    assert len(live) <= MAX_REMEMBERED_SPACES
