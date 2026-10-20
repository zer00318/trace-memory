"""OWNS: local ASR adapter (impl ports.ASR); CPU-only (01 R4)

The ear — speech in, words with time out. faster-whisper on CPU int8:
the GPU belongs to the eye and the mouth by law, never the ear. Model
weights load lazily and once; the cached Systran/faster-whisper-base is
the starting engine (found cached 2026-07-19 — measure before replacing).

Founder-voice WER remains gold-gated (F2); this adapter being real is a
separate fact from it being MEASURED, and the map says which is which.

SPEC: 01 s4
ORACLE: real audio -> utterances with t0/t1 + words; corpus reads existing
        paths while the ambient ear reads in-memory WAV bytes.
"""
from __future__ import annotations

import io
import os
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class Utterance:
    text: str
    t0_ms: int
    t1_ms: int
    confidence: float                        # exp(avg_logprob), rough but honest
    words: Tuple[Tuple[str, int, int], ...]  # (word, t0_ms, t1_ms)


class LocalASR:
    """ports.ASR adapter. One model instance per process; CPU only."""

    def __init__(self, model: str = "base"):
        self._model_name = model
        self._model = None

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            # local_files_only=True — THE EAR DOES NOT PHONE HOME.
            #
            # Found 2026-07-23 by a suite that suddenly took ten minutes:
            # test_asr_adapter_transcribes alone was 603.80s at 0% CPU. The
            # model was already cached; the time was a HuggingFace hub
            # revision check reaching out to the network on every single
            # load and timing out slowly.
            #
            # The speed is the least of it. packaging/entitlements.md
            # declares com.apple.security.network.client = false and calls
            # that "the entitlement that would let a memory of your whole
            # life leave your Mac", and a test asserts it. An organ that
            # opens a network connection to load itself contradicts that
            # in the capture path — quietly, and only when a microphone is
            # in use. It would also mean the ear simply stops working on a
            # plane, in a way no error message would explain.
            #
            # Weights are a build-time artifact, not a runtime dependency.
            self._model = WhisperModel(
                self._model_name, device="cpu", compute_type="int8",
                local_files_only=True)
        return self._model

    def transcribe(self, audio_path, language: Optional[str] = None) -> List[Utterance]:
        if not isinstance(audio_path, (str, os.PathLike)):
            raise TypeError(f"transcribe() needs a filesystem path, got "
                            f"{type(audio_path).__name__}")
        audio_path = os.fspath(audio_path)
        if not os.path.exists(audio_path):
            raise FileNotFoundError(audio_path)

        return self._transcribe(audio_path, language)

    def transcribe_bytes(self, audio: bytes,
                         language: Optional[str] = None) -> List[Utterance]:
        """Transcribe an in-memory encoded chunk; retain no waveform.

        The signed native helper returns a WAV over its stdout pipe. Passing a
        BytesIO directly into faster-whisper/av keeps that waveform off disk,
        and closing it at this boundary makes destruction explicit.
        """
        if not isinstance(audio, bytes):
            raise TypeError("transcribe_bytes() needs immutable bytes")
        if not audio:
            return []
        stream = io.BytesIO(audio)
        try:
            return self._transcribe(stream, language)
        finally:
            stream.close()

    def _transcribe(self, audio, language) -> List[Utterance]:
        import math

        segments, _info = self._load().transcribe(
            audio, language=language, word_timestamps=True,
            # Ambient means long stretches of room tone.  Running the
            # decoder across silence both burns the laptop and hallucinates
            # phrases the microphone never heard.  The bundled Silero VAD is
            # local and CPU-only; it removes non-speech before Whisper while
            # preserving timestamps relative to the original chunk.
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500}, beam_size=1)
        out: List[Utterance] = []
        for seg in segments:
            words = tuple(
                (w.word.strip(), int(w.start * 1000), int(w.end * 1000))
                for w in (seg.words or []) if w.word.strip())
            out.append(Utterance(
                text=seg.text.strip(),
                t0_ms=int(seg.start * 1000),
                t1_ms=int(seg.end * 1000),
                confidence=float(math.exp(seg.avg_logprob)),
                words=words))
        return out
