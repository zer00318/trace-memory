"""THE CROWN TEST: a synthetic week of life flows in one end; a discovered
pattern and a cited answer come out the other. Every subsystem that doesn't
need hardware runs for real: log -> bind -> birth -> activation -> night
(patterns, budget, lease) -> retrieve -> pack -> prompt -> answer -> cite.

If this passes, the architecture executes. It is deliberately written
against the PUBLIC seams only — hub, run_night, ask — so any internal
refactor that keeps the seams keeps the crown.
"""
from __future__ import annotations

import random

from tests.fakes import ContextAnswerReasoner, FakeClock, HashEmbedder
from tracemem.app.wiring import build
from tracemem.domain import NodeKind
from tracemem.evalx.fixtures import synthetic_week
from tracemem.nodes.marination.nightly import run_night

from tests.helpers import judged_people

MS_DAY = 86_400_000


def _hub(clock):
    return build({"embedder": HashEmbedder(), "reasoner": ContextAnswerReasoner(),
                  "clock": clock})


def test_synthetic_week_to_cited_answer_and_pattern():
    week = synthetic_week(seed=7)
    clock = FakeClock(7 * MS_DAY)
    hub = _hub(clock)
    # The night's person verdict, stated directly — see tests.helpers.
    # This test is about the whole spine (segment → birth → night → cited
    # answer → honest refusal), not about whether a model can tell a name
    # from a city, and it runs with fake adapters by design.
    judged_people(hub.w.graph_store, "Maya")

    # ingest the week SHUFFLED — order-independence is a system property
    for t in week.scene_changes:
        hub.post_scene_change(t)
    claims = list(week.claims)
    random.Random(11).shuffle(claims)
    for i in range(0, len(claims), 5):                  # arrives in ragged batches
        hub.post_claims(claims[i:i + 5])

    assert len(hub.episodes) >= 7                       # the week segmented
    kinds = {n.kind for n in hub.w.graph_store.nodes.values()}
    assert {NodeKind.APP, NodeKind.PLACE} <= kinds      # birth ran
    named = [n for n in hub.w.graph_store.nodes.values()
             if any(a.value == "Maya" for a in n.attributes)]
    assert named, "the 'with Maya' mention must birth a person node"

    # the night: patterns discovered from pure counting, budgeted, leased
    night = run_night(hub.w.graph_store, hub.w.claim_log, hub.w.token_ledger,
                      hub.w.spotlight, clock)
    assert night.status == "COMPLETE"
    patterns = [n for n in hub.w.graph_store.nodes.values()
                if n.kind == NodeKind.PATTERN]
    assert patterns, "a routine week must yield at least one pattern node"
    for p in patterns:
        assert p.derivation_depth >= 1 and p.parent_ids
        assert len(p.attributes[0].source_episode_ids) >= 1

    hub.w.vector_index.rebuild_from(hub.w.graph_store)  # night refresh (05 §7)

    # the ask: a planted deviation, answered with a citation
    ans = hub.ask("when was the kiln firing moved to?")
    assert not ans.abstained
    assert "thursday" in ans.text.lower()
    assert ans.citations, "an answer without citations is a confabulation"

    # the gap: never captured -> abstain, never invent
    assert hub.ask("what stock price did zzyzx close at?").abstained


def test_week_ingest_is_order_independent():
    week = synthetic_week(seed=7)
    hubs = []
    for order_seed in (1, 2):
        hub = _hub(FakeClock(7 * MS_DAY))
        for t in week.scene_changes:
            hub.post_scene_change(t)
        claims = list(week.claims)
        random.Random(order_seed).shuffle(claims)
        for c in claims:
            hub.post_claims([c])                        # one at a time, worst case
        hubs.append(hub)
    a = {(e.id, e.claim_ids) for e in hubs[0].episodes}
    b = {(e.id, e.claim_ids) for e in hubs[1].episodes}
    assert a == b                                       # byte-identical projection
