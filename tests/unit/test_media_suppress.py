"""Video suppression: relentless motion mutes once; stillness re-arms; work never mutes."""
from __future__ import annotations

from tracemem.extraction.media_suppress import MediaSuppressor


def test_video_mutes_exactly_once_and_stays_muted():
    m = MediaSuppressor(constant_beats=5, still_beats=3)
    events = [m.observe("vid", True, t) for t in range(20)]
    fired = [e for e in events if e]
    assert len(fired) == 1 and fired[0].kind == "muted" and fired[0].t_ms == 4
    assert m.is_muted("vid")


def test_pause_then_stillness_unmutes_once():
    m = MediaSuppressor(constant_beats=5, still_beats=3)
    for t in range(6):
        m.observe("vid", True, t)
    events = [m.observe("vid", False, 10 + t) for t in range(5)]
    fired = [e for e in events if e]
    assert len(fired) == 1 and fired[0].kind == "unmuted"
    assert not m.is_muted("vid")


def test_sporadic_work_changes_never_mute():
    m = MediaSuppressor(constant_beats=5, still_beats=3)
    pattern = [True, True, False, True, False, True, True, True, False] * 3
    assert all(m.observe("editor", c, t) is None for t, c in enumerate(pattern))
    assert not m.is_muted("editor")


def test_regions_are_independent():
    m = MediaSuppressor(constant_beats=3, still_beats=2)
    for t in range(4):
        m.observe("vid", True, t)
        m.observe("editor", t % 2 == 0, t)
    assert m.is_muted("vid") and not m.is_muted("editor")


def test_filter_changes_serves_the_beat():
    m = MediaSuppressor(constant_beats=3, still_beats=2)
    for t in range(5):
        allowed = m.filter_changes([("vid", t), ("doc", t)] if t % 2 == 0
                                   else [("vid", t)])
    assert "vid" not in allowed
    assert not m.is_muted("doc")
