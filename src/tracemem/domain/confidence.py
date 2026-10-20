"""OWNS: evidential confidence, multiplicative combination (spec 00 §3).

A chain exposes its weakest factor: combining never exceeds either input.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Confidence:
    value: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.value <= 1.0:
            raise ValueError("confidence must be in [0,1]")

    def combine(self, other: "Confidence") -> "Confidence":
        return Confidence(self.value * other.value)
