"""OWNS: a period summary that is COUNTED, not written.

WHY IT EXISTS (witnessed 2026-10-09 on the live daemon). Two questions in a
row — "which apps did I use most today?" and "what was I doing in the last
hour?" — were answered with a dump of raw OCR fragments, because the never-
lie guard (rightly) refused the model's prose: once for "The user was looking
at a MacBook Pro", once for turning a YouTube title, "Baking With Chef Ana
About Fitness Wearables", into "I was baking with Chef Ana". The guard was
correct both times; the fallback was what failed the founder.

A period has an answer that needs no model at all: how long each app was in
front, and what each was showing. Every number here is a sum of moment
durations and every title is one that was read, so this cannot invent
anything — it is what the guard falls back to, and it is the WHOLE answer for
questions that ask for a ranking ("which apps", "how long", "screen time").
"""
from __future__ import annotations

import re
import time
from typing import Dict, List, Optional, Sequence, Tuple

_RANKING = re.compile(
    r"\b(?:which|what)\s+(?:apps?|applications?|programs?|websites?|sites?)\b"
    r"|\bmost\s+(?:used|time)\b|\bhow\s+(?:long|much\s+time)\b"
    r"|\bscreen\s*time\b|\btime\s+(?:spent|on)\b",
    re.IGNORECASE)

MAX_APPS = 6
MAX_TITLES = 3


def is_ranking_question(question: str) -> bool:
    return bool(_RANKING.search(question or ""))


def _minutes(ms: int) -> str:
    m = int(round(ms / 60000.0))
    if m < 1:
        return "under a minute"
    if m < 60:
        return "%d min" % m
    return "%dh %02dm" % (m // 60, m % 60)


def summarize(moments: Sequence, cite_id, caveat: Optional[str] = None,
              phrase: str = "this period",
              clock_fn=None) -> Tuple[str, Tuple[str, ...]]:
    """(text, citations) for a period, from its moments alone."""
    clock_fn = clock_fn or (
        lambda ms: time.strftime("%H:%M", time.localtime(ms / 1000.0)))
    by_app: Dict[str, int] = {}
    titles: Dict[str, Dict[str, int]] = {}
    cites: Dict[str, List[str]] = {}
    for m in moments:
        app = m.app or "unknown app"
        by_app[app] = by_app.get(app, 0) + m.duration_ms
        label = m.label()
        if label and label != app:
            t = titles.setdefault(app, {})
            t[label] = t.get(label, 0) + m.duration_ms
        if m.claim_ids:
            cites.setdefault(app, []).append(cite_id(m.claim_ids[0]))
    if not by_app:
        return ("I have nothing on screen for %s." % phrase, ())

    ranked = sorted(by_app.items(), key=lambda kv: -kv[1])
    lines = []
    head = "On screen during %s" % phrase
    if caveat:
        head += " (%s)" % caveat.rstrip(".")
    lines.append(head + ":")
    used: List[str] = []
    for app, ms in ranked[:MAX_APPS]:
        line = "• %s — %s" % (app, _minutes(ms))
        top = sorted((titles.get(app) or {}).items(), key=lambda kv: -kv[1])
        if top:
            line += ": " + "; ".join('"%s"' % t[:70] for t, _ in top[:MAX_TITLES])
        ids = cites.get(app) or []
        if ids:
            line += " [%s]" % ids[0]
            used.append(ids[0])
        lines.append(line)
    if len(ranked) > MAX_APPS:
        rest = sum(ms for _, ms in ranked[MAX_APPS:])
        lines.append("• %d other app(s) — %s" % (len(ranked) - MAX_APPS,
                                                  _minutes(rest)))
    longest = max(moments, key=lambda m: m.duration_ms)
    if longest.duration_ms >= 5 * 60000:
        lines.append("Longest stretch: %s–%s in %s%s." % (
            clock_fn(longest.t0_ms), clock_fn(longest.t1_ms),
            longest.app or "an app",
            (' ("%s")' % longest.label()[:70]
             if longest.label() != longest.app else "")))
    return "\n".join(lines), tuple(dict.fromkeys(used))
