#!/usr/bin/env python3
"""FIRST ASK — the librarian over tonight's lived claims.

Question in → cited evidence pack out, from the shadow store the living
beat just wrote. HONEST SCOPE: token-match retrieval only (the supplier's
full machinery — cues, vectors, the mouth's dialogue — is the wired
path; this proves screen→store→retrieval closes SAME-NIGHT). The mouth
is deliberately absent: what prints is the PACK, cited, never an answer
pretending to be one.

Usage: .venv/bin/python tools/first_ask.py "what did the screen say about protests?"
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tracemem.text.claim_log import ClaimLog          # noqa: E402

STORE = "/tmp/trace_beat_store.sqlite3"
TOKEN = re.compile(r"[a-z0-9]{2,}")


def main(question: str) -> None:
    q = set(TOKEN.findall(question.lower()))
    log = ClaimLog(STORE)
    scored = []
    for c in log.all_claims():
        overlap = q & set(TOKEN.findall(c.text.lower()))
        if overlap:
            scored.append((len(overlap), c))
    scored.sort(key=lambda t: -t[0])
    print(f"ASK: {question}")
    print(f"store: {log.count()} claims of lived screen-life")
    if not scored:
        print("PACK: nothing found — and that is the honest answer "
              "(coverage: only what the beat watched tonight).")
        return
    print(f"PACK ({min(5, len(scored))} blocks, cited):")
    for score, c in scored[:5]:
        print(f"  [{c.id[:10]}] t={c.coord.t_ms} grade={c.grade.name} "
              f"conf={getattr(c.confidence, 'value', c.confidence)}: {c.text[:70]}")


if __name__ == "__main__":
    main(" ".join(sys.argv[1:]) or "what did the screen say about protests?")
