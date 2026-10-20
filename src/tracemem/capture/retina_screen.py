"""OWNS: turning the OS's screen into FrameRecords for Retina.push()
(map: World / SCStream session, campaign/tree/01_world.json).

Capture only. No OCR, no coordinate work, no business logic lives here —
frames arrive as opaque payloads and leave as opaque payloads. Everything
downstream of Retina.push() is someone else's job.

STATUS (witnessed 2026-07-19, this Mac, the borrowed VLM venv):
pyobjc-core / pyobjc-framework-Quartz ARE installed, but
pyobjc-framework-ScreenCaptureKit — the actual Python binding for
ScreenCaptureKit's SCStream/SCStreamOutput/SCContentFilter classes — is
NOT. `pip list` confirms its absence; `import ScreenCaptureKit` raises
ModuleNotFoundError. Installing it into that venv is forbidden by this
atom's constraints (it is a borrowed interpreter, not ours to grow).
`objc.loadBundle` can load the bare .framework, but without the binding's
bridge metadata every class/protocol/selector would have to be hand-wired
from scratch — that is a from-scratch framework-binding project, not a
capture wrapper, and is out of scope for this atom.

SCStreamSession below is the REAL wrapper the atom's brief asks for. It is
honest about being blocked: start() raises ScreenCaptureKitUnavailable
naming exactly this, rather than pretending to work or silently degrading.

ScreencaptureFallbackSession is a STAND-IN, explicitly not the deliverable:
it drives the identical start()/stop() -> Retina.push(FrameRecord) contract
using the `screencapture` CLI (a single still per call — there is no
continuous-stream API at this layer) polled on a background thread. It
exists ONLY to prove frames reach the ring end-to-end while the real
binding is unavailable. Cadence and per-frame cost are both far worse than
a real SCStream would give; replace it the moment the framework binding
lands (this atom's `pct` stays 0 for the real wrapper until then).
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from tracemem.capture.capture_config import (
    CHANGE_TRIGGER_COOLDOWN_MS,
    MOTION_FPS,
    MOTION_HEIGHT,
    MOTION_WIDTH,
    READING_EYE_GRAB_FPS,
    SCSTREAM_SESSION_DEFAULT_FPS,
)
from tracemem.extraction.retina import FrameRecord, Retina

DEFAULT_INTERVAL_S = 1.0   # screencapture floor for this fallback; not a real frame rate


class ScreenCaptureKitUnavailable(RuntimeError):
    """The real ScreenCaptureKit (pyobjc) binding is not installed. See the
    module docstring — this is a witnessed environment fact, not a bug."""


def _default_import_probe() -> bool:
    try:
        import ScreenCaptureKit  # noqa: F401
    except ImportError:
        return False
    return True


@dataclass(frozen=True)
class PixelPayload:
    """Geometry-carrying frame payload (World / Motion sense stream). Only
    used when a session is built with capture_geometry=True — the default
    reading-eye path is untouched and still pushes raw bytes, preserving
    the already-witnessed 2026-07-19 oracle. Downstream adapters (e.g.
    extraction/peripheral.py's grayscale_grid_from_bgra) read width/height/
    bytes_per_row/data by name, duck-typed — no cross-layer import needed.
    """
    width: int
    height: int
    bytes_per_row: int
    data: bytes


def _default_pixel_reader(sample_buffer) -> Optional[Tuple[int, int, int, bytes]]:
    """Real CoreMedia/Quartz extraction: (width, height, bytes_per_row, data)
    or None for non-frame samples (e.g. audio) / unreadable buffers.
    Injectable so _on_sample_buffer's framing logic is unit-testable on any
    interpreter without a real CVPixelBuffer.
    """
    import CoreMedia
    import Quartz
    pixel_buffer = CoreMedia.CMSampleBufferGetImageBuffer(sample_buffer)
    if pixel_buffer is None:
        return None
    Quartz.CVPixelBufferLockBaseAddress(pixel_buffer, 1)  # 1 = read-only
    try:
        size = Quartz.CVPixelBufferGetDataSize(pixel_buffer)
        base = Quartz.CVPixelBufferGetBaseAddress(pixel_buffer)
        data = bytes(base.as_buffer(size)) if base is not None else b""
        width = Quartz.CVPixelBufferGetWidth(pixel_buffer)
        height = Quartz.CVPixelBufferGetHeight(pixel_buffer)
        bytes_per_row = Quartz.CVPixelBufferGetBytesPerRow(pixel_buffer)
    finally:
        Quartz.CVPixelBufferUnlockBaseAddress(pixel_buffer, 1)
    if not data:
        return None
    return width, height, bytes_per_row, data


def _native_pixels(sc_display) -> Tuple[Optional[int], Optional[int]]:
    """A ScreenCaptureKit display's real BACKING PIXEL size.

    SCDisplay speaks points; SCStreamConfiguration wants pixels. Bridging
    the two needs the display mode, which only Quartz exposes. Returns
    (None, None) when anything is unavailable so the caller falls back to
    the points figure rather than capturing nothing at all.
    """
    try:
        import Quartz
        did = sc_display.displayID()
        mode = Quartz.CGDisplayCopyDisplayMode(did)
        if mode is None:
            return (None, None)
        return (int(Quartz.CGDisplayModeGetPixelWidth(mode)),
                int(Quartz.CGDisplayModeGetPixelHeight(mode)))
    except Exception:
        return (None, None)


_stream_output_class = None    # memoized ObjC delegate class — see _get_stream_output_class


def _get_stream_output_class():
    """The SCStreamOutput delegate class, defined exactly ONCE per process.

    BUG FOUND LIVE (2026-07-19, motion-sense oracle run): start() used to
    define `class _Output(NSObject, ...)` INSIDE the method body. PyObjC
    registers ObjC classes globally by name, so a second SCStreamSession
    in the same process (e.g. the motion-sense stream plus a triggered
    one-shot full-res grab) collided: `error('_Output is overriding
    existing Objective-C class')`, and the second session silently landed
    zero frames (last_error was set, nothing crashed — an invisible
    failure). Hoisting the class to module scope, created once and reused
    (each instance carries its owning session via initWithSession_),
    fixes multi-session-per-process capture. Not a fork of SCStreamSession
    — its public contract (start/stop/frame callback) is unchanged.
    """
    global _stream_output_class
    if _stream_output_class is None:
        import objc
        from Foundation import NSObject

        class _TraceStreamOutput(NSObject, protocols=[objc.protocolNamed("SCStreamOutput")]):
            def initWithSession_(self, session):
                self = objc.super(_TraceStreamOutput, self).init()
                if self is None:
                    return None
                self._session = session
                return self

            def stream_didOutputSampleBuffer_ofType_(self, stream, sbuf, otype):
                try:
                    self._session._on_sample_buffer(sbuf)
                except Exception as exc:      # capture-only: never crash the queue
                    self._session.last_error = repr(exc)

        _stream_output_class = _TraceStreamOutput
    return _stream_output_class


class SCStreamSession:
    """The real wrapper: a ScreenCaptureKit stream whose frame callback
    feeds a sink (Retina.push by default). Runs on the repo's own venv
    (WO-013), where pyobjc-framework-ScreenCaptureKit is installed. On an
    interpreter without the binding, start() still refuses loudly.
    `import_probe` is injectable so the missing/present branch stays
    testable anywhere.

    Frames are copied out of the CVPixelBuffer for now — an honest copy,
    not zero-copy; the Frame-delivery atom owns replacing the copy with a
    buffer reference once its CPU bench says the copy is too expensive.

    Parameterized (ruling z), NOT forked, for the motion-sense eye:
    - width/height: override the stream's SCStreamConfiguration dimensions
      (ScreenCaptureKit downscales natively — no software downscale needed).
      None (default) keeps the reading-eye's native-resolution behavior.
    - on_frame: sink override. Defaults to retina.push, so the existing
      reading-eye contract is unchanged. The motion-sense stream passes a
      callback straight into the diff kernel instead of a Retina.
    - capture_geometry: when True, the pushed FrameRecord.frame is a
      PixelPayload (width/height/bytes_per_row/data) instead of raw bytes,
      so a grayscale-grid adapter can reshape it correctly. Default False
      preserves the already-witnessed reading-eye byte contract exactly.
    """

    START_TIMEOUT_S = 10.0

    def __init__(self, retina: Optional[Retina] = None, display: str = "main",
                 fps: float = SCSTREAM_SESSION_DEFAULT_FPS,
                 width: Optional[int] = None, height: Optional[int] = None,
                 on_frame: Optional[Callable[[FrameRecord], Any]] = None,
                 capture_geometry: bool = False,
                 import_probe: Callable[[], bool] = _default_import_probe,
                 pixel_reader: Callable[[Any], Optional[Tuple[int, int, int, bytes]]] = _default_pixel_reader,
                 clock: Callable[[], float] = time.time):
        self.retina = retina
        self.display = display
        self.fps = fps
        self.width = width
        self.height = height
        self.capture_geometry = capture_geometry
        self._sink = on_frame or (retina.push if retina is not None else None)
        if self._sink is None:
            raise ValueError("SCStreamSession needs a retina or an on_frame sink")
        self._import_probe = import_probe
        self._pixel_reader = pixel_reader
        self._clock = clock
        self._stream = None
        self._output = None          # kept referenced or ObjC releases it
        self._queue = None
        self._closed = False
        self.frames_landed = 0
        self.last_error: Optional[str] = None
        # Scale stamping atom (01_world): the display's width in POINTS, learned
        # from SCDisplay at start(). Backing scale is then measured per frame as
        # (pixels delivered / points wide) rather than assumed from the hardware
        # — because cfg.setWidth_() may downscale, a Retina display can legally
        # deliver a 1.0-scale frame. Measuring survives that; assuming doesn't.
        self._display_points_width: Optional[float] = None

    def _scale_for(self, pixel_width: int) -> float:
        """Backing scale of THIS frame. Unknown geometry → 1.0, the honest
        identity: better a frame that claims no scaling than one that claims
        a scaling it never had."""
        pts = self._display_points_width
        if not pts or pts <= 0 or pixel_width <= 0:
            return 1.0
        return float(pixel_width) / float(pts)

    # -- delegate target: called from the stream's dispatch queue --------
    def _on_sample_buffer(self, sample_buffer) -> None:
        if self._closed:
            return                # a late frame after stop(): see stop()
        read = self._pixel_reader(sample_buffer)
        if read is None:      # non-frame samples (e.g. audio) arrive too
            return
        width, height, bytes_per_row, data = read
        if not data:
            self.last_error = "CVPixelBuffer had no readable base address"
            return
        frame: Any = (PixelPayload(width, height, bytes_per_row, data)
                      if self.capture_geometry else data)
        self._sink(FrameRecord(
            t_ms=int(self._clock() * 1000),
            display=self.display,
            frame=frame,
            nbytes=len(data),
            scale=self._scale_for(width),
        ))
        self.frames_landed += 1

    def start(self) -> None:
        if not self._import_probe():
            raise ScreenCaptureKitUnavailable(
                "pyobjc-framework-ScreenCaptureKit is not importable on "
                "this interpreter. Run on the repo's own venv "
                "(.venv, WO-013); the borrowed VLM venv never gets it. "
                "ScreencaptureFallbackSession remains the stand-in "
                "elsewhere."
            )
        import objc
        import CoreMedia
        import libdispatch
        import ScreenCaptureKit as SCK
        from Foundation import NSObject

        got = {}
        done = threading.Event()

        def _content_cb(content, error):
            got["content"], got["error"] = content, error
            done.set()

        SCK.SCShareableContent.getShareableContentWithCompletionHandler_(_content_cb)
        if not done.wait(self.START_TIMEOUT_S) or got.get("error") is not None:
            raise RuntimeError(
                "SCShareableContent failed — usually Screen Recording "
                f"permission for this interpreter: {got.get('error')}"
            )
        displays = got["content"].displays()
        if not displays:
            raise RuntimeError("ScreenCaptureKit reports zero displays")
        sc_display = displays[0]
        # SCDisplay reports its width in POINTS; the CVPixelBuffer arrives in
        # pixels. Holding both is what makes _scale_for() a measurement.
        self._display_points_width = float(sc_display.width() or 0)

        OutputCls = _get_stream_output_class()

        # UNIT BUG FIXED (audit 2026-07-21): SCDisplay.width/height are in
        # POINTS; SCStreamConfiguration.width/height are in PIXELS. Passing
        # the former straight into the latter asked for a frame at 1 pixel
        # per point — so on a Retina display the READING EYE was capturing
        # at half linear resolution (a quarter of the pixels) while looking
        # entirely correct, and _scale_for() could only ever compute 1.0,
        # making the whole points law unreachable in production.
        # Native resolution = the display's real backing pixel size.
        native_w, native_h = _native_pixels(sc_display)
        cfg = SCK.SCStreamConfiguration.alloc().init()
        cfg.setWidth_(self.width or native_w or sc_display.width())
        cfg.setHeight_(self.height or native_h or sc_display.height())
        cfg.setMinimumFrameInterval_(CoreMedia.CMTimeMake(1, max(1, int(self.fps))))
        # PIXEL FORMAT LAW (found live, first-beat probe 2026-07-19): SCStream's
        # DEFAULT is multi-plane YUV ('420v') — bpr/width measured 1.02, and every
        # BGRA-assuming consumer read garbage (change-storms, hallucinated OCR).
        # Force 32BGRA so PixelPayload means what it says. fourcc 'BGRA'.
        cfg.setPixelFormat_(0x42475241)
        flt = SCK.SCContentFilter.alloc().initWithDisplay_excludingWindows_(sc_display, [])
        self._queue = libdispatch.dispatch_queue_create(b"trace.capture.screen", None)
        self._output = OutputCls.alloc().initWithSession_(self)
        self._stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(flt, cfg, None)
        ok, err = self._stream.addStreamOutput_type_sampleHandlerQueue_error_(
            self._output, SCK.SCStreamOutputTypeScreen, self._queue, None)
        if not ok:
            raise RuntimeError(f"addStreamOutput failed: {err}")

        started = threading.Event()
        start_err = {}

        def _start_cb(error):
            start_err["error"] = error
            started.set()

        self._stream.startCaptureWithCompletionHandler_(_start_cb)
        if not started.wait(self.START_TIMEOUT_S):
            raise RuntimeError("startCapture never completed")
        if start_err.get("error") is not None:
            raise RuntimeError(f"startCapture failed: {start_err['error']}")

    def stop(self) -> None:
        """Stop the stream AND wait out every frame already handed to us.

        The completion handler of stopCapture only says the stream will
        produce no NEW samples; samples already enqueued on our serial
        dispatch queue still run afterwards, on a GCD worker thread, inside
        Python. Witnessed 2026-10-07: a daemon finished its clean goodbye,
        returned 0, and died with SIGKILL during interpreter teardown — a
        late frame was still in numpy when Python finalized, called
        pthread_exit on a thread it does not own, and Foundation trapped.
        Every ordinary stop became a crash report and exit 137.

        So: refuse late frames at the door (`_closed`), detach the output,
        and drain the serial queue with an empty synchronous block — when
        it returns, nothing of ours is running on that queue.
        """
        if self._stream is None:
            return
        self._closed = True
        stopped = threading.Event()

        def _stopped(_err) -> None:
            stopped.set()

        self._stream.stopCaptureWithCompletionHandler_(_stopped)
        stopped.wait(self.START_TIMEOUT_S)
        queue = self._queue
        try:
            import ScreenCaptureKit as SCK
            self._stream.removeStreamOutput_type_error_(
                self._output, SCK.SCStreamOutputTypeScreen, None)
        except Exception:                                   # noqa: BLE001
            pass
        if queue is not None:
            try:
                import libdispatch

                def _drained() -> None:
                    return None

                libdispatch.dispatch_sync(queue, _drained)
            except Exception:                               # noqa: BLE001
                pass
        self._stream = None
        self._output = None
        self._queue = None


class MotionGatedCapture:
    """World / Motion sense stream + Extraction / Diff kernel, wired
    together (ruling z): a low-res SCStreamSession feeds the diff kernel
    ONLY (never a reading Retina); a change verdict fires one full-res
    reading-eye capture, debounced so one edit's multi-region churn does
    not re-trigger for CHANGE_TRIGGER_COOLDOWN_MS. Fixed-cadence full-res
    capture is banned by the same ruling — this is the change-gated
    replacement path for it.

    Does not fork SCStreamSession: both the motion eye and each one-shot
    full-res grab are plain SCStreamSession instances, parameterized.
    """

    def __init__(self, display: str = "main",
                 motion_width: int = MOTION_WIDTH, motion_height: int = MOTION_HEIGHT,
                 motion_fps: float = MOTION_FPS,
                 peripheral: Optional[Any] = None,
                 on_full_res_frame: Optional[Callable[[FrameRecord], Any]] = None,
                 trigger_cooldown_ms: int = CHANGE_TRIGGER_COOLDOWN_MS,
                 session_factory: Callable[..., "SCStreamSession"] = None,
                 clock: Callable[[], float] = time.time):
        from tracemem.extraction.peripheral import Peripheral  # local: avoid capture->extraction import at module load

        self.display = display
        self.peripheral = peripheral or Peripheral()
        self._on_full_res_frame = on_full_res_frame
        self._trigger_cooldown_ms = trigger_cooldown_ms
        self._session_factory = session_factory or SCStreamSession
        self._clock = clock
        self._last_trigger_ms: Optional[int] = None
        self.full_res_triggers = 0
        self.last_error: Optional[str] = None

        self.motion = self._session_factory(
            retina=None, display=display, fps=motion_fps,
            width=motion_width, height=motion_height,
            capture_geometry=True, on_frame=self._on_motion_frame,
        )

    # -- motion-sense sink: every low-res frame runs through the kernel ---
    def _on_motion_frame(self, record: FrameRecord) -> None:
        from tracemem.extraction.peripheral import feed_motion_frame

        events = feed_motion_frame(self.peripheral, record.frame, record.t_ms)
        if any(e.kind == "change" for e in events):
            self._maybe_trigger_full_res(record.t_ms)

    def _maybe_trigger_full_res(self, t_ms: int) -> None:
        if (self._last_trigger_ms is not None
                and t_ms - self._last_trigger_ms < self._trigger_cooldown_ms):
            return
        self._last_trigger_ms = t_ms
        self.full_res_triggers += 1
        self._capture_one_full_res_frame()

    # -- the triggered full-res reading-eye capture -----------------------
    def _capture_one_full_res_frame(self) -> None:
        """One real, full-resolution ScreenCaptureKit frame — reuses
        SCStreamSession (no fork): start a fresh full-res session, wait
        for exactly one frame, stop it. Errors are recorded, never raised
        (a failed reading-eye grab must not kill the motion-sense loop)."""
        got = threading.Event()
        holder: Dict[str, FrameRecord] = {}

        def _sink(record: FrameRecord) -> None:
            holder["record"] = record
            got.set()

        session = self._session_factory(
            retina=None, display=self.display, fps=READING_EYE_GRAB_FPS,
            on_frame=_sink,
        )
        try:
            session.start()
            if not got.wait(2.0):
                self.last_error = "full-res trigger timed out waiting for a frame"
                return
        except Exception as exc:
            self.last_error = repr(exc)
            return
        finally:
            session.stop()
        record = holder.get("record")
        if record is not None and self._on_full_res_frame is not None:
            self._on_full_res_frame(record)

    def start(self) -> None:
        self.motion.start()

    def stop(self) -> None:
        self.motion.stop()


class ScreencaptureFallbackSession:
    """STAND-IN ONLY (see module docstring) — not ScreenCaptureKit. Proves
    the ring-intake path using the `screencapture` CLI on a timer. Capture
    only: `frame` is the raw JPEG bytes, opaque to the ring and to us.
    """

    STAND_IN = True   # load-bearing marker: never mistake this for the real wrapper

    def __init__(self, retina: Retina, display: str = "main",
                 interval_s: float = DEFAULT_INTERVAL_S, display_index: int = 1,
                 grab: Optional[Callable[[], bytes]] = None,
                 clock: Callable[[], float] = time.time):
        self.retina = retina
        self.display = display
        self.interval_s = interval_s
        self.display_index = display_index
        self._grab = grab or self._grab_via_screencapture
        self._clock = clock
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self.frames_landed = 0
        self.last_error: Optional[str] = None

    # -- lifecycle -----------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None

    # -- the loop --------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop_evt.is_set():
            t0 = time.monotonic()
            self._capture_once()
            elapsed = time.monotonic() - t0
            self._stop_evt.wait(max(0.0, self.interval_s - elapsed))

    def _capture_once(self) -> None:
        try:
            payload = self._grab()
        except Exception as exc:                    # capture-only: never crash the loop
            self.last_error = repr(exc)
            return
        record = FrameRecord(
            t_ms=int(self._clock() * 1000),
            display=self.display,
            frame=payload,           # opaque bytes; Retina never inspects them
            nbytes=len(payload),
        )
        self.retina.push(record)
        self.frames_landed += 1

    # -- the only place that shells out -----------------------------------
    def _grab_via_screencapture(self) -> bytes:
        fd, path = tempfile.mkstemp(suffix=".jpg")
        os.close(fd)
        try:
            r = subprocess.run(
                ["screencapture", "-x", "-t", "jpg", f"-D{self.display_index}", path],
                capture_output=True, timeout=5,
            )
            if r.returncode != 0 or not os.path.exists(path):
                raise RuntimeError(
                    f"screencapture exit={r.returncode} stderr={r.stderr!r}"
                )
            with open(path, "rb") as fh:
                return fh.read()
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
