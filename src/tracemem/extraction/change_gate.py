"""OWNS: pure change-gate verdict — the flow-control valve (spec 01 §2).

Count-based block diff (mean diffs wash out sparse text). X6: any failure
degrades to READ — the gate can only SAVE work, never blind us. Pure: frames
arrive as 2D int grids (the adapter downscales); no PIL here.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Sequence

GRID_X, GRID_Y = 16, 10
PIX_DELTA = 12
BLOCK_FRAC = 0.02
READ_FLOOR_FRAC = 0.012
FORCE_READ_MS = 300_000
MEDIA_FRAC = 0.15
MEDIA_STREAK_N = 4

Frame = Sequence[Sequence[int]]  # downscaled grayscale rows


class Verdict(Enum):
    READ = "read"
    SKIP = "skip"
    MEDIA = "media"


@dataclass(frozen=True)
class GateResult:
    verdict: Verdict
    changed_frac: float
    media_streak: int


def decide_read(frame: Frame, last: Optional[Frame], last_read_t_ms: int,
                t_ms: int, media_streak: int) -> GateResult:
    try:
        if last is None:
            return GateResult(Verdict.READ, 1.0, 0)
        frac = _changed_block_frac(frame, last)
        if frac < READ_FLOOR_FRAC:
            if t_ms - last_read_t_ms >= FORCE_READ_MS:
                return GateResult(Verdict.READ, frac, 0)   # timestamp refresh
            return GateResult(Verdict.SKIP, frac, 0)
        if frac >= MEDIA_FRAC:
            streak = media_streak + 1
            if streak >= MEDIA_STREAK_N:
                return GateResult(Verdict.MEDIA, frac, streak)
            return GateResult(Verdict.READ, frac, streak)
        return GateResult(Verdict.READ, frac, 0)
    except Exception:
        return GateResult(Verdict.READ, 1.0, 0)            # X6: fail open


def _changed_block_frac(frame: Frame, last: Frame) -> float:
    h, w = len(frame), len(frame[0])
    bh, bw = max(1, h // GRID_Y), max(1, w // GRID_X)
    changed_blocks = 0
    total_blocks = 0
    for by in range(0, h - bh + 1, bh):
        for bx in range(0, w - bw + 1, bw):
            total_blocks += 1
            changed = sum(
                1
                for y in range(by, by + bh)
                for x in range(bx, bx + bw)
                if abs(frame[y][x] - last[y][x]) > PIX_DELTA
            )
            if changed / float(bh * bw) > BLOCK_FRAC:
                changed_blocks += 1
    return changed_blocks / float(total_blocks) if total_blocks else 1.0
