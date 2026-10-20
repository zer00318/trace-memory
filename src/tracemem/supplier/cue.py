"""OWNS: query → Cue via deterministic heuristics; no-signal → HYBRID (05 §1, A3)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from tracemem.domain.time import TimeRange


class Precision(Enum):
    PATTERN = "pattern"
    FACT = "fact"
    VERBATIM = "verbatim"
    HYBRID = "hybrid"

_VERBATIM_RE = re.compile(r'"[^"]+"|\bexact(?:ly)?\b|\bword for word\b|\bverbatim\b', re.I)
_PATTERN_RE = re.compile(r"\busually\b|\bhow often\b|\bpattern\b|\brecurring\b|\bhabit\b|\btypically\b|\banything i should\b", re.I)
_FACT_RE = re.compile(r"\bwhat\b|\bwhen\b|\bwhere\b|\bwho\b|\bwhich\b|\bdid\b", re.I)
_LAST_WEEK_MS = 7 * 24 * 3600 * 1000


@dataclass(frozen=True)
class Cue:
    text: str
    precision: Precision
    t_hint: Optional[TimeRange] = None
    place_hint: Optional[str] = None
    # THE RESOLVED WINDOW, when the question named a time. Kept beside
    # `t_hint` rather than replacing it: `t_hint` is a HINT the packer uses to
    # widen a claim drill, while this is an ASSERTION that the question is
    # about a period, which routes to a different retrieval path entirely.
    # Collapsing the two would make every question mentioning "yesterday"
    # abandon the node graph, including "who did I meet yesterday".
    window: Optional[object] = None


def parse_cue(text: str, clock=None, activity=None) -> Cue:
    if _VERBATIM_RE.search(text):
        p = Precision.VERBATIM
    elif _PATTERN_RE.search(text):
        p = Precision.PATTERN
    elif _FACT_RE.search(text):
        p = Precision.FACT
    else:
        p = Precision.HYBRID          # A3: never guess an extreme without signal
    t_hint = None
    window = None
    if clock is not None:
        from tracemem.supplier.time_phrases import find_range
        now = clock.now_ms()
        window = find_range(text, now, activity)
        if window is not None:
            t_hint = TimeRange(window.t0_ms, window.t1_ms)
        elif re.search(r"\blast week\b|\bthis week\b|\byesterday\b", text, re.I):
            t_hint = TimeRange(now - _LAST_WEEK_MS, now)
    return Cue(text=text, precision=p, t_hint=t_hint, window=window)
