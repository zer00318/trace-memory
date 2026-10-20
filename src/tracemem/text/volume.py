"""OWNS: how much of what the eye stores is signal (read-only measurement).

NAPKIN F-RAW priced raw at ~1.6 MB/day. On 2026-10-14/15 the store grew by
108-124 MB/day, 99% of it one source (the eye's OCR). Whether that is a life
being recorded or one screen being re-read is the question a volume budget
has to answer first, and the answer needs decrypted text, so it runs inside
the signed daemon on its owner thread, bounded by `sample`.

A REPEAT is a claim whose normalized text the same source already stored
from the same app within `window_ms`. It is the napkin's "Signal-Noise
decipherer" measured, not applied: nothing here changes what is stored.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict

_SPACE = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _SPACE.sub(" ", (text or "").strip().lower())


def noise_report(claim_log, sample: int = 4000,
                 window_ms: int = 10 * 60 * 1000) -> Dict:
    sample = max(1, min(int(sample), 20_000))
    rows = claim_log._db.execute(
        "SELECT id FROM claim_link ORDER BY seq DESC LIMIT ?", (sample,)).fetchall()
    claims = [c for c in (claim_log.get(r[0]) for r in reversed(rows)) if c]
    claims.sort(key=lambda c: c.coord.t_ms)
    last_seen = {}
    per = defaultdict(lambda: {"claims": 0, "repeats": 0, "chars": 0,
                               "repeat_chars": 0, "frames": set()})
    for c in claims:
        src = c.bidder or c.provenance.perceiver
        app = getattr(c.coord.capture, "app", None)
        key = (src, app, _norm(c.text))
        p = per[src]
        p["claims"] += 1
        p["chars"] += len(c.text or "")
        frame = getattr(c.coord.capture, "frame_id", None)
        if frame:
            p["frames"].add(frame)
        prev = last_seen.get(key)
        if prev is not None and c.coord.t_ms - prev <= window_ms:
            p["repeats"] += 1
            p["repeat_chars"] += len(c.text or "")
        last_seen[key] = c.coord.t_ms
    out = {}
    for src, p in sorted(per.items(), key=lambda kv: -kv[1]["claims"]):
        n = p["claims"]
        out[src] = {
            "claims": n,
            "repeat_rate": round(p["repeats"] / n, 4) if n else 0.0,
            "repeat_char_share": (round(p["repeat_chars"] / p["chars"], 4)
                                  if p["chars"] else 0.0),
            "claims_per_frame": round(n / len(p["frames"]), 2) if p["frames"] else None,
        }
    span = ((claims[-1].coord.t_ms - claims[0].coord.t_ms) / 60000.0
            if len(claims) > 1 else 0.0)
    return {"sampled": len(claims), "span_minutes": round(span, 1),
            "window_minutes": window_ms / 60000.0, "by_source": out}
