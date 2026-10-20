"""OWNS: answer + citation extraction back to source ids (05 §5)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Tuple

_CITE_RE = re.compile(r"\[([\w,-]+)\]")


@dataclass(frozen=True)
class Answer:
    text: str
    citations: Tuple[str, ...]
    abstained: bool
    # Citation markers the model wrote that name NOTHING it was given.
    # Kept rather than discarded: an answer that cites a source which does
    # not exist has fabricated, and the reader must be able to see that. The
    # old behaviour dropped these from `citations` while leaving them in the
    # visible text, which is the most dangerous possible handling — the
    # answer LOOKS grounded and the machine knows it is not.
    invented: Tuple[str, ...] = ()


def parse_answer(raw_text: str, context) -> Answer:
    text = raw_text.strip()
    abstained = text.lower().startswith("not in memory")
    known = set()
    for b in context.blocks:
        known.update(b.source_ids)
    cites, invented = [], []
    for m in _CITE_RE.findall(text):
        for cid in m.split(","):
            cid = cid.strip()
            if not cid:
                continue
            (cites if cid in known else invented).append(cid)
    invented = tuple(dict.fromkeys(invented))
    # Rewrite the VISIBLE text. Dropping an invented id from `citations` while
    # leaving "[start_ofc]" on screen is the worst of both: the answer reads
    # as grounded and the machine already knows it is not.
    for bad in invented:
        text = text.replace("[%s]" % bad, "[UNSUPPORTED]")
    return Answer(text=text, citations=tuple(dict.fromkeys(cites)),
                  abstained=abstained, invented=invented)
