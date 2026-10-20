"""Spec 01 §2 oracles — the flow-control valve."""
from tracemem.extraction.change_gate import (
    FORCE_READ_MS, MEDIA_STREAK_N, Verdict, decide_read,
)


def frame(val):
    return [[val] * 64 for _ in range(40)]


def half_changed(a, b):
    f = frame(a)
    for row in f[:20]:
        for x in range(len(row)):
            row[x] = b
    return f


def test_first_frame_reads():
    assert decide_read(frame(0), None, 0, 0, 0).verdict is Verdict.READ

def test_static_skips():
    assert decide_read(frame(10), frame(10), 0, 1000, 0).verdict is Verdict.SKIP

def test_static_force_reads_after_window():
    r = decide_read(frame(10), frame(10), 0, FORCE_READ_MS + 1, 0)
    assert r.verdict is Verdict.READ

def test_real_change_reads():
    r = decide_read(half_changed(0, 200), frame(0), 0, 1000, 0)
    assert r.verdict is Verdict.READ and r.changed_frac > 0.15

def test_media_demotion_after_streak():
    streak = 0
    for _ in range(MEDIA_STREAK_N):
        r = decide_read(half_changed(0, 200), frame(0), 0, 1000, streak)
        streak = r.media_streak
    assert r.verdict is Verdict.MEDIA

def test_gate_fails_open_on_garbage():
    assert decide_read(None, frame(0), 0, 0, 0).verdict is Verdict.READ  # type: ignore[arg-type]
