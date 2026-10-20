"""OWNS: verbs from change cadence — the actions dimension (SCHEMA §1.9,
founder-ratified: 'verbs come from change cadence: scroll vs type vs watch, and
content diffs — no new hardware').

The producer the preference derivation was waiting for. Watching the content of
one app change over consecutive claims tells you the MODE of engagement without
any input capture:
  · writing  — the text grows incrementally (you are producing it) → VOLITIONAL
  · browsing — the text changes wholesale, fast (you are navigating)  → VOLITIONAL
  · reading  — the text sits still (you are consuming it)             → unknown
  · idle     — nothing there

Volition (chosen vs imposed) is read off the verb CONSERVATIVELY: only active
production and navigation count as chosen; passive reading stays unknown, so the
'loves inbox' guard holds — scrolling a feed you opened is a choice, but merely
having mail on screen is not. This never guesses beyond what the cadence shows.

Lexicon-free (L11): it compares token SETS and their growth, never a word list.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import Dict, List, Optional

BROWSE_JACCARD = 0.3           # below this overlap between consecutive frames = wholesale change

_VOLITION = {"writing": True, "browsing": True, "reading": None, "idle": None}


def _tokens(text: str):
    return {w for w in text.lower().split() if len(w) > 2}


def _verb(prev_text: str, cur_text: str) -> str:
    pt, ct = _tokens(prev_text), _tokens(cur_text)
    if not ct:
        return "idle"
    if pt and pt <= ct and len(ct) > len(pt):
        return "writing"                            # grew, keeping everything before
    union = len(pt | ct) or 1
    if len(pt & ct) / union < BROWSE_JACCARD:
        return "browsing"                           # little in common = a jump
    return "reading"                                # mostly the same text, sitting there


def infer_verbs(claims) -> Dict[str, str]:
    """claim id -> engagement verb, from the cadence within each app. The first
    claim of an app has no predecessor, so it is 'reading' (a neutral baseline),
    never inferred as an active act."""
    by_app: Dict[Optional[str], list] = defaultdict(list)
    for c in sorted(claims, key=lambda c: c.coord.t_ms):
        by_app[c.coord.capture.app].append(c)
    out: Dict[str, str] = {}
    for _app, seq in by_app.items():
        prev = None
        for c in seq:
            out[c.id] = "reading" if prev is None else _verb(prev.text, c.text)
            prev = c
    return out


def volition_from_verb(verb: str) -> Optional[bool]:
    return _VOLITION.get(verb)


def stamp_frame_volition(claims, prev_frame_text: str, cur_frame_text: str) -> List:
    """Stamp a whole frame's claims with the volition read from how this frame's
    content changed from the last — the true frame-to-frame cadence the live
    pipeline sees. Unknown (passive) leaves them untouched."""
    v = volition_from_verb(_verb(prev_frame_text, cur_frame_text))
    if v is None:
        return list(claims)
    return [replace(c, structured={**(c.structured or {}), "initiated": v})
            for c in claims]


def with_volition(claims) -> List:
    """Return claims with `structured['initiated']` set wherever the cadence
    shows a chosen act; unchanged where it does not (unknown stays unstamped)."""
    claims = list(claims)
    verbs = infer_verbs(claims)
    out = []
    for c in claims:
        v = volition_from_verb(verbs.get(c.id))
        if v is None:
            out.append(c)
            continue
        merged = dict(c.structured or {})
        merged["initiated"] = v
        out.append(replace(c, structured=merged))
    return out
