"""OWNS: t_ms conventions + TimeRange window ops (spec 00 §8)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TimeRange:
    start_ms: int
    end_ms: int

    def __post_init__(self) -> None:
        if self.end_ms < self.start_ms:
            raise ValueError("end_ms must be >= start_ms")

    def overlaps(self, other: "TimeRange") -> bool:
        return self.start_ms <= other.end_ms and other.start_ms <= self.end_ms

    def contains(self, t_ms: int) -> bool:
        return self.start_ms <= t_ms <= self.end_ms
