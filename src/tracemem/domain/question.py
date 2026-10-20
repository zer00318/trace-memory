"""OWNS: Question — a clarifying question competing for the attention budget
(spec 00, spec 04 birth/question_gen, spec 08 §5)."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Tuple


class QuestionStatus(Enum):
    QUEUED = "queued"
    ASKED = "asked"
    ANSWERED = "answered"
    EXPIRED = "expired"


@dataclass
class Question:
    id: str
    text: str
    about_node_ids: Tuple[str, ...]
    info_gain: float
    created_t_ms: int
    status: QuestionStatus = QuestionStatus.QUEUED
