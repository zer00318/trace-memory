"""Deterministic fakes for hardware/model ports. The whole brain runs on these."""
from __future__ import annotations

import hashlib
import math
from contextlib import contextmanager
from typing import List, Sequence


from tracemem.supplier.text_norm import STOPWORDS, tokens


class HashEmbedder:
    """Deterministic token-hash embedding, L2-normalized. Shared tokens =>
    cosine similarity. Exercises every retrieval/novelty algorithm without
    model weights.

    WIDENED 128 -> 4096 AND STOPWORD-DAMPED, 2026-07-23. At 128 buckets this
    fake could not rank relevance, and that is why relevance went untested:
    with ~20-token documents, md5 collisions dominated the score and the
    LONGEST text won every query. Measured on the relevance fixture, the
    question "what did the sleep paper say about replay?" scored:

        HashEmbedder(128)  Mail:0.492  Safari:0.294   <- wrong topic wins
        MiniLM (ships)     Safari:0.493 Mail:0.030    <- right topic, 16x margin

    A fake that inverts the production ranking is not a cheap stand-in, it is
    a false witness: every supplier test written against it agreed with code
    that could not retrieve. Bucket count now exceeds any realistic document's
    token count, and function words — which every document shares and which
    therefore encode pure length — carry a tenth of the weight.
    """

    DIM = 4096
    STOP_WEIGHT = 0.1

    def embed(self, text: str) -> Sequence[float]:
        v = [0.0] * self.DIM
        for tok in tokens(text):          # ONE tokenizer (supplier.text_norm)
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            v[h % self.DIM] += self.STOP_WEIGHT if tok in STOPWORDS else 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class ScriptedReasoner:
    """Returns scripted responses keyed by substring; records every prompt."""

    def __init__(self, script=None, default='{"kind":"none"}'):
        self.script = script or {}
        self.default = default
        self.prompts: List[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        for key, resp in self.script.items():
            if key in prompt:
                return resp
        return self.default


class ContextAnswerReasoner:
    """Deterministic extractive stand-in for a competent small LLM: answers
    with the context line sharing the most content tokens with the question,
    cites that line's source marker, abstains when nothing overlaps.
    Understands both the baseline format '[id] t=.. text' and the supplier
    format '(kind|GRADE|src) text'."""

    _LINE = __import__("re").compile(r"^(?:\[([\w-]+)\]|\(([^)]*)\))\s*(.*)$")

    def __init__(self):
        self.prompts: List[str] = []

    @staticmethod
    def _tokens(s: str):
        return {t for t in s.lower().replace("?", " ").replace(":", " ").split()
                if len(t) > 3}

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        # The never-lie guard's verification pass (llm/bluff_check.py) asks a
        # separate yes/no question. A cooperative fake affirms its own
        # citation — it just answered from that block, so the block supports
        # the answer. This keeps hub.ask()'s guard path exercised by the
        # integration tests instead of quietly turning every fake answer into
        # an abstention. A test that wants to see the guard REJECT uses a
        # different reasoner double (see test_app's bluff case).
        if "fact-checker" in prompt:
            return "yes"
        try:
            body, tail = prompt.rsplit("QUESTION:", 1)
        except ValueError:
            return "not in memory"
        q_tokens = self._tokens(tail.split("ANSWER")[0])
        best, best_score, best_src = None, 0, ""
        for line in body.splitlines():
            m = self._LINE.match(line.strip())
            if not m:
                continue
            src = m.group(1) or (m.group(2) or "").split("|")[-1]
            text = m.group(3)
            toks = self._tokens(text)
            score = sum(1 for qt in q_tokens
                        if any(t.startswith(qt) or qt.startswith(t) for t in toks))
            if score > best_score:
                best, best_score, best_src = text, score, src
        if best is None or best_score == 0:
            return "not in memory"
        cite = (" [%s]" % best_src.split(",")[0]) if best_src else ""
        return best + cite


class FakeClock:
    def __init__(self, t_ms: int = 0):
        self.t = t_ms

    def now_ms(self) -> int:
        return self.t

    def advance(self, ms: int) -> None:
        self.t += ms


class FakeLease:
    """One-model law enforced; preemption togglable from tests."""

    def __init__(self):
        self.resident = None
        self.holder = None
        self._preempt = False
        self.log: List[str] = []

    @contextmanager
    def acquire(self, model: str, role: str = "?", priority: int = 0):
        if self.resident is not None:
            raise RuntimeError("one-model law: %s already resident" % self.resident)
        self.resident, self.holder = model, role
        self.log.append("acquire:%s:%s" % (role, model))
        try:
            yield
        finally:
            self.log.append("release:%s:%s" % (role, model))
            self.resident = self.holder = None

    def preempted(self) -> bool:
        return self._preempt

    def preempt(self) -> None:
        self._preempt = True

    def is_uncontended(self) -> bool:
        return self.resident is None
