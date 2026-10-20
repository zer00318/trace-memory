"""Capture wrapper contract tests — no OS senses touched here.

The REAL proof for SCStreamSession is the live 60-second oracle run
(numbers on the board); these tests pin the injectable branches so any
interpreter can run the suite: the loud refusal when the binding is
absent, and the fallback's frame->ring contract with a fake grabber.

Motion-sense / MotionGatedCapture tests below use a fake sample-buffer +
injected pixel_reader (no CoreMedia/Quartz/ObjC touched) to pin the
low-res-config + change-triggers-full-res-capture wiring (ruling z). The
REAL proof for that wiring is the live oracle: motion stream sustains fps
at <2% CPU; a deliberate screen change triggers exactly one full-res
capture within 1s; a still screen triggers zero.
"""
import pytest

from tracemem.extraction.peripheral import Peripheral
from tracemem.extraction.retina import FrameRecord, Retina
from tracemem.capture.retina_screen import (
    MotionGatedCapture,
    PixelPayload,
    SCStreamSession,
    ScreenCaptureKitUnavailable,
    ScreencaptureFallbackSession,
)


def _solid_bgra(width, height, val):
    return bytes([val, val, val, 255]) * width * height


def test_scstream_refuses_loudly_without_binding():
    s = SCStreamSession(Retina(), import_probe=lambda: False)
    with pytest.raises(ScreenCaptureKitUnavailable):
        s.start()


def test_fallback_lands_frames_in_ring_via_fake_grab():
    r = Retina()
    ticks = iter(range(1, 100))
    s = ScreencaptureFallbackSession(
        r, grab=lambda: b"\xffJPEGISH", clock=lambda: float(next(ticks)))
    s._capture_once()
    s._capture_once()
    frames = list(r.stream_since(0))
    assert s.frames_landed == 2
    assert len(frames) == 2
    assert frames[0].frame == b"\xffJPEGISH"
    assert frames[0].nbytes == 8
    assert frames[0].t_ms == 1000


def test_fallback_capture_error_never_crashes_and_is_recorded():
    r = Retina()

    def boom():
        raise RuntimeError("screen went away")

    s = ScreencaptureFallbackSession(r, grab=boom)
    s._capture_once()
    assert s.frames_landed == 0
    assert "screen went away" in s.last_error


def test_fallback_is_marked_stand_in():
    assert ScreencaptureFallbackSession.STAND_IN is True


# -- Motion sense stream: parameterized (not forked) SCStreamSession -------

def test_session_requires_retina_or_on_frame_sink():
    with pytest.raises(ValueError):
        SCStreamSession(retina=None)


def test_on_frame_sink_receives_raw_bytes_by_default_unchanged_contract():
    """capture_geometry defaults False — the already-witnessed reading-eye
    byte contract (frame == raw bytes) must not change."""
    landed = []
    s = SCStreamSession(
        retina=None, on_frame=landed.append,
        pixel_reader=lambda sbuf: (32, 20, 128, b"\x00" * (128 * 20)),
        clock=lambda: 2.0,
    )
    s._on_sample_buffer(object())  # sample_buffer unused; pixel_reader is faked
    assert len(landed) == 1
    assert landed[0].frame == b"\x00" * (128 * 20)
    assert landed[0].nbytes == 128 * 20
    assert landed[0].t_ms == 2000
    assert s.frames_landed == 1


def test_capture_geometry_wraps_pixelpayload_for_motion_sense():
    landed = []
    data = b"\x01\x02\x03\xff" * (32 * 20)
    s = SCStreamSession(
        retina=None, on_frame=landed.append, capture_geometry=True,
        pixel_reader=lambda sbuf: (32, 20, 32 * 4, data),
    )
    s._on_sample_buffer(object())
    frame = landed[0].frame
    assert isinstance(frame, PixelPayload)
    assert (frame.width, frame.height, frame.bytes_per_row) == (32, 20, 128)
    assert frame.data == data
    assert landed[0].nbytes == len(data)


def test_non_frame_sample_is_ignored():
    landed = []
    s = SCStreamSession(retina=None, on_frame=landed.append,
                        pixel_reader=lambda sbuf: None)
    s._on_sample_buffer(object())
    assert landed == []
    assert s.frames_landed == 0


def test_retina_default_sink_backward_compatible():
    r = Retina()
    s = SCStreamSession(
        retina=r, pixel_reader=lambda sbuf: (10, 10, 40, b"x" * 400),
        clock=lambda: 5.0,
    )
    s._on_sample_buffer(object())
    frames = list(r.stream_since(0))
    assert len(frames) == 1
    assert frames[0].frame == b"x" * 400


# -- MotionGatedCapture: diff kernel wiring + change-gated full-res grab ---

class _FakeSession:
    """Stand-in for SCStreamSession in MotionGatedCapture tests: no ObjC.
    start() synchronously fires on_frame once (simulating the one-shot
    full-res grab actually landing a frame), matching SCStreamSession's
    real contract closely enough to exercise the coordinator's wiring."""

    def __init__(self, retina=None, display="main", fps=1.0,
                 width=None, height=None, capture_geometry=False,
                 on_frame=None, **_ignored):
        self.display = display
        self.width = width
        self.height = height
        self.capture_geometry = capture_geometry
        self.on_frame = on_frame
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True
        if self.on_frame is not None:
            self.on_frame(FrameRecord(t_ms=999, display=self.display,
                                      frame=b"FULLRES", nbytes=7))

    def stop(self):
        self.stopped = True


def _payload(val, t=None):
    return PixelPayload(width=32, height=20, bytes_per_row=32 * 4,
                        data=_solid_bgra(32, 20, val))


def test_motion_gated_capture_configures_low_res_motion_stream():
    mgc = MotionGatedCapture(motion_width=480, motion_height=270, motion_fps=8.0,
                             session_factory=_FakeSession)
    assert (mgc.motion.width, mgc.motion.height) == (480, 270)
    assert mgc.motion.capture_geometry is True


def test_still_screen_triggers_zero_full_res_captures():
    # Peripheral's first-ever observation has no baseline to diff against,
    # so it always reports "change" (same convention as change_gate's
    # first-frame READ) — that legitimately fires the one startup capture.
    # A genuinely still screen (frame N vs N-1, identical) must fire zero.
    mgc = MotionGatedCapture(peripheral=Peripheral(dwell_ms=50_000),
                             session_factory=_FakeSession)
    black = _payload(0)
    mgc._on_motion_frame(FrameRecord(t_ms=1000, display="main", frame=black, nbytes=0))
    assert mgc.full_res_triggers == 1     # baseline frame: expected startup trigger
    mgc._on_motion_frame(FrameRecord(t_ms=5000, display="main", frame=black, nbytes=0))
    mgc._on_motion_frame(FrameRecord(t_ms=9000, display="main", frame=black, nbytes=0))
    assert mgc.full_res_triggers == 1     # no NEW triggers on a genuinely still screen


def test_deliberate_change_triggers_exactly_one_full_res_capture():
    captured = []
    mgc = MotionGatedCapture(peripheral=Peripheral(dwell_ms=50_000),
                             on_full_res_frame=captured.append,
                             session_factory=_FakeSession)
    mgc._on_motion_frame(FrameRecord(t_ms=1000, display="main", frame=_payload(0), nbytes=0))
    mgc._on_motion_frame(FrameRecord(t_ms=1010, display="main", frame=_payload(255), nbytes=0))
    assert mgc.full_res_triggers == 1
    assert len(captured) == 1
    assert captured[0].frame == b"FULLRES"


def test_change_bursts_within_cooldown_collapse_to_one_trigger():
    mgc = MotionGatedCapture(peripheral=Peripheral(dwell_ms=50_000),
                             trigger_cooldown_ms=1_000,
                             session_factory=_FakeSession)
    mgc._on_motion_frame(FrameRecord(t_ms=1000, display="main", frame=_payload(0), nbytes=0))
    mgc._on_motion_frame(FrameRecord(t_ms=1010, display="main", frame=_payload(255), nbytes=0))
    mgc._on_motion_frame(FrameRecord(t_ms=1200, display="main", frame=_payload(0), nbytes=0))
    assert mgc.full_res_triggers == 1     # second change is within the 1s cooldown
    mgc._on_motion_frame(FrameRecord(t_ms=2500, display="main", frame=_payload(255), nbytes=0))
    assert mgc.full_res_triggers == 2     # cooldown elapsed: fires again
