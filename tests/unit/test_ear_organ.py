"""The ambient ear is continuous, bounded, local, and claim-only."""
from __future__ import annotations

import queue
import threading
import time
from types import SimpleNamespace

import pytest

from tracemem.app.ear_organ import (
    AudioCaptureError,
    AudioPermissionDenied,
    EarOrgan,
    SignedAudioRecorder,
)
from tracemem.app.witness import WitnessKind
from tracemem.domain import Modality
from tracemem.extraction.asr_local import LocalASR, Utterance


def eventually(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


class Clock:
    def __init__(self, start=1_000):
        self.value = start
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            self.value += 100
            return self.value


class Recorder:
    """A recorder whose next chunks are controlled by the test."""

    def __init__(self, initial=()):
        self.items = queue.Queue()
        for item in initial:
            self.items.put(item)
        self.cancelled = False
        self.validated = False

    def validate(self):
        self.validated = True

    def capture(self, seconds):
        item = self.items.get(timeout=5)
        if isinstance(item, BaseException):
            raise item
        return item

    def cancel(self):
        self.cancelled = True
        self.items.put(AudioCaptureError("cancelled"))


class ASR:
    def __init__(self, result=None, error=None):
        self.result = result or [Utterance(
            "hello from the room", 20, 420, .8,
            (("hello", 20, 100), ("room", 300, 420)))]
        self.error = error
        self.seen = []

    def transcribe_bytes(self, audio):
        self.seen.append(audio)
        if self.error:
            raise self.error
        return self.result


class Sink:
    def __init__(self):
        self.claims = []
        self.lock = threading.Lock()

    def __call__(self, claim):
        with self.lock:
            self.claims.append(claim)
        return True


def witness_kinds(sink):
    return [c.structured.get("witness") for c in sink.claims
            if c.structured and "witness" in c.structured]


def test_audio_becomes_a_fully_bound_mic_claim_and_not_a_waveform(monkeypatch):
    monkeypatch.setattr("tracemem.capture.machine_sense.read_ssid",
                        lambda: "home")
    recorder = Recorder([b"RIFF private waveform"])
    sink = Sink()
    ear = EarOrgan(sink, recorder=recorder, asr=ASR(), chunk_seconds=.1,
                   clock=Clock(), private_ssids={"home"})

    ear.start()
    eventually(lambda: any(c.provenance.modality is Modality.MIC
                           for c in sink.claims))
    ear.stop()

    claim = next(c for c in sink.claims
                 if c.provenance.modality is Modality.MIC)
    assert recorder.validated and recorder.cancelled
    assert claim.text == "hello from the room"
    assert claim.provenance.perceiver == "asr"
    assert claim.provenance.source_ids == (claim.coord.capture.frame_id,)
    assert claim.coord.capture.frame_id.startswith("audio-")
    assert claim.coord.capture.device_id == "mac-1"
    assert claim.coord.world.place_key != "home"  # private SSID is not stored
    assert claim.bidder == "ear:ambient"
    assert claim.bid_id and claim.bid_predicted_value == 1.0
    assert claim.actual_cost >= 0 and claim.scope == "frame"
    assert claim.structured["waveform_retained"] is False
    assert claim.structured["t1_ms"] > claim.coord.t_ms
    assert claim.structured["words"][0]["t0_ms"] >= claim.coord.t_ms
    assert b"private waveform" not in repr(claim).encode()
    assert ear.health()["waveforms_destroyed"] >= 1
    assert not hasattr(ear, "store") and not hasattr(ear, "_store")


def test_asr_failure_still_destroys_the_chunk_and_exposes_health():
    recorder = Recorder([b"RIFF secret"])
    ear = EarOrgan(Sink(), recorder=recorder,
                   asr=ASR(error=RuntimeError("decoder failed")),
                   chunk_seconds=.1, clock=Clock())
    ear.start()
    eventually(lambda: ear.errors == 1)
    ear.stop()

    assert ear.waveforms_destroyed >= 1
    assert "decoder failed" in ear.health()["last_error"]


def test_bounded_backpressure_sheds_audio_with_an_interval_witness():
    entered = threading.Event()
    release = threading.Event()

    class BlockingASR(ASR):
        def transcribe_bytes(self, audio):
            entered.set()
            release.wait(2)
            return []

    recorder = Recorder([b"one", b"two", b"three", b"four"])
    sink = Sink()
    ear = EarOrgan(sink, recorder=recorder, asr=BlockingASR(),
                   buffered_chunks=1, chunk_seconds=.1, clock=Clock())
    ear.start()
    assert entered.wait(1)
    eventually(lambda: ear.chunks_dropped >= 1)
    release.set()
    ear.stop()

    witnesses = [c for c in sink.claims if c.structured and
                 c.structured.get("witness") == WitnessKind.OVERWHELM.value]
    assert len(witnesses) == 1
    assert witnesses[0].structured["t2_ms"] > witnesses[0].coord.t_ms
    assert witnesses[0].structured["organ"] == "ear"
    assert witnesses[0].structured["dropped_chunks"] == ear.chunks_dropped
    assert ear.waveforms_destroyed == ear.chunks_captured


def test_permission_denial_is_one_honest_hole_and_regain_is_recorded():
    recorder = Recorder([
        AudioPermissionDenied("microphone permission denied"),
        AudioPermissionDenied("microphone permission denied"),
        b"RIFF after grant",
    ])
    sink = Sink()
    ear = EarOrgan(sink, recorder=recorder, asr=ASR(result=[]),
                   chunk_seconds=.1, retry_seconds=.01, clock=Clock())
    ear.start()
    eventually(lambda: WitnessKind.PERMISSION_REGAIN.value in
               witness_kinds(sink))
    ear.stop()

    kinds = witness_kinds(sink)
    assert kinds.count(WitnessKind.ORGAN_START_FAILED.value) == 1
    assert kinds.count(WitnessKind.PERMISSION_REGAIN.value) == 1
    assert ear.health()["permission_missing"] is False


def test_an_undeliverable_native_permission_sheet_is_not_a_generic_timeout(
        monkeypatch):
    class Process:
        returncode = 70
        def communicate(self, timeout):
            return b"", b"TRACE helper: microphone permission decision timed out"

    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: Process())
    recorder = SignedAudioRecorder("/Applications/TRACE.app/Contents/MacOS/TRACE")

    with pytest.raises(AudioPermissionDenied):
        recorder.capture(15)


def test_stop_cancels_an_active_native_capture():
    recorder = Recorder()
    ear = EarOrgan(Sink(), recorder=recorder, asr=ASR(),
                   chunk_seconds=.1, clock=Clock())
    ear.start()
    ear.stop()
    assert recorder.cancelled
    assert not ear.alive()


def test_cpu_ear_does_not_reserve_the_gpu_from_the_night(tmp_path):
    """Only the eye owns Spotlight's capture side.  Treating any live organ
    as GPU capture would make an always-on ear starve reflection forever."""
    from tracemem.__main__ import run

    class FakeEar:
        name = "ear"

        def __init__(self):
            self.running = False

        def start(self): self.running = True
        def stop(self): self.running = False
        def alive(self): return self.running

    class Spotlight:
        def __init__(self):
            self.values = []

        def set_capture(self, active):
            self.values.append(bool(active))

    spot = Spotlight()
    report = run(store_path=str(tmp_path / "raw.sqlite3"),
                 lock_path=str(tmp_path / "trace.lock"),
                 seconds=.12, tick_seconds=.03, organs=[FakeEar()],
                 spotlight=spot)

    assert spot.values and not any(spot.values)
    assert report["eyeless"] is True
    assert report["earless"] is False


def test_local_asr_accepts_an_in_memory_wav_without_a_temp_path():
    seen = {}

    class Model:
        def transcribe(self, audio, **kwargs):
            seen["audio"] = audio
            seen["kwargs"] = kwargs
            word = SimpleNamespace(word=" hello ", start=.1, end=.2)
            segment = SimpleNamespace(text=" hello ", start=.1, end=.2,
                                      avg_logprob=-.1, words=[word])
            return iter([segment]), SimpleNamespace()

    asr = LocalASR()
    asr._model = Model()
    out = asr.transcribe_bytes(b"RIFF bytes that never touch a path")

    assert out[0].text == "hello"
    assert out[0].words == (("hello", 100, 200),)
    assert seen["kwargs"]["word_timestamps"] is True
    assert seen["audio"].closed
