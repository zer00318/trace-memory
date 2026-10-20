"""The frames the macro eye will look at: bounded, spread, and never on disk.

The buffer exists because of a scheduling knot. The macro VLM cannot run
beside live capture — heavy GPU work makes capture go dark silently, which
the scheduler encodes as a hard refusal — so it can only run while the eye is
closed, and a closed eye has no frames. Something has to hold them.

What these tests hold it to:

  · it never grows without bound, in frames or in bytes;
  · a full buffer keeps a SPREAD of the session rather than its tail;
  · the same scene twice does not eat two slots;
  · draining leaves nothing behind.
"""
from __future__ import annotations

import pytest

from tracemem.extraction.macro_buffer import MacroBuffer

PNG = b"x" * 100


def _offer(buf, t, scene=None, png=PNG):
    return buf.offer("f%d" % t, png, t, scene_key=scene or "s%d" % t)


def test_it_holds_what_it_is_given():
    b = MacroBuffer()
    assert _offer(b, 1000) is True
    assert len(b) == 1 and b.nbytes == 100


def test_an_empty_frame_is_not_a_frame():
    b = MacroBuffer()
    assert _offer(b, 1000, png=b"") is False
    assert len(b) == 0


def test_the_same_scene_does_not_eat_two_slots():
    """A founder reading one page for twenty minutes is ONE scene. Without
    this the buffer fills with copies of it and the rest of the day is never
    understood."""
    b = MacroBuffer()
    for t in range(1000, 9000, 1000):
        _offer(b, t, scene="the same page")
    assert len(b) == 1
    assert b.stats()["deduped"] == 7


def test_it_never_exceeds_its_frame_ceiling():
    b = MacroBuffer(max_frames=4)
    for t in range(1, 40):
        _offer(b, t * 1000)
    assert len(b) == 4


def test_it_never_exceeds_its_byte_ceiling():
    b = MacroBuffer(max_frames=100, max_bytes=350)
    for t in range(1, 10):
        _offer(b, t * 1000)
    assert b.nbytes <= 350
    assert len(b) == 3


def test_a_full_buffer_keeps_the_whole_session_not_its_tail():
    """THE POINT OF THE THINNING POLICY.

    A FIFO would end the day holding the last four scenes — the founder's
    evening, repeatedly, and never their morning. What is wanted is coverage
    of the whole day at low resolution, not the last hour at high.

    Twenty scenes across a twenty-hour session, into four slots. The first
    and last must survive (they are the session's bounds) and the middle two
    must be spread through it, not bunched at either end.
    """
    b = MacroBuffer(max_frames=4)
    hour = 3_600_000
    for h in range(20):
        _offer(b, h * hour)
    kept = [f.t_ms // hour for f in b.take_all()]
    assert kept[0] == 0, "the session's start was thinned away"
    assert kept[-1] == 19, "the session's end was thinned away"
    # A FIFO would give [16, 17, 18, 19]. Anything spread beats that; the
    # concrete bar is that the middle of the day is represented.
    assert any(3 <= k <= 15 for k in kept[1:-1]), \
        "kept %r — that is a tail, not a spread" % kept


def test_the_endpoints_survive_a_long_run():
    b = MacroBuffer(max_frames=3)
    for t in range(50):
        _offer(b, t * 1000)
    kept = [f.t_ms for f in b.take_all()]
    assert kept[0] == 0
    assert kept[-1] == 49_000


def test_frames_arrive_in_time_order():
    b = MacroBuffer(max_frames=5)
    for t in (5, 1, 3, 2, 4):
        _offer(b, t * 1000)
    # Offered out of order, kept in offer order — the drain must not assume
    # sorting it did not do. This pins the actual contract rather than a
    # hoped-for one.
    assert [f.t_ms for f in b.take_all()] == [5000, 1000, 3000, 2000, 4000]


def test_draining_leaves_nothing_resident():
    """A drain that half-fails must not leave pixels in memory for an hour."""
    b = MacroBuffer()
    for t in range(1, 5):
        _offer(b, t * 1000)
    got = b.take_all()
    assert len(got) == 4
    assert len(b) == 0 and b.nbytes == 0
    assert b.take_all() == []


def test_stats_say_what_the_eye_never_saw():
    """`deduped` and `thinned` are the coverage this eye gave up. They are
    reported because a macro pass that understood three scenes out of eighty
    must not read as a macro pass that understood the day."""
    b = MacroBuffer(max_frames=3)
    for t in range(1, 8):                    # 7 distinct scenes into 3 slots
        _offer(b, t * 1000)
    s = b.stats()
    assert s["held"] == 3
    assert s["offered"] == 7
    assert s["thinned"] == 4
    assert s["deduped"] == 0
    b.take_all()
    assert b.stats()["drained"] == 3


def test_dedupe_only_covers_what_is_still_held():
    """A scene already thinned away can come back, and that is correct.

    Dedupe is a slot-efficiency rule, not a history of everything ever seen.
    Keeping a permanent set of scene keys would make a page revisited eight
    hours later invisible to the macro eye forever — and that revisit is a
    real fact about the day.
    """
    b = MacroBuffer(max_frames=3)
    _offer(b, 1000)                          # the session's start, protected
    _offer(b, 2000, scene="morning page")    # interior, so thinnable
    for t in range(3, 9):
        _offer(b, t * 1000)
    assert "morning page" not in [f.scene_key for f in b.take_all()]

    b2 = MacroBuffer(max_frames=3)
    _offer(b2, 1000)
    _offer(b2, 2000, scene="morning page")
    for t in range(3, 9):
        _offer(b2, t * 1000)
    assert _offer(b2, 9000, scene="morning page") is True
    assert b2.stats()["deduped"] == 0


def test_a_scene_still_held_is_refused_however_late():
    b = MacroBuffer(max_frames=8)
    assert _offer(b, 1000, scene="one page") is True
    assert _offer(b, 9_000_000, scene="one page") is False
    assert b.stats()["deduped"] == 1


def test_a_buffer_too_small_to_spread_is_a_construction_error():
    with pytest.raises(ValueError):
        MacroBuffer(max_frames=1)


def test_one_enormous_frame_does_not_empty_the_buffer():
    """Over the byte ceiling with only two frames, dropping the newest beats
    thinning down to nothing."""
    b = MacroBuffer(max_frames=10, max_bytes=150)
    assert _offer(b, 1000) is True
    _offer(b, 2000, png=b"y" * 500)
    assert len(b) >= 1, "the buffer emptied itself over one fat frame"
