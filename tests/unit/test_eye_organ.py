"""ORACLE for the eye organ (WO-091b).

The adapter that lets the daemon actually watch. Driven with SYNTHETIC
frames through an injected session factory, so the whole beat is exercised
without a display, without Screen Recording permission, and without
capturing anyone's screen during a test run.

The tests that matter most are the ones about what the organ must NOT do:
it must not hold the store, and it must not let an exception vanish. Both
are failure modes that leave a daemon looking healthy while it records
nothing — which is the shape of every real defect this session has found.
"""
import pytest

from tracemem.app.eye_organ import DEFAULT_FPS, EyeOrgan
from tracemem.capture.retina_screen import FrameRecord, PixelPayload

W = H = 128


class FakeSession:
    """Stands in for SCStreamSession; the test pushes frames by hand."""

    def __init__(self, on_frame, fps, capture_geometry, clock=None):
        self.on_frame = on_frame
        self.fps = fps
        self.capture_geometry = capture_geometry
        self.clock = clock
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1


def frame(t_ms, value):
    data = bytes([value, value, value, 255]) * (W * H)
    return FrameRecord(t_ms=t_ms, display="main",
                       frame=PixelPayload(W, H, W * 4, data), nbytes=len(data))


def organ(submit=None, factory_holder=None):
    sessions = factory_holder if factory_holder is not None else []

    def factory(**kw):
        s = FakeSession(**kw)
        sessions.append(s)
        return s

    return EyeOrgan(submit=submit or (lambda c: True), session_factory=factory)


# -- the organ contract ----------------------------------------------------
def test_it_satisfies_the_supervisors_contract():
    """`def alive` used to appear only in tests. The supervisor needs all
    four of these on a real class."""
    eye = organ()
    assert eye.name == "eye"
    for method in ("start", "stop", "alive"):
        assert callable(getattr(eye, method))


def test_alive_tracks_the_session():
    eye = organ()
    assert eye.alive() is False
    eye.start()
    assert eye.alive() is True
    eye.stop()
    assert eye.alive() is False


def test_stopping_twice_is_harmless():
    """The supervisor's clean stop may follow a stop that already happened."""
    eye = organ()
    eye.start()
    eye.stop()
    eye.stop()


def test_it_starts_the_stream_at_the_reading_cadence():
    sessions = []
    eye = organ(factory_holder=sessions)
    eye.start()
    assert sessions[0].started == 1
    assert sessions[0].fps == DEFAULT_FPS
    assert sessions[0].capture_geometry is True


# -- the beat --------------------------------------------------------------
def test_frames_flow_through_the_change_gate():
    eye = organ()
    eye.start()
    for i in range(6):
        eye._session.on_frame(frame(1000 + i * 500, 0 if i % 2 else 255))

    health = eye.health()
    assert health["frames"] == 6
    assert health["errors"] == 0


def test_an_unchanging_screen_stops_spending_deep_looks():
    """The change gate is the whole economy: a still screen must not spend a
    deep look every beat.

    Note what the FIRST frame does. It earns a gaze, and that is correct
    rather than a leak: with no baseline to compare against, the first
    thing the eye ever sees IS news. (My first version of this test asserted
    zero gazes and was simply wrong about the design.) What must not happen
    is a second, third and fourth gaze on a screen that never changed.
    """
    eye = organ()
    eye.start()
    for i in range(6):
        eye._session.on_frame(frame(1000 + i * 500, 128))   # identical frames

    assert eye.gazes == 1, "only the baseline frame should earn a look"
    assert eye.frames == 6


def test_stillness_eventually_earns_one_dwell_gaze():
    eye = organ()
    eye.start()
    eye._session.on_frame(frame(1_000, 128))
    eye._session.on_frame(frame(10_000, 128))
    assert eye.gazes == 2, "baseline plus one dwell gaze after sustained reading"


def test_ax_refreshes_on_its_own_cadence_when_pixels_are_static():
    reads = []
    eye = EyeOrgan(submit=lambda c: True,
                   session_factory=lambda **kw: FakeSession(**kw),
                   ax_reader=lambda: reads.append(1) or None)
    eye.start()
    eye._session.on_frame(frame(1_000, 128))
    eye._session.on_frame(frame(7_000, 128))
    assert len(reads) == 2


def test_blurred_crop_is_dropped_before_ocr(monkeypatch):
    import tracemem.extraction.l1_read as l1
    monkeypatch.setattr(l1, "recognize_pixels",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("blurred crop reached OCR")))
    eye = organ()
    eye.start()
    eye._session.on_frame(frame(1_000, 128))
    assert eye.blur_drops == 1


def test_real_beat_attributes_ocr_to_the_window_owning_its_pixels(monkeypatch):
    """Black-box the production weld, not only the geometry helper.

    Focus can belong to an editor while a floating browser window owns the
    pixels OCR reads.  The resulting claim must cite the pixel owner and its
    durable window id; otherwise later answers can confidently name the
    wrong application.
    """
    from types import SimpleNamespace

    from tracemem.capture.window_stack import StackSnapshot, WindowInfo
    from tracemem.extraction.ocr_vision import TextRun
    import tracemem.extraction.attention_mac as attention
    import tracemem.extraction.blur_gate as blur
    import tracemem.extraction.l1_read as l1

    monkeypatch.setattr(blur, "is_readable", lambda _gray: True)
    monkeypatch.setattr(
        l1, "recognize_pixels",
        lambda *_a, **_k: [TextRun("visible browser words", .25, .25,
                                    .5, .5, .99)])
    monkeypatch.setattr(
        attention, "attention_span",
        lambda _t: SimpleNamespace(app="Focused Editor", window="notes"))

    snapshot = StackSnapshot(1_000, (WindowInfo(
        window_id=73, owner="Visible Browser", pid=7,
        title="reference", layer=0, rect=(0, 0, W, H), z=0),))
    seen = []
    eye = EyeOrgan(
        submit=lambda claim: (seen.append(claim), True)[1],
        session_factory=lambda **kw: FakeSession(**kw),
        ax_reader=lambda: None,
        window_reader=lambda _t: snapshot)
    monkeypatch.setattr(eye, "_place", lambda _t: "dev:test")

    eye.start()
    eye._session.on_frame(frame(1_000, 128))

    assert len(seen) == 1
    claim = seen[0]
    assert claim.text == "visible browser words"
    assert claim.coord.capture.app == "Visible Browser"
    assert claim.coord.capture.window == "window:73"
    assert eye.window_attributed == 1


def test_sandboxed_helper_output_reaches_store_ready_claims_with_parent_coords(
        monkeypatch, tmp_path):
    """Reference-helper vertical slice: real eye crop -> OCR text -> helper
    output -> a fully stamped specialist claim. The child never controls the
    coordinate or its own grade."""
    from types import SimpleNamespace

    from tracemem.domain import Grade
    from tracemem.extraction.helper_runtime import HelperReading, HelperRun
    from tracemem.extraction.ocr_vision import TextRun
    import tracemem.extraction.attention_mac as attention
    import tracemem.extraction.blur_gate as blur
    import tracemem.extraction.l1_read as l1

    monkeypatch.setattr(blur, "is_readable", lambda _gray: True)
    monkeypatch.setattr(l1, "recognize_pixels", lambda *_a, **_k: [
        TextRun("class MemoryCell", .25, .25, .5, .5, .99)])
    monkeypatch.setattr(attention, "attention_span", lambda _t:
                        SimpleNamespace(app="Visual Studio Code", window="main"))

    class Helpers:
        def run(self, text, app, context):
            assert text == "class MemoryCell"
            assert app == "Visual Studio Code"
            return HelperRun(readings=(HelperReading(
                "com.trace.reference.code-symbols", "1.0.0",
                "Code symbols: MemoryCell", .98,
                {"symbols": ["MemoryCell"]}, .003, 1.0),))

        def health(self):
            return {"discovered": 1, "calls": 1, "failures": 0,
                    "deferred": 0}

    seen = []
    eye = EyeOrgan(
        submit=lambda claim: (seen.append(claim), True)[1],
        session_factory=lambda **kw: FakeSession(**kw),
        ax_reader=lambda: None,
        window_reader=lambda _t: SimpleNamespace(windows=()),
        helper_runtime=Helpers())
    monkeypatch.setattr(eye, "_place", lambda _t: "dev:test")
    eye.start()
    import time
    captured_at = int(time.time() * 1000)
    eye._session.on_frame(frame(captured_at, 128))

    assert not eye.errors, eye.last_error
    helper = next(c for c in seen if c.bidder.startswith("helper:"))
    parent = next(c for c in seen if c.provenance.perceiver == "ocr")
    assert helper.grade is Grade.SPECIALIST
    assert helper.provenance.perceiver == (
        "specialist:com.trace.reference.code-symbols@1.0.0")
    assert helper.coord.capture == parent.coord.capture
    assert helper.coord.span.bbox == parent.coord.span.bbox
    assert helper.structured["parent_claim_ids"] == [parent.id]
    assert helper.bid_id and helper.actual_cost == .003
    assert helper.scope == "frame"

    # The same reference result survives the real append gate and is usable
    # by the guarded period-answer path. This closes capture→helper→TEXT→ask,
    # rather than proving the child process in isolation.
    from tests.fakes import ContextAnswerReasoner
    from tracemem.app.window_answer import WindowAnswerer
    from tracemem.text.claim_log import ClaimLog

    store_path = str(tmp_path / "raw.sqlite3")
    store = ClaimLog(store_path)
    for claim in seen:
        store.append(claim)

    class Clock:
        def now_ms(self): return captured_at

    answer = WindowAnswerer(
        lambda: ClaimLog(store_path), ContextAnswerReasoner(), Clock()).answer(
            "What code symbols did I see today?")
    assert "MemoryCell" in answer.text
    assert answer.citations


# -- what it must never do -------------------------------------------------
def test_it_is_never_handed_the_store():
    """An organ holding a ClaimLog could append from SCStream's thread —
    the exact defect WO-093 removed. Not giving it one is how that stays
    removed rather than merely fixed."""
    eye = organ()
    for attr in vars(eye):
        value = getattr(eye, attr)
        assert not hasattr(value, "append") or callable(value), attr
    assert not hasattr(eye, "_store")
    assert not hasattr(eye, "_claim_log")


def test_submit_is_the_only_outward_path():
    seen = []
    eye = organ(submit=lambda c: (seen.append(c), True)[1])
    eye.start()
    for i in range(4):
        eye._session.on_frame(frame(1000 + i * 500, 0 if i % 2 else 255))
    # Whatever the OCR found (nothing, on synthetic pixels), the ONLY route
    # out of the organ was submit.
    assert eye.claims == len(seen)


def test_an_exception_in_the_beat_is_recorded_not_swallowed():
    """The ObjC delegate discards callback exceptions silently, so a
    capture thread that starts raising looks exactly like a quiet
    afternoon: no error, no claims, no hole."""
    eye = organ()
    eye.start()

    def explode(_payload, _t_ms):
        raise ValueError("peripheral blew up")

    import tracemem.extraction.peripheral as peripheral_mod
    original = peripheral_mod.feed_motion_frame
    peripheral_mod.feed_motion_frame = lambda p, f, t: explode(f, t)
    try:
        eye._session.on_frame(frame(1000, 255))
    finally:
        peripheral_mod.feed_motion_frame = original

    assert eye.errors == 1
    assert "peripheral blew up" in eye.last_error


def test_the_beat_survives_a_failing_focus_sense(monkeypatch):
    """A claim stamped 'unknown' app is worth far more than no claim, and
    infinitely more than a guessed one."""
    import tracemem.extraction.attention_mac as attention

    def boom(*_a, **_k):
        raise RuntimeError("no accessibility permission")

    monkeypatch.setattr(attention, "attention_span", boom)

    eye = organ()
    eye.start()
    for i in range(4):
        eye._session.on_frame(frame(1000 + i * 500, 0 if i % 2 else 255))
    assert eye.errors == 0          # degraded, did not die


def test_a_degenerate_crop_yields_nothing_rather_than_raising():
    """Tile arithmetic on a tiny frame can produce an empty crop. That is a
    nothing, not a crash — a crashing eye stops the whole beat."""
    eye = organ()
    eye.start()
    tiny = FrameRecord(t_ms=1, display="main",
                       frame=PixelPayload(1, 1, 4, bytes([0, 0, 0, 255])),
                       nbytes=4)
    eye._session.on_frame(tiny)
    assert eye.errors == 0


def test_health_reports_the_whole_story():
    eye = organ()
    eye.start()
    eye._session.on_frame(frame(1000, 255))
    got = eye.health()
    assert set(got) == {"frames", "gazes", "claims", "ax_claims",
                        "ax_trusted", "blur_drops", "repeats_suppressed", "ax_repeats_suppressed", "presence_claims", "detail_bids", "detail_reads", "detail_fresh", "window_attributed", "helper_claims",
                        "helper_failures", "helper_deferred", "helpers",
                        "errors", "last_error"}, (
        "ax_claims joined the story when the Accessibility floor landed — a\n"
        "sense that emits claims nobody can count is a sense nobody can\n"
        "tell has stopped working")


# ---- a half-built organ must never report itself alive --------------------

def test_a_failed_start_leaves_the_eye_dead_not_half_alive():
    """WITNESSED ON THE FOUNDER'S MACHINE, and it produced the worst state
    this product has: capture dark, the daemon reporting eye=alive, and the
    brain refusing to think because it believed the eye held the GPU.

    `start()` assigned self._session and THEN called session.start(). When
    that raised — "ScreenCaptureKit reports zero displays", which is what a
    sleeping display gives you — the half-built session stayed on the
    instance and `alive()` answered True forever. So:

      · the supervisor saw a live organ and never retried it, so the backoff
        that exists for exactly this never got a turn;
      · set_capture(True) stayed signalled and the night refused every slice
        with "capture is live", while capture was capturing nothing.

    An organ is alive when it is RUNNING, never when it has been constructed.
    """
    class _Doomed:
        def __init__(self, **kw):
            pass

        def start(self):
            raise RuntimeError("ScreenCaptureKit reports zero displays")

        def stop(self):
            pass

    eye = EyeOrgan(submit=lambda c: True, session_factory=_Doomed)

    with pytest.raises(RuntimeError):
        eye.start()

    assert eye.alive() is False, (
        "a constructed-but-not-started session must not read as alive — the "
        "supervisor's whole retry path hangs off this answer")


def test_a_retry_after_a_failed_start_can_still_succeed():
    """The recovery the bug above made impossible: once the world allows it,
    the very next start must bring the eye back."""
    state = {"ok": False}

    class _NotYet:
        def __init__(self, **kw):
            pass

        def start(self):
            if not state["ok"]:
                raise RuntimeError("zero displays")

        def stop(self):
            pass

    eye = EyeOrgan(submit=lambda c: True, session_factory=_NotYet)
    with pytest.raises(RuntimeError):
        eye.start()
    assert eye.alive() is False

    state["ok"] = True                      # the display wakes
    eye.start()

    assert eye.alive() is True
    assert eye.health()["last_error"] is None, (
        "a recovered eye kept advertising its obsolete start failure")


def test_a_failed_start_records_why_so_status_can_say_it():
    """`./trace status` reads last_error to tell the founder WHY the eye is
    dark. The why that matters most is not the code's fault — Screen
    Recording granted to the terminal but not to the interpreter launchd
    spawns — and without this, status could report a dead eye and nothing
    about the one setting that fixes it."""
    class _Denied:
        def __init__(self, **kw):
            pass

        def start(self):
            raise RuntimeError(
                "SCShareableContent failed — usually Screen Recording "
                "permission for this interpreter: ... Code=-3801")

        def stop(self):
            pass

    eye = EyeOrgan(submit=lambda c: True, session_factory=_Denied)
    with pytest.raises(RuntimeError):
        eye.start()

    assert eye.errors == 1
    assert "-3801" in (eye.health()["last_error"] or "")
