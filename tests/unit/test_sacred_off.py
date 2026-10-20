"""ORACLE for The sacred OFF (08_running).

PROOF: "Test: pause -> zero frames enter the retina thereafter; witnesses
bracket the hole."

Run against a REAL Retina rather than a counter, because the claim is about
what is in the ring, and a mock would only prove that the gate returned
False. `test_zero_frames_enter_the_retina_after_pause` is the oracle.

The other tests here are mostly about what the switch REFUSES to do. Those
matter more than usual: every one of them is a property that would be
easy and reasonable-sounding to add later ("just confirm destructive
pauses", "defer while the night job finishes"), and each would quietly
convert an off switch that belongs to the user into one that belongs to the
software.
"""
import inspect

import pytest

from tracemem.app.sacred_off import SacredOff
from tracemem.app.witness import WitnessKind
from tracemem.extraction.retina import FrameRecord, Retina


class FakeStream:
    def __init__(self):
        self.running = True

    def stop(self):
        self.running = False

    def start(self):
        self.running = True


def _frame(t_ms):
    return FrameRecord(t_ms=t_ms, display="main", frame=object(),
                       nbytes=1024)


# -- THE ORACLE ------------------------------------------------------------
def test_zero_frames_enter_the_retina_after_pause():
    retina = Retina()
    off = SacredOff()

    def capture(t_ms):
        if off.admit(_frame(t_ms)):
            retina.push(_frame(t_ms))

    for t in range(5):
        capture(t)
    assert len(retina.stream_since(0)) == 5

    off.pause(now_ms=1000)
    for t in range(100, 120):
        capture(t)

    assert len(retina.stream_since(0)) == 5      # not one more
    assert retina.scene_now().t_ms == 4
    assert off.frames_refused == 20


def test_witnesses_bracket_the_hole():
    off = SacredOff()
    start = off.pause(now_ms=1_000_000)
    end = off.resume(now_ms=1_600_000)

    assert start.structured["witness"] == WitnessKind.FOUNDER_PAUSE.value
    assert end.structured["witness"] == WitnessKind.FOUNDER_RESUME.value
    assert start.coord.t_ms == 1_000_000
    assert end.coord.t_ms == 1_600_000
    assert end.structured["paused_ms"] == 600_000
    assert "10.0 min" in end.text


def test_capture_flows_again_after_resume():
    retina = Retina()
    off = SacredOff()
    off.pause(1000)
    off.resume(2000)
    if off.admit(_frame(3000)):
        retina.push(_frame(3000))
    assert retina.scene_now() is not None


# -- stopping at the SOURCE, not downstream -------------------------------
def test_pause_stops_the_stream_itself():
    """Filtering after capture would mean pixels of a paused moment existed
    in this process. The promise is that they were never taken."""
    stream = FakeStream()
    off = SacredOff(stoppers=[stream.stop], starters=[stream.start])
    off.pause(1000)
    assert stream.running is False
    off.resume(2000)
    assert stream.running is True


def test_every_organ_is_silenced_even_if_one_throws():
    """A stopper that raises must not leave the other organs recording — the
    switch's whole meaning is that afterwards, nothing is capturing."""
    eye, ear = FakeStream(), FakeStream()

    def broken():
        raise RuntimeError("mic tap wedged")

    off = SacredOff(stoppers=[eye.stop, broken, ear.stop])
    with pytest.raises(RuntimeError):
        off.pause(1000)

    assert eye.running is False
    assert ear.running is False                  # reached despite the throw
    assert off.paused is True                    # and the state stuck


def test_organs_can_register_themselves():
    stream = FakeStream()
    off = SacredOff()
    off.register(stop=stream.stop, start=stream.start)
    off.pause(1)
    assert stream.running is False


# -- the refusals ----------------------------------------------------------
def test_pause_cannot_be_asked_for_confirmation():
    """Structural, not stylistic: there is no parameter through which a
    caller could route an 'are you sure'. An off switch that asks a question
    belongs to the software, not to you."""
    params = list(inspect.signature(SacredOff.pause).parameters)
    assert params == ["self", "now_ms"]
    for forbidden in ("confirm", "force", "reason", "unless", "if_"):
        assert not any(forbidden in p for p in params)


def test_pause_returns_a_witness_not_a_verdict():
    """It cannot report failure, because it cannot fail. A boolean return
    would invite `if off.pause(): ...` and, eventually, a False."""
    off = SacredOff()
    out = off.pause(1)
    assert not isinstance(out, bool)
    assert out.structured["witness"] == WitnessKind.FOUNDER_PAUSE.value


def test_pause_takes_effect_immediately_not_on_the_next_beat():
    """Synchronous by construction: the stop has already run by the time
    pause() returns, so there is no window in which the user believes they
    are off and are not."""
    stream = FakeStream()
    off = SacredOff(stoppers=[stream.stop], starters=[stream.start])
    off.pause(1)
    assert stream.running is False and off.accepts_frames() is False


def test_pausing_twice_is_not_a_second_hole():
    """The state is the same; a duplicate witness would record an interval
    that never existed and inflate the gap list."""
    off = SacredOff()
    off.pause(1000)
    again = off.pause(2000)
    assert again.structured["redundant"] is True
    assert off.paused_at_ms == 1000              # the original hole stands


def test_resuming_when_not_paused_is_not_a_hole_either():
    off = SacredOff()
    out = off.resume(1000)
    assert out.structured["redundant"] is True
    assert off.paused is False


def test_the_pause_witness_is_written_after_capture_actually_stopped():
    """Order matters: if the witness were written first and the stop then
    failed, the store would hold a record of a pause that did not happen —
    the more dangerous of the two failures."""
    order = []
    off = SacredOff(stoppers=[lambda: order.append("stopped")])
    w = off.pause(1)
    order.append("witnessed:" + w.structured["witness"])
    assert order == ["stopped", "witnessed:founder-pause"]


def test_a_pause_starting_at_time_zero_is_measured_correctly():
    """Regression: `self.paused_at_ms or now_ms` treats t=0 as absent, and
    reports a real ten-minute pause as zero-length. Any run whose clock
    starts at zero — every fixture, and a monotonic clock at boot — hits it."""
    off = SacredOff()
    off.pause(now_ms=0)
    out = off.resume(now_ms=600_000)
    assert out.structured["paused_ms"] == 600_000
