"""OWNS: the shed — raw-claim embeddings + top-k + one LLM call (spec X §1).

F1 is structural: this module imports NOTHING from binder/nodes/supplier —
the baseline must be exactly what a competent weekend hacker would build, or
beating it proves nothing. Verified by tests/invariants via AST.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Tuple

_CITE_RE = re.compile(r"\[([\w.,:-]+)\]")

PROMPT = (
    "Answer strictly from the context lines below. Cite the ids of the lines "
    "you used, in square brackets. If the context does not contain the answer, "
    "reply exactly: not in memory.\n\nCONTEXT:\n%s\n\nQUESTION: %s\nANSWER:"
)


@dataclass(frozen=True)
class RagAnswer:
    text: str
    citations: Tuple[str, ...]
    abstained: bool
    retrieved_ids: Tuple[str, ...]
    context_tokens: int


def _cos(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


class Corpus:
    """The baseline's index: every claim, embedded ONCE.

    WHY THIS EXISTS, and why it does not make the baseline unfair. `answer`
    re-embedded every claim in the store on every question — 633,761 forward
    passes per question on the founder's real store, which is the mechanical
    reason spec X's whole experiment had only ever been run against a
    7-question synthetic fixture. A competent weekend hacker building the shed
    would embed their corpus once and keep it; recomputing per query is not a
    stronger baseline, it is a slower one. F1 is about what the baseline may
    IMPORT — nothing from binder, nodes or supplier — and a dict of vectors
    imports nothing.

    Build it once per store and hand it to every question.
    """

    __slots__ = ("claims", "vectors", "_matrix")

    def __init__(self, claim_store, embedder, progress=None,
                 max_claims: int = 0, claims=None, vectors=None) -> None:
        if claims is not None:
            # THE CALLER ALREADY HAS THEM. A reduced run must not pay for a
            # full one: `all_claims()` DECRYPTS every row before anything can
            # look at it, so sampling afterwards costs the whole store anyway.
            # A caller that can select by the plaintext time index hands the
            # subset in instead.
            self.claims = list(claims)
        else:
            self.claims = list(claim_store.all_claims())
        if max_claims and len(self.claims) > max_claims:
            # A SAMPLE SAYS SO. Evenly spaced rather than the first N, so a
            # reduced run still spans the whole recorded period instead of
            # measuring one afternoon.
            step = len(self.claims) / float(max_claims)
            self.claims = [self.claims[int(i * step)] for i in range(max_claims)]
        texts = [c.text for c in self.claims]
        many = getattr(embedder, "embed_many", None)
        if vectors is not None:
            # ALREADY EMBEDDED, from a previous run. A corpus pass over the
            # real store is over an hour; paying it again to iterate on the
            # question set is how an experiment stops being run.
            if len(vectors) != len(self.claims):
                raise ValueError(
                    "%d cached vectors for %d claims — a stale cache would "
                    "score the wrong text against every question"
                    % (len(vectors), len(self.claims)))
            self.vectors = list(vectors)
            self._install_matrix()
            return
        vectors = []
        if many is None:
            vectors = [embedder.embed(t) for t in texts]
        else:
            # Chunked so a caller can say how far along it is: a corpus pass
            # over a real store is minutes, and a silent minute reads as a
            # hang.
            step = 4096
            for i in range(0, len(texts), step):
                vectors.extend(many(texts[i:i + step]))
                if progress is not None:
                    progress(len(vectors), len(texts))
        self.vectors = vectors
        self._install_matrix()

    def _install_matrix(self) -> None:
        """FLOAT32 IN ONE BLOCK WHEN NUMPY IS HERE. 633,805 claims as Python
        lists of 384 floats is a couple of gigabytes of boxed objects and a
        scoring loop in the interpreter; as one array it is ~0.9GB and one
        matrix-vector product. numpy is not a memory system — F1 forbids the
        baseline importing binder/nodes/supplier, not arithmetic."""
        self._matrix = None
        try:
            import numpy as np
            self._matrix = np.asarray(self.vectors, dtype=np.float32)
        except Exception:                                     # noqa: BLE001
            self._matrix = None

    def top_k(self, qv, k: int):
        """The k most similar claims. Vectors are normalized, so dot IS cosine."""
        if self._matrix is not None:
            import numpy as np
            sims = self._matrix @ np.asarray(qv, dtype=np.float32)
            idx = np.argsort(-sims)[:k]
            return [(self.claims[int(i)], float(sims[int(i)])) for i in idx]
        return sorted(zip(self.claims, (_cos(qv, v) for v in self.vectors)),
                      key=lambda t: t[1], reverse=True)[:k]


def answer(question: str, claim_store, embedder, reasoner,
           budget: int, k: int = 24, corpus: "Corpus" = None) -> RagAnswer:
    if corpus is None:
        corpus = Corpus(claim_store, embedder)
    qv = embedder.embed(question)
    scored = corpus.top_k(qv, k)

    lines: List[str] = []
    used_ids: List[str] = []
    spent = 0
    for c, _sim in scored:
        line = "[%s] t=%d %s" % (c.id[:8], c.coord.t_ms, c.text)
        cost = len(line.split())
        if spent + cost > budget:
            break
        lines.append(line)
        used_ids.append(c.id)
        spent += cost

    raw = reasoner.complete(PROMPT % ("\n".join(lines), question))
    text = raw.strip()
    abstained = text.lower().startswith("not in memory")
    cited = tuple(m for m in _CITE_RE.findall(text)
                  if any(u.startswith(m) for u in used_ids))
    return RagAnswer(text=text, citations=cited, abstained=abstained,
                     retrieved_ids=tuple(used_ids), context_tokens=spent)
