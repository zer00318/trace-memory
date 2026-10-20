"""A stream that dies quietly must not report itself alive.

THE SHAPE OF THE SIXTEEN-DAY BLACKOUT. `EyeOrgan.alive()` was
`self._session is not None` — which answers "did start() return", and
nothing whatever about whether this eye is still SEEING.

The SCStream is created with `delegate=None`, so when macOS stops it — a
display change, a revoked permission, a driver hiccup — there is no callback
and no exception. The session object sits there. `alive()` keeps saying True.
The supervisor, which already knows how to witness a dead organ and restart
it under backoff, is never told there is anything to do. And the eye is dark.

`tools/blind_alarm.py` exists because this could only be detected from
OUTSIDE the process. A frame clock detects it from inside, and it catches
every cause because it never asks WHY the frames stopped.
"""
from __future__ import annotations

from tracemem.app.eye_organ import EyeOrgan


class Clock:
    def __init__(self):
        self.t = 1_700_000_000_000

    def __call__(self):
        return self.t

    def advance(self, ms):
        self.t += ms


class Record:
    def __init__(self, t_ms):
        self.t_ms = t_ms
        self.frame = None


def _organ(clock):
    o = EyeOrgan.__new__(EyeOrgan)
    o._clock = clock
    o._session = object()
    o._last_frame_ms = None
    o._started_ms = clock()
    return o


def test_a_freshly_started_stream_is_alive():
    c = Clock()
    assert _organ(c).alive() is True


def test_frames_arriving_keep_it_alive():
    c = Clock()
    o = _organ(c)
    for _ in range(10):
        c.advance(EyeOrgan.STALE_AFTER_MS // 2)
        o._last_frame_ms = c()
        assert o.alive() is True


def test_a_stream_that_stops_delivering_is_dead():
    """THE WHOLE POINT. No exception, no callback, no error — just silence.
    Before this, the organ reported healthy for ever."""
    c = Clock()
    o = _organ(c)
    o._last_frame_ms = c()
    c.advance(EyeOrgan.STALE_AFTER_MS + 1)
    assert o.alive() is False


def test_a_stream_that_never_delivers_is_eventually_dead():
    """A permission granted to the wrong binary produces exactly this: start
    succeeds, no frame ever arrives."""
    c = Clock()
    o = _organ(c)
    assert o.alive() is True, "it must not be called dead while warming up"
    c.advance(EyeOrgan.STALE_AFTER_MS + 1)
    assert o.alive() is False


def test_a_stopped_organ_is_dead_immediately():
    c = Clock()
    o = _organ(c)
    o._session = None
    assert o.alive() is False


def test_the_grace_period_is_generous_enough_to_be_safe():
    """Too tight and a busy machine restarts a HEALTHY stream, which costs
    real capture. This is a stopped stream, not a slow frame."""
    assert EyeOrgan.STALE_AFTER_MS >= 60_000


def test_the_frame_clock_is_wall_time_not_frame_time():
    """Frame timestamps come from the stream. If the stream stops, they stop
    too — so judging staleness by them would freeze the clock at exactly the
    moment it needs to move."""
    c = Clock()
    o = _organ(c)
    o.frames = 0
    o._blocked = lambda t: True          # short-circuit the rest of the beat
    o._beat(Record(t_ms=1))              # a frame whose OWN stamp is ancient
    assert o._last_frame_ms == c(), "staleness was measured by frame time"


def test_a_revived_stream_is_not_instantly_stale_again():
    """THE RESTART LOOP THIS NEARLY SHIPPED.

    A revived organ that kept the old last-frame stamp reads as instantly
    stale, so the supervisor restarts it, and it is instantly stale again.
    That loop ends with the eye BENCHED after the restart threshold and
    permanently dark — the exact outcome the staleness check exists to
    prevent, delivered by the check itself.
    """
    c = Clock()
    o = EyeOrgan.__new__(EyeOrgan)
    o._clock = c
    o._session = object()
    o._started_ms = c()
    o._last_frame_ms = c()

    c.advance(EyeOrgan.STALE_AFTER_MS * 5)     # it died a long time ago
    assert o.alive() is False

    # Whatever start() does with the stream, it must reset the frame clock.
    o._session = object()
    o._started_ms = c()
    o._last_frame_ms = None
    assert o.alive() is True, "a freshly revived eye was called dead"


def test_start_resets_the_frame_clock():
    """Asserted against the real start path rather than a hand-set field."""
    import inspect

    from tracemem.app import eye_organ

    src = inspect.getsource(eye_organ.EyeOrgan.start)
    assert "_last_frame_ms = None" in src, \
        "start() does not reset the frame clock — a revived organ will be " \
        "restarted forever and then benched"
