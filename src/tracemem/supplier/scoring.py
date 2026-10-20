"""OWNS: one rankable currency: sim × grade × time × activation (05 §3)."""
from __future__ import annotations

from tracemem.domain import Grade
from tracemem.domain.activation import activation_now

WEIGHTS = dict(sim=0.4, grade=0.25, time=0.2, act=0.15)
_GRADE_FACTOR = {Grade.VERBATIM: 1.0, Grade.SPECIALIST: 0.85, Grade.GIST: 0.6,
                 Grade.DERIVED: 0.45, Grade.FLOOR: 0.2}


def grade_factor(g: Grade) -> float:
    return _GRADE_FACTOR[g]


def score(sim: float, grade: Grade, t_ms: int, item_t_ms: int,
          activation: float, t_hint=None) -> float:
    if t_hint is not None:
        time_f = 1.0 if t_hint.contains(item_t_ms) else 0.15   # hint match dominates
    else:
        age_days = max(0.0, (t_ms - item_t_ms) / 86_400_000.0)
        time_f = 1.0 / (1.0 + age_days / 30.0)                 # gentle recency
    return (WEIGHTS["sim"] * sim + WEIGHTS["grade"] * grade_factor(grade)
            + WEIGHTS["time"] * time_f + WEIGHTS["act"] * activation)
