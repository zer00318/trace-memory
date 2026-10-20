"""OWNS: the cost cascade wired end to end (spec 01 §11).

change gate → blur gate → cheap extract (OCR) → scene change → route →
summon specialist → build claims → post TEXT to hub → DELETE the frame.

X1: only Claims cross `hub.post_claims`; the frame dies here, seconds old.
X3: nothing GPU-shaped (vlm/specialist) runs unless the cheap gates passed.
X6: any gate failure degrades to "read" — the cascade can only SAVE work.

Ports arrive via PipelineDeps; per-stream memory lives in PipelineState.
The daemon owns the loop; this function owns ONE frame's fate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List

from tracemem.binder.cross_pillar import SensorCtx
from tracemem.extraction.blur_gate import is_readable
from tracemem.extraction.change_gate import Verdict, decide_read
from tracemem.extraction.claim_builder import build_claim
from tracemem.extraction.coordinate_builder import build_stack
from tracemem.extraction.router import route
from tracemem.extraction.scene_change import is_new_activity


@dataclass
class PipelineDeps:
    ocr: object                              # ports.OCR
    hub: object                              # .post_claims(list[Claim])
    registry: object = None                  # SpecialistRegistry (None: cheap-only)
    vlm: object = None                       # gist/classify port (None on known-app Macs)
    embedder: object = None                  # phone-pillar scene change
    deleter: Callable = lambda frame: None   # pixel destruction witness (X1)


@dataclass
class PipelineState:
    last_frame: object = None
    last_read_frame: object = None
    last_read_t_ms: int = 0
    last_read_text: str = ""             # for frame-to-frame volition cadence (§1.9)
    media_streak: int = 0
    last_ctx: dict = field(default_factory=dict)
    specialist: object = None
    vlm_calls: int = 0
    shed_log: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class FrameOutcome:
    verdict: str                 # skipped|dropped_blur|read|media
    claims_posted: int
    used_gpu: bool


def process(frame, ctx: dict, sensor_ctx: SensorCtx, deps: PipelineDeps,
            state: PipelineState, t_ms: int) -> FrameOutcome:
    try:
        # 1. change gate (~ms, pure) — the flow-control valve
        gate = decide_read(frame, state.last_read_frame, state.last_read_t_ms,
                           t_ms, state.media_streak)
        state.media_streak = gate.media_streak
        state.last_frame = frame
        if gate.verdict is Verdict.SKIP:
            deps.deleter(frame)
            return FrameOutcome("skipped", 0, False)
        if gate.verdict is Verdict.MEDIA:
            deps.deleter(frame)                       # titles+audio carry media (01 §2)
            return FrameOutcome("media", 0, False)

        # 2. blur gate (~ms, pure) — unreadable frames buy nothing
        if not is_readable(frame):
            state.shed_log.append("blur@%d" % t_ms)   # no silent truncation
            deps.deleter(frame)
            return FrameOutcome("dropped_blur", 0, False)

        # 3. cheap extraction (CPU) — verbatim spans with bboxes
        spans = deps.ocr.read(frame)

        # 4. scene change → routing (GPU only past this line, X3)
        used_gpu = False
        if is_new_activity(ctx, state.last_ctx, frame, state.last_frame,
                           embedder=deps.embedder):
            activity, vlm_calls = route(frame, ctx, deps.vlm)
            state.vlm_calls += vlm_calls
            used_gpu = used_gpu or vlm_calls > 0
            if deps.registry is not None:
                state.specialist = deps.registry.summon(activity)
        state.last_ctx = dict(ctx)

        # 5. specialist steady-state read (GPU) — only when one is resident
        reads = []
        if state.specialist is not None:
            reads = [dict(r, modality=r.get("modality", "screen"))
                     for r in state.specialist.read(frame)]
            used_gpu = True

        # 6. claims: every span carries its bbox into the stack (P2.1)
        claims = []
        for s in spans:
            stack, _co = build_stack(tuple(s["bbox"]) if s.get("bbox") else None,
                                     ctx, sensor_ctx, t_ms)
            claims.append(build_claim(
                {"text": s["text"], "confidence": s.get("confidence", 0.9)},
                stack, "ocr"))
        for r in reads:
            stack, _co = build_stack(None, ctx, sensor_ctx, t_ms)
            claims.append(build_claim(r, stack, "specialist:%s" %
                                      getattr(state.specialist, "name", "eye")))

        # 6b. volition from FRAME-to-frame cadence (the actions dimension, §1.9):
        # content that grew = you were writing (chosen); a wholesale jump = you
        # navigated (chosen); a still frame = reading (unknown). Stamped here, at
        # the only place that sees the frame sequence.
        from tracemem.extraction.actions import stamp_frame_volition
        frame_text = " ".join(c.text for c in claims)
        claims = stamp_frame_volition(claims, state.last_read_text, frame_text)
        state.last_read_text = frame_text

        # 7. the trust boundary: TEXT leaves, pixels die (X1)
        deps.hub.post_claims(claims)
        state.last_read_frame = frame
        state.last_read_t_ms = t_ms
        deps.deleter(frame)
        return FrameOutcome("read", len(claims), used_gpu)
    except Exception:
        # X6 at cascade level: a wiring fault must not kill capture — the
        # daemon writes a gap witness and the loop continues; the frame still dies.
        deps.deleter(frame)
        raise
