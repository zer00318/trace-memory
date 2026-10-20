"""The ambient microphone as a supervised, claim-only organ.

The signed native helper owns microphone permission and returns one short WAV
through a pipe.  This organ keeps at most a bounded number of chunks in memory,
transcribes locally on CPU, emits TEXT claims, and releases every waveform on
success, error, overflow, and stop.  It never opens the claim store.
"""
from __future__ import annotations

import os
import queue
import subprocess
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from tracemem.app.witness import WitnessKind, witness
from tracemem.capture.steady_clock import SteadyClock
from tracemem.domain import CaptureCoord, CoordinateStack, SpanCoord, WorldCoord
from tracemem.extraction.claim_builder import build_claim

DEFAULT_CHUNK_SECONDS = 15.0
DEFAULT_BUFFERED_CHUNKS = 2
PERMISSION_RETRY_SECONDS = 30.0
DROP_WITNESS_WINDOW_MS = 60_000


class AudioCaptureError(RuntimeError):
    pass


class AudioPermissionDenied(AudioCaptureError):
    pass


class SignedAudioRecorder:
    """One bounded recording made by TRACE's signed native executable."""

    def __init__(self, helper_path: Optional[str] = None) -> None:
        chosen = helper_path or os.environ.get("TRACE_HELPER_PATH") \
            or "/Applications/TRACE.app/Contents/MacOS/TRACE"
        self.path = Path(chosen)
        self._lock = threading.Lock()
        self._process = None

    def validate(self) -> None:
        if not self.path.is_file() or not os.access(str(self.path), os.X_OK):
            raise AudioCaptureError(
                "ambient ear requires the installed signed TRACE helper")
        result = subprocess.run(
            ["/usr/bin/codesign", "--verify", "--deep", "--strict",
             str(self.path.parent.parent.parent)],
            capture_output=True, timeout=10)
        if result.returncode != 0:
            raise AudioCaptureError("TRACE helper bundle signature is invalid")

    def capture(self, seconds: float) -> bytes:
        process = subprocess.Popen(
            [str(self.path), "--capture-audio", str(float(seconds))],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with self._lock:
            self._process = process
        try:
            audio, error = process.communicate(timeout=float(seconds) + 15.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise AudioCaptureError("signed microphone helper timed out")
        finally:
            with self._lock:
                if self._process is process:
                    self._process = None
        if process.returncode != 0:
            detail = error.decode("utf-8", "replace").strip()[:240]
            if "permission" in detail.lower():
                raise AudioPermissionDenied(detail)
            raise AudioCaptureError(detail or "signed microphone helper failed")
        if not audio:
            raise AudioCaptureError("signed microphone helper returned no audio")
        return audio

    def cancel(self) -> None:
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            process.terminate()


@dataclass
class _Chunk:
    id: str
    started_ms: int
    ended_ms: int
    audio: bytes

    def destroy(self) -> None:
        # Bytes are immutable, so explicit zeroing is unavailable; dropping
        # the sole retained reference is the truthful Python boundary. No
        # waveform is ever appended, serialized, or written by this process.
        self.audio = b""


class EarOrgan:
    name = "ear"

    def __init__(self, submit: Callable, recorder=None, asr=None,
                 chunk_seconds: float = DEFAULT_CHUNK_SECONDS,
                 buffered_chunks: int = DEFAULT_BUFFERED_CHUNKS,
                 retry_seconds: float = PERMISSION_RETRY_SECONDS,
                 device_id: str = "mac-1", clock=None,
                 private_ssids=frozenset()) -> None:
        if chunk_seconds <= 0 or chunk_seconds > 60:
            raise ValueError("ear chunk_seconds must be within 0–60")
        if buffered_chunks < 1:
            raise ValueError("ear needs at least one bounded chunk slot")
        self._submit = submit
        self._recorder = recorder or SignedAudioRecorder()
        self._asr = asr
        self._chunk_seconds = float(chunk_seconds)
        self._retry_seconds = float(retry_seconds)
        self._device = device_id
        self._private_ssids = frozenset(private_ssids)
        self._owned_clock = SteadyClock() if clock is None else None
        self._clock = clock or self._owned_clock.now_ms
        self._chunks = queue.Queue(maxsize=buffered_chunks)
        self._stop = threading.Event()
        self._producer = None
        self._consumer = None
        self._running = False
        self._permission_missing = False
        self._place_key = None
        self._place_read_ms = -1
        # Backpressure is one continuous condition, not one immutable fact
        # per discarded 15-second chunk.  Keep the exact count and interval
        # in memory, then append at most one aggregate witness per minute.
        self._drop_start_ms = None
        self._drop_end_ms = None
        self._drop_count = 0

        self.chunks_captured = 0
        self.chunks_transcribed = 0
        self.chunks_dropped = 0
        self.waveforms_destroyed = 0
        self.claims = 0
        self.errors = 0
        self.last_error = None

    def start(self) -> None:
        if self._running:
            return
        validate = getattr(self._recorder, "validate", None)
        if validate is not None:
            validate()
        self._stop.clear()
        self._running = True
        self._consumer = threading.Thread(
            target=self._consume, name="trace-ear-asr", daemon=True)
        self._producer = threading.Thread(
            target=self._capture, name="trace-ear-capture", daemon=True)
        self._consumer.start()
        self._producer.start()

    def stop(self) -> None:
        self._running = False
        self._stop.set()
        cancel = getattr(self._recorder, "cancel", None)
        if cancel is not None:
            cancel()
        for thread in (self._producer, self._consumer):
            if thread is not None:
                thread.join(timeout=2.0)
        self._flush_drop_witness()
        self._destroy_waiting()

    def alive(self) -> bool:
        return bool(self._running and self._producer is not None
                    and self._producer.is_alive() and self._consumer is not None
                    and self._consumer.is_alive())

    def _capture(self) -> None:
        while not self._stop.is_set():
            started = self._clock()
            try:
                audio = self._recorder.capture(self._chunk_seconds)
            except AudioPermissionDenied as exc:
                self._record_error(exc)
                if not self._permission_missing:
                    self._permission_missing = True
                    self._submit(witness(
                        WitnessKind.ORGAN_START_FAILED, started,
                        detail="ambient ear cannot read microphone: %s" % exc,
                        device_id=self._device,
                        structured_extra={"organ": "ear",
                                          "permission": "microphone"}))
                self._stop.wait(self._retry_seconds)
                continue
            except BaseException as exc:                    # noqa: BLE001
                if not self._stop.is_set():
                    self._record_error(exc)
                    self._stop.wait(min(5.0, self._retry_seconds))
                continue
            ended = self._clock()
            if self._stop.is_set():
                audio = b""
                self.waveforms_destroyed += 1
                break
            if self._permission_missing:
                self._permission_missing = False
                self._submit(witness(
                    WitnessKind.PERMISSION_REGAIN, ended,
                    detail="microphone granted; ambient ear resumed",
                    device_id=self._device,
                    structured_extra={"organ": "ear",
                                      "permission": "microphone"}))
            chunk = _Chunk(uuid.uuid4().hex, started, ended, audio)
            self.chunks_captured += 1
            try:
                self._chunks.put_nowait(chunk)
            except queue.Full:
                self.chunks_dropped += 1
                chunk.destroy()
                self.waveforms_destroyed += 1
                self._record_drop(started, ended)

    def _record_drop(self, started_ms: int, ended_ms: int) -> None:
        if self._drop_start_ms is None:
            self._drop_start_ms = int(started_ms)
        self._drop_end_ms = int(ended_ms)
        self._drop_count += 1
        if self._drop_end_ms - self._drop_start_ms >= DROP_WITNESS_WINDOW_MS:
            self._flush_drop_witness()

    def _flush_drop_witness(self) -> None:
        if not self._drop_count or self._drop_start_ms is None:
            return
        count = self._drop_count
        start = self._drop_start_ms
        end = self._drop_end_ms if self._drop_end_ms is not None else start
        self._drop_start_ms = self._drop_end_ms = None
        self._drop_count = 0
        self._submit(witness(
            WitnessKind.OVERWHELM, start,
            detail="ambient ear shed %d audio chunk(s) before ASR" % count,
            device_id=self._device, span_t2_ms=end,
            structured_extra={"organ": "ear", "dropped_chunks": count,
                              "queue_maxsize": self._chunks.maxsize}))

    def _consume(self) -> None:
        if self._asr is None:
            from tracemem.extraction.asr_local import LocalASR
            self._asr = LocalASR()
        while not self._stop.is_set():
            try:
                chunk = self._chunks.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                utterances = self._asr.transcribe_bytes(chunk.audio)
                self.chunks_transcribed += 1
                if self._stop.is_set():
                    continue
                for utterance in utterances:
                    if not utterance.text.strip():
                        continue
                    if self._submit(self._claim(chunk, utterance)):
                        self.claims += 1
            except BaseException as exc:                    # noqa: BLE001
                self._record_error(exc)
            finally:
                chunk.destroy()
                self.waveforms_destroyed += 1
        self._destroy_waiting()

    def _destroy_waiting(self) -> None:
        while True:
            try:
                chunk = self._chunks.get_nowait()
            except queue.Empty:
                return
            chunk.destroy()
            self.waveforms_destroyed += 1

    def _claim(self, chunk, utterance):
        t_ms = chunk.started_ms + int(utterance.t0_ms)
        frame_id = "audio-%s" % chunk.id
        coord = CoordinateStack(
            world=WorldCoord(place_key=self._place(t_ms)),
            capture=CaptureCoord.lived(frame_id, self._device, app="ambient"),
            span=SpanCoord(bbox=None), t_ms=t_ms)
        words = [
            {"text": word,
             "t0_ms": chunk.started_ms + int(t0),
             "t1_ms": chunk.started_ms + int(t1)}
            for word, t0, t1 in utterance.words]
        return build_claim(
            {"text": utterance.text.strip(),
             "confidence": max(0.0, min(1.0, utterance.confidence)),
             "modality": "mic", "source_ids": [frame_id],
             "structured": {"t1_ms": chunk.started_ms + int(utterance.t1_ms),
                            "words": words, "waveform_retained": False}},
            coord, "asr", bidder="ear:ambient",
            bid_id="ear:%s:%d" % (chunk.id, utterance.t0_ms),
            bid_predicted_value=1.0,
            actual_cost=max(0.0, (chunk.ended_ms - chunk.started_ms) / 1000.0),
            scope="frame")

    def _place(self, t_ms: int) -> str:
        # SSID is a coarse context, not a per-utterance signal.  Reading it
        # once per minute avoids launching a system command for every ASR
        # segment while still letting a moved laptop change place promptly.
        if (self._place_key is not None
                and t_ms - self._place_read_ms < 60_000):
            return self._place_key
        try:
            from tracemem.capture.machine_sense import read_ssid
            ssid = read_ssid()
        except Exception:                                  # noqa: BLE001
            ssid = None
        from tracemem.binder.cross_pillar import SensorCtx, place_key
        self._place_key = place_key(
            SensorCtx(device_id=self._device, ssid=ssid),
            private_ssids=self._private_ssids)[0]
        self._place_read_ms = t_ms
        return self._place_key

    def _record_error(self, exc) -> None:
        self.errors += 1
        self.last_error = repr(exc)[:240]

    def health(self) -> dict:
        return {"chunks_captured": self.chunks_captured,
                "chunks_transcribed": self.chunks_transcribed,
                "chunks_dropped": self.chunks_dropped,
                "waveforms_destroyed": self.waveforms_destroyed,
                "claims": self.claims, "errors": self.errors,
                "permission_missing": self._permission_missing,
                "buffered_chunks": self._chunks.qsize(),
                "last_error": self.last_error}
