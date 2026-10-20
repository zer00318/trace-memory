"""OWNS: the entry-door index over NODES/META (05 §2, §7).

Brute cosine over 10^4-10^5 node summaries is legitimate — the no-brute-force
law forbids scanning 10^6+ RAW claims, not the compact model. Embedder is a
port (sentence-transformers in prod, HashEmbedder in tests).
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple


def _cos(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class VectorIndex:
    def __init__(self, embedder, evidence=None) -> None:
        self._embedder = embedder
        self._vecs: Dict[str, Sequence[float]] = {}
        self._texts: Dict[str, str] = {}
        self._evidence = evidence

    @property
    def embedder(self):
        """The drill ranks claims with the SAME embedder the door uses —
        two similarity spaces in one retrieval would rank incomparably."""
        return self._embedder

    def upsert(self, node_id: str, text: str) -> None:
        """Index a node. Re-embedding an UNCHANGED summary is skipped.

        THE DAEMON WEDGED WITHOUT THIS (found by running it, 2026-07-28).
        `Hub.post_claims` calls `rebuild_from`, whose docstring already says
        "incremental" — but this method re-embedded unconditionally, so every
        flush re-embedded EVERY node with a real MiniLM model on CPU. Binding
        runs on the daemon's own loop thread, so the cost landed directly on
        capture: with ~900 claims bound the loop hit 100% CPU, stopped
        draining frames (claim count froze at 923), and stopped answering —
        `--stats` timed out at 25s and an ask at 120s. The eye goes dark and
        nothing says so, which is the exact failure this product may not have.

        The index already records what each node was indexed AS (`_texts`,
        added for the relevance audit), so "has this changed?" was already
        answerable — the check simply was not being made. With it,
        `rebuild_from` costs one embed per NEW or CHANGED node instead of one
        per node in the graph, which is what "incremental" always meant.
        """
        if self._texts.get(node_id) == text:
            return
        self._vecs[node_id] = self._embedder.embed(text)
        self._texts[node_id] = text

    def text_of(self, node_id: str) -> str:
        """What this node was indexed AS. Retrieval that cannot be inspected
        cannot be tested for relevance — the audit's whole finding."""
        return self._texts.get(node_id, "")

    def remove(self, node_id: str) -> None:
        self._vecs.pop(node_id, None)
        self._texts.pop(node_id, None)

    def topk(self, query_text: str, k: int) -> List[Tuple[str, float]]:
        q = self._embedder.embed(query_text)
        scored = [(nid, _cos(q, v)) for nid, v in self._vecs.items()]
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[:k]

    def bind_evidence(self, fn) -> None:
        """Give the index a way to read a node's RAW MATERIAL.

        Set once by the Hub at construction, so every existing
        `rebuild_from(graph_store)` call site keeps working and silently gets
        the richer surface.
        """
        self._evidence = fn

    EVIDENCE_CHARS = 600      # a node's searchable surface, not its archive
    EVIDENCE_CLAIMS = 8       # and never decrypt a lifetime to fill it

    def _summary(self, node) -> str:
        """WHAT A NODE IS FINDABLE BY (fixed 2026-07-23).

        It used to be `" ".join(a.value ...)` — the attribute LABELS alone.
        A node born from "kiln firing rescheduled to thursday afternoon" was
        indexed as the string "Calendar", so a question about the kiln could
        not reach it by any phrasing: measured cos("did the kiln get
        rescheduled?", "Calendar") = 0.239 against cos(same question, the
        actual claim) = 0.593. The information was there and the door was
        the wrong shape.

        A node is now findable by what it is made of as well as what it is
        called. Entry still happens through NODES — spec 05 S4's "never
        free-floating soup" holds, because nothing here indexes a claim that
        no node vouches for.
        """
        label = " ".join(a.value for a in node.attributes)
        body = ""
        if self._evidence is not None:
            body = (self._evidence(node) or "")[:self.EVIDENCE_CHARS]
        return (label + " " + body).strip() or node.id

    def rebuild_from(self, graph_store) -> int:
        """Nightly incremental rebuild (05 §7): index node summaries."""
        offered = []
        for node in graph_store.nodes.values():
            text = self._summary(node)
            if self._texts.get(node.id) != text:
                offered.append((node.id, text))
        batch = getattr(self._embedder, "embed_many", None)
        if offered and batch is not None:
            # The production MiniLM adapter can embed hundreds per forward
            # pass. Calling `upsert` here made one forward pass per node; the
            # first 483-node rebuild held the sole owner/answer thread beyond
            # 120 seconds despite a ten-second night lease. Same vectors,
            # bounded evidence, two batches instead of 483 model calls.
            vectors = batch([text for _node_id, text in offered])
            if len(vectors) != len(offered):
                raise ValueError("embed_many returned the wrong vector count")
            for (node_id, text), vector in zip(offered, vectors):
                self._vecs[node_id] = vector
                self._texts[node_id] = text
        else:
            for node_id, text in offered:
                self.upsert(node_id, text)
        return len(graph_store.nodes)
