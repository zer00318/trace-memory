"""OWNS: the ONE tokenizer every lexical path in the supplier shares.

Written 2026-07-23. Before this file, each lexical consumer split on
whitespace and invented its own punctuation handling — or none:

  · HashEmbedder            text.lower().split()          (no stripping)
  · packer._overlap         text.lower().split()          (no stripping)
  · ContextAnswerReasoner   .replace("?", " ").replace(":", " ")  (two chars)

So "replay?" and "replay" were different tokens everywhere except the one
place someone had been bitten. Measured cost: the question "what did the
sleep paper say about replay?" scored EXACTLY 0.0000 against the claim
"hippocampal replay compresses experience twentyfold" — the one claim that
answered it — because the interrogative word carrying the most information
is, by construction, the one adjacent to the question mark.

That is the whole bug class: a query's most load-bearing token is the most
likely to be punctuated. One tokenizer, used by everyone, or it returns.
"""
from __future__ import annotations

from typing import List, Set

# Function words: shared by every document, so they encode length, not topic.
STOPWORDS: Set[str] = frozenset("""a an and are as at be been but by can did do
does for from had has have how i if in into is it its me my of on or our so
that the their there this to was were what when where which who whom why will
with you your about get got""".split())


def tokens(text: str) -> List[str]:
    """Lowercase word tokens, punctuation stripped from both edges.

    Inner punctuation survives on purpose: "wifi:home-ap", "4ms",
    "conn.execute" and "gemma3:12b" are identifiers in this system, and
    shattering them would lose exactly the place/version/measurement terms
    that make a memory findable.
    """
    out = []
    for raw in text.lower().split():
        tok = raw.strip("\"'`.,;:!?()[]{}<>…-–—*_")
        if tok:
            out.append(tok)
    return out


def content_tokens(text: str, min_len: int = 1) -> Set[str]:
    """Topic-bearing tokens only — stopwords and stubs removed."""
    return {t for t in tokens(text) if len(t) >= min_len and t not in STOPWORDS}
