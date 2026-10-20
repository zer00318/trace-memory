"""SCStreamSession.stop() must leave nothing of ours running on the queue.

WITNESSED 2026-10-07: a daemon wrote its clean-stop witness, returned 0, and
died with SIGKILL (exit 137) during interpreter teardown. A frame callback
enqueued before stopCapture's completion handler fired was still executing
Python (numpy, in the peripheral) on a GCD worker thread when the interpreter
finalized; Python called pthread_exit on a thread it does not own and
Foundation trapped. Every ordinary stop produced a crash report.
"""
import threading
import time

import pytest

libdispatch = pytest.importorskip("libdispatch")

from tracemem.capture.retina_screen import SCStreamSession  # noqa: E402


class _FakeStream:
    def __init__(self):
        self.removed = False

    def stopCaptureWithCompletionHandler_(self, handler):
        handler(None)

    def removeStreamOutput_type_error_(self, output, otype, err):
        self.removed = True
        return True


def _session(reader):
    return SCStreamSession(on_frame=lambda rec: None, pixel_reader=reader,
                           import_probe=lambda: True)


def test_stop_waits_out_a_frame_already_on_the_queue():
    s = _session(lambda sb: None)
    s._stream = _FakeStream()
    s._queue = libdispatch.dispatch_queue_create(b"test.drain", None)
    finished = threading.Event()

    def slow_frame() -> None:
        time.sleep(0.3)
        finished.set()

    libdispatch.dispatch_async(s._queue, slow_frame)
    time.sleep(0.02)                       # let the worker pick it up
    s.stop()
    assert finished.is_set(), "stop() returned while a frame was still running"
    assert s._stream is None and s._queue is None


def test_a_late_frame_after_stop_is_refused_at_the_door():
    calls = []
    s = _session(lambda sb: calls.append(sb) or None)
    s._stream = _FakeStream()
    s._queue = libdispatch.dispatch_queue_create(b"test.late", None)
    s.stop()
    s._on_sample_buffer(object())
    assert calls == [], "a frame delivered after stop() reached the pipeline"
