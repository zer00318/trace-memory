"""OWNS: retrieve → pack → ground, one call (05 §8)."""
from __future__ import annotations

from tracemem.supplier.packer import Context, pack
from tracemem.supplier.retriever import retrieve


def supply(cue, graph_store, claim_store, vector_index, budget_tokens: int,
           tokenizer=None, claims_by_episode=None) -> Context:
    tokenizer = tokenizer or (lambda s: max(1, len(s) // 4))
    candidates = retrieve(cue, graph_store, vector_index)
    return pack(candidates, cue, budget_tokens, tokenizer, claim_store,
                claims_by_episode=claims_by_episode,
                embedder=getattr(vector_index, "embedder", None))
