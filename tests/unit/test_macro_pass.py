"""Running the macro eye without taking capture or the founder down with it.

`VlmMacroEye` has worked since July and has never produced a claim in the
product, because nothing scheduled it. Scheduling it is the whole risk: it is
a 12B model on the same Metal GPU that live capture needs, and heavy GPU work
beside capture makes capture go dark WITH NO ERROR.

So the tests that matter here are not "does it produce claims". They are:

  · does it refuse to run while the eye is open;
  · does it get out of the way when the founder asks a question;
  · does it lose frames when it is interrupted or refused;
  · does one bad frame end the pass.
"""
from __future__ import annotations

from tracemem.app.macro_pass import MACRO_ROLE, MacroPass
from tracemem.app.scheduler import GPU_HEAVY_ROLES, Spotlight
from tracemem.extraction.macro_buffer import MacroBuffer


class Clock:
    def __init__(self):
        self.t = 1_700_000_000_000

    def now_ms(self):
        self.t += 1000
        return self.t


class Eye:
    """Returns one object claim per frame, counting looks."""

    def __init__(self, fail_on=(), reply=None):
        self.looks = 0
        self.fail_on = set(fail_on)
        self.reply = reply

    def describe_bytes(self, png):
        self.looks += 1
        if self.looks in self.fail_on:
            raise RuntimeError("the model returned nonsense")
        if self.reply is not None:
            return self.reply
        return [{"kind": "object", "text": "a chess board"}]


def _rig(n_frames=3, eye=None, capture=False):
    clock = Clock()
    spot = Spotlight({"gemma3:12b-it-qat": 4.0}, clock)
    spot.set_capture(capture)
    buf = MacroBuffer()
    for i in range(n_frames):
        buf.offer("f%d" % i, b"png" * 10, 1000 * (i + 1), app="Brave",
                  scene_key="s%d" % i)
    written = []
    p = MacroPass(buf, eye or Eye(), written.append, spot, clock, "mac-1")
    return p, buf, spot, written


# -- the safety rule ----------------------------------------------------

def test_the_role_is_one_the_arbiter_actually_refuses():
    """The arbiter's protection is a role-name lookup. If MACRO_ROLE drifts
    out of GPU_HEAVY_ROLES, every test below still passes and the product
    silently starts running a 12B model beside live capture."""
    assert MACRO_ROLE in GPU_HEAVY_ROLES


def test_it_will_not_run_while_the_eye_is_open():
    p, buf, _, written = _rig(capture=True)
    out = p.run()
    assert out["ran"] == 0
    assert "eye is open" in out["skipped"]
    assert written == []
    assert len(buf) == 3, "a refused pass consumed the frames"


def test_it_says_why_rather_than_just_not_running():
    p, _, _, _ = _rig(capture=True)
    assert "eye is open" in p.why_not()
    p2, _, _, _ = _rig(n_frames=0)
    assert p2.why_not() == "no frames banked"
    p3, _, spot, _ = _rig()
    assert p3.why_not() == ""


def test_a_busy_gpu_is_not_a_failure():
    p, buf, spot, _ = _rig()
    with spot.acquire("local-ollama", role="ANSWER", priority=10):
        out = p.run()
    assert out["ran"] == 0 and out["skipped"]
    assert len(buf) == 3


# -- the ordinary path --------------------------------------------------

def test_it_understands_what_was_banked():
    p, buf, _, written = _rig(n_frames=3)
    out = p.run()
    assert out["ran"] == 1 and out["frames"] == 3 and out["claims"] == 3
    assert len(buf) == 0, "understood frames must not be understood twice"
    assert all(c.provenance.perceiver == "vlm" for c in written)


def test_claims_carry_the_frame_they_came_from():
    p, _, _, written = _rig(n_frames=2)
    p.run()
    assert {c.coord.capture.frame_id for c in written} == {"f0", "f1"}
    assert {c.coord.t_ms for c in written} == {1000, 2000}
    assert all(c.coord.capture.app == "Brave" for c in written)


def test_the_cost_of_looking_is_recorded():
    """`predicted_vs_actual_by_bidder` is only meaningful if someone writes
    down what the look actually cost."""
    p, _, _, written = _rig(n_frames=1)
    p.run()
    assert written[0].actual_cost > 0


# -- getting out of the way ---------------------------------------------

def test_a_waiting_question_stops_the_pass():
    """The founder waiting on a background chore is the product feeling
    broken. The arbiter's contract is poll-and-yield; this polls per frame."""
    clock = Clock()
    spot = Spotlight({"gemma3:12b-it-qat": 4.0}, clock)
    buf = MacroBuffer()
    for i in range(4):
        buf.offer("f%d" % i, b"png", 1000 * (i + 1), scene_key="s%d" % i)

    written = []

    class Interrupting(Eye):
        def describe_bytes(self, png):
            out = Eye.describe_bytes(self, png)
            if self.looks == 2:
                spot.signal_query()          # the founder asks, mid-pass
            return out

    p = MacroPass(buf, Interrupting(), written.append, spot, clock, "mac-1")
    out = p.run()
    assert out["frames"] == 2, "it kept looking after being asked to stop"
    assert out["yielded"] == 2
    # WHAT IT EARNED IT KEEPS, WHAT IT DID NOT IT RETURNS.
    assert len(written) == 2
    assert len(buf) == 2, "the unread frames were thrown away"
    assert {f.frame_id for f in buf.take_all()} == {"f2", "f3"}


def test_an_interrupted_pass_does_not_redo_what_it_finished():
    clock = Clock()
    spot = Spotlight({"gemma3:12b-it-qat": 4.0}, clock)
    buf = MacroBuffer()
    for i in range(3):
        buf.offer("f%d" % i, b"png", 1000 * (i + 1), scene_key="s%d" % i)
    written = []

    class Once(Eye):
        def describe_bytes(self, png):
            out = Eye.describe_bytes(self, png)
            if self.looks == 1:
                spot.signal_query()
            return out

    p = MacroPass(buf, Once(), written.append, spot, clock, "mac-1")
    p.run()
    left = {f.frame_id for f in buf.take_all()}
    assert "f0" not in left, "an understood frame went back into the buffer"


# -- one bad frame -------------------------------------------------------

def test_one_bad_frame_costs_only_itself():
    """A local model's JSON is not always clean, and one enormous frame can
    time out. Neither may cost the whole pass."""
    p, buf, _, written = _rig(n_frames=3, eye=Eye(fail_on=(2,)))
    out = p.run()
    assert out["ran"] == 1
    assert out["frames"] == 2 and out["failed"] == 1
    assert len(written) == 2


def test_a_frame_the_model_says_nothing_about_is_not_an_error():
    p, _, _, written = _rig(n_frames=2, eye=Eye(reply=[]))
    out = p.run()
    assert out["ran"] == 1 and out["frames"] == 2 and out["claims"] == 0
    assert out["failed"] == 0
    assert written == []


# -- honesty about coverage ---------------------------------------------

def test_stats_carry_what_the_eye_never_saw():
    """A pass that understood three scenes out of eighty must not read as a
    pass that understood the day."""
    clock = Clock()
    spot = Spotlight({"gemma3:12b-it-qat": 4.0}, clock)
    buf = MacroBuffer(max_frames=3)
    for i in range(20):
        buf.offer("f%d" % i, b"png", 1000 * (i + 1), scene_key="s%d" % i)
    p = MacroPass(buf, Eye(), lambda c: None, spot, clock, "mac-1")
    p.run()
    s = p.stats()
    assert s["frames_seen"] == 3
    assert s["offered"] == 20
    assert s["thinned"] == 17
