"""OWNS: model-first packing, precision-steered drill, hard cap (05 §4).

S1 budget never exceeded; S3 every block graded+sourced; S4 raw claims only
via drill-down from packed nodes (or a t_hint range), never free-floating soup.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from tracemem.domain import Grade
from tracemem.domain.node import human_kind
from tracemem.supplier.cue import Precision
from tracemem.supplier.text_norm import content_tokens

DRILL_RESERVE = {Precision.PATTERN: 0.10, Precision.FACT: 0.40,
                 Precision.VERBATIM: 0.70, Precision.HYBRID: 0.25}


@dataclass(frozen=True)
class CtxBlock:
    kind: str                    # node | claim
    text: str
    grade: Grade
    source_ids: Tuple[str, ...]
    t_ms: int


@dataclass(frozen=True)
class Context:
    blocks: Tuple[CtxBlock, ...]
    tokens: int


def _render_node(node) -> str:
    attrs = "; ".join("%s=%s" % (a.key, a.value) for a in node.attributes[:6])
    # human_kind, not kind.value: `[ENTITY 1a2b3c]` puts a database type into
    # the evidence, and the never-lie guard refuses answers that restate it.
    # See domain/node.human_kind.
    return "[%s %s] %s" % (human_kind(node.kind).upper(), node.id[:8], attrs)


def _render_claim(claim) -> str:
    return "[%s t=%d] %s" % (claim.grade.name, claim.coord.t_ms, claim.text)


def pack(candidates, cue, budget_tokens: int, tokenizer,
         claim_store=None, claims_by_episode=None, embedder=None) -> Context:
    blocks: List[CtxBlock] = []
    spent = 0
    reserve = int(budget_tokens * DRILL_RESERVE[cue.precision])
    node_budget = budget_tokens - reserve

    packed_nodes = []
    sims = {}                        # node identity -> its entry similarity
    for cand in candidates:
        sims[id(cand.node)] = cand.entry_sim
        text = _render_node(cand.node)
        cost = tokenizer(text)
        if spent + cost > node_budget:
            break
        grade = max((a.grade for a in cand.node.attributes), default=Grade.GIST)
        blocks.append(CtxBlock("node", text, grade,
                               tuple(a.source_episode_ids[0] for a in
                                     cand.node.attributes[:3] if a.source_episode_ids),
                               cand.node.last_active_t_ms))
        spent += cost
        packed_nodes.append(cand.node)

    # drill: verbatim claims for the top packed nodes, within the reserve (S4)
    if (claim_store is not None or claims_by_episode is not None) and reserve > 0:
        drill_n = {Precision.PATTERN: 1, Precision.FACT: 2,
                   Precision.VERBATIM: len(packed_nodes) or 1,
                   Precision.HYBRID: 2}[cue.precision]
        # THE DRILL FOLLOWS RELEVANCE (2026-07-23). Today this is a NO-OP and
        # the honest note says so: `retrieve` seeds scratch activation FROM
        # entry similarity and spread only ever decays it, so ordering by heat
        # and ordering by relevance are the same list. Mutation-tested —
        # replacing this sort with a constant changes no observable behaviour.
        #
        # It stays because the equality is a coincidence of one decision, not
        # a property. The moment activation stops being seeded from similarity
        # — recency weighting, spec 05 R2's "no mutation in v1" being revisited,
        # any prior on node heat — the two orders separate, and only drill_n
        # nodes ever have their raw material read. On that day the context
        # would carry the right node's LABEL beside a different node's
        # EVIDENCE, which reads to the LLM exactly like an answer.
        by_relevance = sorted(
            packed_nodes, key=lambda n: -sims.get(id(n), 0.0))
        seen_claims = set()          # a claim cited twice is budget burned twice
        for node in by_relevance[:drill_n]:
            for a in node.attributes:
                for ep_id in a.source_episode_ids[:2]:
                    for claim in _claims_for(claim_store, ep_id, cue,
                                             claims_by_episode, embedder):
                        if claim.id in seen_claims:
                            continue
                        seen_claims.add(claim.id)
                        text = _render_claim(claim)
                        cost = tokenizer(text)
                        if spent + cost > budget_tokens:
                            return Context(tuple(blocks), spent)
                        blocks.append(CtxBlock("claim", text, claim.grade,
                                               (claim.id,), claim.coord.t_ms))
                        spent += cost
    return Context(tuple(blocks), spent)


def _cos(a, b) -> float:
    num = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return num / (na * nb) if na and nb else 0.0


def _overlap(text: str, cue_text: str) -> float:
    """Embedder-free fallback ranking. Never the primary path."""
    q = content_tokens(cue_text)
    d = content_tokens(text)
    return len(q & d) / (len(q) or 1)


def _claims_for(claim_store, episode_id: str, cue, claims_by_episode=None,
                embedder=None, limit: int = 4):
    """THE DRILL: the raw material behind ONE packed node's episode.

    Rewritten 2026-07-23. What this did before: it accepted `episode_id`,
    never used it, called `read_range(0, 2**53)` — every claim ever recorded
    — and kept the first four whose text contained any query token longer
    than three characters.

    Three things were wrong with that, in ascending order of seriousness:

      1. It is the brute-force scan over raw claims that the index's own
         docstring declares forbidden. At 10^6 claims it is the product.
      2. First-four-substring is not ranking. "did the kiln get
         rescheduled?" matched "coffee with Maya about the kiln project
         timeline" first purely because it was written earlier, and the
         real answer never made the cut.
      3. Because this scan found answers on its own, the node graph above
         it was load-bearing in name only — five mutations that reduced
         retrieval to random order left the whole suite green. A component
         that cannot fail is a component that is not being used.

    Now: scoped to the episode the node actually cites, ranked by the same
    embedder the door used. The store is touched only when no episode map
    is wired, and even then within the cue's time hint — never end to end.
    """
    claims = None
    if claims_by_episode is not None:
        claims = claims_by_episode.get(episode_id)
    if claims is None:
        # No episode map wired (unit callers): stay inside the cue's window.
        # An unbounded read here is what this rewrite exists to remove, so a
        # missing hint costs recall, never a full scan.
        if cue.t_hint is None:
            return []
        claims = claim_store.read_range(cue.t_hint.start_ms, cue.t_hint.end_ms)
    if not claims:
        return []

    if embedder is not None:
        q = embedder.embed(cue.text)
        scored = [(_cos(q, embedder.embed(c.text)), c) for c in claims]
    else:
        scored = [(_overlap(c.text, cue.text), c) for c in claims]
    scored.sort(key=lambda t: (-t[0], t[1].coord.t_ms))
    return [c for score, c in scored[:limit] if score > 0.0]
