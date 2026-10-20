"""Detecting that the screen is not only yours.

`Screen stream / Display changes / Projector / AirPlay`. macOS mirrors by
giving two displays the same origin and size — they occupy one rectangle of
the desktop because they are painting the same thing. Detectable from the
display list alone.

IT DECIDES NOTHING. The founder already ruled on what happens while a screen
is shared: CAPTURE EVERYTHING (board ruling ii). This reports a fact; putting
the decision here would quietly relitigate a ruling inside a helper.

It is still worth knowing. "You were mirroring to something" is usually a
meeting, a lecture or a demo, and a memory that cannot tell a private
afternoon from a presented one is missing the thing a person would say first
when describing their day.
"""
from __future__ import annotations

from tracemem.capture.displays import (DisplayIdentity, is_mirroring,
                                       mirrored_groups, mirroring_event)


def _d(key, x=0.0, y=0.0, w=1512.0, h=982.0, builtin=True):
    return DisplayIdentity(key=key, vendor=1, model=2, serial=3,
                           builtin=builtin, points_w=w, points_h=h,
                           pixels_w=int(w * 2), pixels_h=int(h * 2),
                           display_id=1, origin_x=x, origin_y=y)


def test_one_display_is_never_mirroring():
    assert mirrored_groups([_d("a")]) == []
    assert is_mirroring([_d("a")]) is False
    assert mirroring_event([_d("a")]) is None


def test_no_displays_is_not_mirroring():
    """A sleeping Mac reports zero displays. That is not a projector."""
    assert is_mirroring([]) is False
    assert mirroring_event([]) is None


def test_two_panels_on_the_same_rectangle_are_mirrored():
    got = mirrored_groups([_d("laptop"), _d("projector", builtin=False)])
    assert got == [["laptop", "projector"]]
    assert is_mirroring([_d("laptop"), _d("projector", builtin=False)]) is True


def test_a_second_display_beside_the_first_is_not_mirroring():
    """THE CASE THIS MUST NOT GET WRONG. An ordinary two-monitor desk is not
    a presentation, and calling it one would mark every working day as
    'shared'."""
    side_by_side = [_d("laptop"), _d("desk", x=1512.0, builtin=False)]
    assert mirrored_groups(side_by_side) == []
    assert mirroring_event(side_by_side) is None


def test_a_negotiated_resolution_is_still_the_same_rectangle():
    """A projector reporting 1279.998 points is showing the same rectangle as
    one reporting 1280. Floating-point equality would call that private."""
    pair = [_d("laptop", w=1280.0, h=800.0),
            _d("beamer", w=1279.998, h=800.0004, builtin=False)]
    assert mirrored_groups(pair) == [["beamer", "laptop"]]


def test_the_event_names_what_it_is_mirrored_to():
    ev = mirroring_event([_d("laptop"), _d("projector", builtin=False)])
    assert ev["mirroring"] is True
    assert ev["to_external"] == ["projector"]
    assert "1 other panel" in ev["detail"]


def test_three_panels_showing_one_thing_are_one_group():
    ev = mirroring_event([_d("laptop"),
                          _d("beamer", builtin=False),
                          _d("tv", builtin=False)])
    assert ev["groups"] == [["beamer", "laptop", "tv"]]
    assert "2 other panel" in ev["detail"]


def test_two_separate_mirror_groups_are_both_reported():
    displays = [_d("a"), _d("b", builtin=False),
                _d("c", x=2000.0, builtin=False),
                _d("d", x=2000.0, builtin=False)]
    assert mirrored_groups(displays) == [["a", "b"], ["c", "d"]]


def test_absence_is_not_an_event():
    """An event stream that reports the absence of things is one nobody can
    read."""
    assert mirroring_event([_d("a"), _d("b", x=1512.0, builtin=False)]) is None
