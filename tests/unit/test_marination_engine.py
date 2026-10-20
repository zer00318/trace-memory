"""THE MARINATION GAUNTLET — the napkin's own sentences, executed.

Each test is one claim from the Node Creation Black Box, proven or failed:
  "raw material that can be marinated on"           → mining names the ghost
  "combine with fellow nodes to derive new things"  → succession makes causal real
  "always running in background, but better nightly"→ schemas + prediction live
  "the marinated content that's not useful is
   discarded, NOT the raw material"                 → derive → prune → RE-derive
"""
from __future__ import annotations

import json

import pytest

from tests.fakes import FakeClock, FakeLease, ScriptedReasoner
from tests.helpers import make_claim
from tracemem.domain import Episode, Grade, Modality, NodeKind, TimeRange
from tracemem.nodes.accrual import agenda, names_of, values_of
from tracemem.nodes.graph_store import MemoryGraphStore
from tracemem.nodes.marination.background import upkeep
from tracemem.nodes.marination.nightly import run_night
from tracemem.prediction.model import RoutineModel
from tracemem.prediction.surprise import score as surprise
from tracemem.domain import Modality

MS_H = 3_600_000
MS_DAY = 24 * MS_H


class World:
    """A tiny life: builds episodes through the real upkeep path and keeps
    the claims_by_episode map the night needs."""

    def __init__(self):
        self.g = MemoryGraphStore()
        self.episodes = []
        self.claims_by_episode = {}

    def live(self, ep_id, place, t0, claims, hours=1):
        e = Episode(id=ep_id, t_range=TimeRange(t0, t0 + hours * MS_H),
                    place_key=place, claim_ids=())
        e = e.with_claims([c.id for c in claims])
        upkeep(e, claims, self.g)
        self.episodes.append(e)
        self.claims_by_episode[e.id] = list(claims)
        return e

    def night(self, t_ms, reasoner=None, model=None):
        return run_night(self.g, None, None, FakeLease(), FakeClock(t_ms),
                         reasoner=reasoner, new_episodes=self.episodes,
                         claims_by_episode=self.claims_by_episode,
                         routine_model=model)


def ghost_claim(day, text="a person is at the kitchen table"):
    return make_claim(text=text, t_ms=day * MS_DAY + 500, place="wifi:home",
                      frame="k-%d" % day, grade=Grade.GIST, perceiver="gist",
                      modality=Modality.CAMERA, app=None)


# ---- "raw material that can be marinated on" ----

def test_mining_names_the_ghost_from_its_own_raw():
    w = World()
    for day in range(3):
        w.live("ep-%d" % day, "wifi:home", day * MS_DAY, [ghost_claim(day)])
    # day 2 also HEARD an introduction — meaningless to day-0 extraction,
    # gold to the ghost's later hunger:
    intro = make_claim(text="this is my neighbor Priya",
                       t_ms=2 * MS_DAY + 600, place="wifi:home", frame="k-2b",
                       modality=Modality.MIC, perceiver="asr", app=None)
    w.live("ep-2b", "wifi:home", 2 * MS_DAY + 550, [ghost_claim(2), intro])

    ghost = [n for n in w.g.nodes.values()
             if n.kind == NodeKind.ENTITY and not names_of(n)][0]
    assert "who is this?" in agenda(ghost)          # the hunger exists

    report = w.night(3 * MS_DAY)
    assert any(k == "seen_as" and v == "Priya" for _, k, v in report.mined)
    ghost = w.g.get_node(ghost.id)
    assert "Priya" in names_of(ghost)               # the ghost has a name
    assert "neighbor" in values_of(ghost, "relationship")
    assert "who is this?" not in agenda(ghost)      # the hunger is fed
    # provenance: the name came from the episode that actually said it
    named = [a for a in ghost.attributes if a.value == "Priya"][0]
    assert "ep-2b" in named.source_episode_ids


def test_mining_is_idempotent_on_unchanged_raw():
    w = World()
    for day in range(3):
        w.live("ep-%d" % day, "wifi:home", day * MS_DAY, [ghost_claim(day)])
    w.live("ep-x", "wifi:home", 2 * MS_DAY + 9000,
           [ghost_claim(2), make_claim(text="this is my neighbor Priya",
                                       t_ms=2 * MS_DAY + 9100, place="wifi:home",
                                       frame="kx", modality=Modality.MIC,
                                       perceiver="asr", app=None)])
    w.night(3 * MS_DAY)
    ghost = [n for n in w.g.nodes.values()
             if n.kind == NodeKind.ENTITY and "Priya" in names_of(n)][0]
    before = [(a.key, a.value, a.source_episode_ids) for a in ghost.attributes]
    second = w.night(3 * MS_DAY + 1000)
    assert not second.mined                          # unchanged raw: no re-chew
    assert [(a.key, a.value, a.source_episode_ids)
            for a in w.g.get_node(ghost.id).attributes] == before


def test_mining_receipt_is_written_only_after_the_node_update(monkeypatch):
    """A completion receipt before the graph write turns a crash into
    permanent amnesia: restart sees the receipt and never retries the raw."""
    from tracemem.nodes.marination.mining import mine, mining_tasks

    w = World()
    w.live("ep-1", "wifi:home", 0, [ghost_claim(0)])
    intro = make_claim(text="this is my neighbor Priya", t_ms=2_000,
                       place="wifi:home", frame="intro", modality=Modality.MIC,
                       perceiver="asr", app=None)
    w.live("ep-2", "wifi:home", 1_500, [ghost_claim(0), intro])
    ghost = [n for n in w.g.nodes.values()
             if n.kind == NodeKind.ENTITY and not names_of(n)][0]
    task = [t for t in mining_tasks(w.g, 10_000)
            if t.node_id == ghost.id and t.key == "seen_as"][0]

    monkeypatch.setattr(
        w.g, "upsert_node",
        lambda _node: (_ for _ in ()).throw(RuntimeError("disk stopped")))
    with pytest.raises(RuntimeError, match="disk stopped"):
        mine(task, w.g, w.claims_by_episode)

    assert w.g.mining_fingerprint(task.node_id, task.key) is None


def test_changed_text_grammar_reopens_unchanged_evidence(monkeypatch):
    """Raw forever has value only if a grammar added tomorrow actually gets
    another turn over yesterday's same claim ids."""
    import tracemem.nodes.marination.mining as mining

    w = World()
    opaque = make_claim(text="opaque old words", t_ms=1_000,
                        place="wifi:home", frame="old", modality=Modality.MIC,
                        perceiver="asr", app=None)
    w.live("ep-old", "wifi:home", 0, [ghost_claim(0), opaque])
    ghost = [n for n in w.g.nodes.values()
             if n.kind == NodeKind.ENTITY and not names_of(n)][0]
    task = [t for t in mining.mining_tasks(w.g, 10_000)
            if t.node_id == ghost.id and t.key == "seen_as"][0]

    abstain = ScriptedReasoner(
        default='{"value":null,"claim_id":null}')
    assert mining.mine(task, w.g, w.claims_by_episode,
                       reasoner=abstain) == []
    old_receipt = w.g.mining_fingerprint(task.node_id, task.key)
    assert old_receipt is not None

    monkeypatch.setattr(mining, "GRAMMAR_VERSION", "new-text-grammar")
    monkeypatch.setattr(
        mining, "_mine_deterministic",
        lambda key, _node, claims: [("seen_as", "Priya", claims[-1])]
        if key == "seen_as" else [])
    assert mining.mine(task, w.g, w.claims_by_episode) == [("seen_as", "Priya")]
    assert w.g.mining_fingerprint(task.node_id, task.key) != old_receipt
    assert "Priya" in names_of(w.g.get_node(ghost.id))


def test_reasoner_hunger_is_cited_literal_and_charged_once():
    from tracemem.nodes.marination.mining import mine, mining_tasks

    w = World()
    evidence = make_claim(text="the account label is Northstar", t_ms=1_000,
                          place="wifi:home", frame="old",
                          modality=Modality.MIC, perceiver="asr", app=None)
    w.live("ep-old", "wifi:home", 0, [ghost_claim(0), evidence])
    ghost = [n for n in w.g.nodes.values()
             if n.kind == NodeKind.ENTITY and not names_of(n)][0]
    task = [t for t in mining_tasks(w.g, 10_000)
            if t.node_id == ghost.id and t.key == "seen_as"][0]
    reasoner = ScriptedReasoner(default=json.dumps(
        {"value": "Northstar", "claim_id": evidence.id}))
    charged = []

    got = mine(task, w.g, w.claims_by_episode, reasoner=reasoner,
               on_reasoner_call=lambda: charged.append(1))

    assert got == [("seen_as", "Northstar")]
    assert charged == [1]
    assert "Northstar" in names_of(w.g.get_node(ghost.id))


def test_reasoner_hunger_never_caches_an_invented_value():
    from tracemem.nodes.marination.mining import mine, mining_tasks

    w = World()
    evidence = make_claim(text="an unnamed account", t_ms=1_000,
                          place="wifi:home", frame="old",
                          modality=Modality.MIC, perceiver="asr", app=None)
    w.live("ep-old", "wifi:home", 0, [ghost_claim(0), evidence])
    ghost = [n for n in w.g.nodes.values()
             if n.kind == NodeKind.ENTITY and not names_of(n)][0]
    task = [t for t in mining_tasks(w.g, 10_000)
            if t.node_id == ghost.id and t.key == "seen_as"][0]
    reasoner = ScriptedReasoner(default=json.dumps(
        {"value": "Priya", "claim_id": evidence.id}))

    assert mine(task, w.g, w.claims_by_episode, reasoner=reasoner) == []
    assert w.g.mining_fingerprint(task.node_id, task.key) is None
    assert "Priya" not in names_of(w.g.get_node(ghost.id))


def test_night_hunger_spends_through_the_same_token_ledger():
    from tracemem.nodes.marination.mining import MINING_TOKENS

    w = World()
    evidence = make_claim(text="the account label is Northstar", t_ms=1_000,
                          place="wifi:home", frame="old",
                          modality=Modality.MIC, perceiver="asr", app=None)
    w.live("ep-old", "wifi:home", 0, [ghost_claim(0), evidence])

    class Budget:
        def __init__(self):
            self.total = 0

        def may_spend(self, n):
            return self.total + n <= MINING_TOKENS

        def spend(self, n):
            self.total += n

    budget = Budget()
    reasoner = ScriptedReasoner(default=json.dumps(
        {"value": "Northstar", "claim_id": evidence.id}))
    report = run_night(
        w.g, None, budget, FakeLease(), FakeClock(10_000),
        reasoner=reasoner, new_episodes=w.episodes,
        claims_by_episode=w.claims_by_episode)

    assert report.hunger_model_calls == 1
    assert report.tokens_spent == budget.total == MINING_TOKENS
    assert report.hunger_completed >= 1


# ---- "combine with fellow nodes to derive NEW things" ----

def _topic_claim(day, topic, t_off, place="wifi:home"):
    """A LIVED, physical observation — hence CAMERA.

    "reading X" is a physical-world grammar (entity_extractor._READING_RE)
    and since 2026-08-24 it only fires for a sense that can see a physical
    thing. This fixture is about a rhythm in the founder's LIFE — late
    dinners preceding poor sleep — which is precisely camera-world material;
    it was reaching the birth layer as SCREEN only because that was the
    helper's default, and on screen the same grammar produced "their
    accessibility contexts and m" from browser OCR.
    """
    return make_claim(text="reading %s notes" % topic,
                      t_ms=day * MS_DAY + t_off, place=place,
                      frame="%s-%d" % (topic[:4], day), app=None,
                      modality=Modality.CAMERA)


def test_succession_makes_causal_thought_reachable():
    w = World()
    for day in range(4):                            # a rhythm, not a coincidence
        w.live("dinner-%d" % day, "wifi:home", day * MS_DAY + 21 * MS_H,
               [_topic_claim(day, "late dinner", 21 * MS_H + 60_000)])
        w.live("sleep-%d" % day, "wifi:home", day * MS_DAY + 23 * MS_H,
               [_topic_claim(day, "poor sleep", 23 * MS_H + 60_000)])

    dinner_eps = tuple("dinner-%d" % d for d in range(4))
    sleep_eps = tuple("sleep-%d" % d for d in range(4))
    dream = json.dumps({"kind": "causal",
                        "claim": "late dinners precede poor sleep for you",
                        "supported_by": list(dinner_eps + sleep_eps),
                        "rationale": "4/4 nights"})
    r = ScriptedReasoner(script={"late dinner": dream})
    report = w.night(5 * MS_DAY, reasoner=r)

    assert report.succession_observed > 0
    from tracemem.domain import EdgeType
    pre = [e for e in w.g.edges.values() if e.type == EdgeType.PRECEDES]
    assert pre and max(e.evidence_count for e in pre) >= 3   # rhythm, counted
    hyps = [n for n in w.g.nodes.values() if n.kind == NodeKind.HYPOTHESIS]
    assert hyps, "the rhythm must become a hypothesis"
    h = hyps[0]
    assert h.derivation_depth >= 1 and h.parent_ids
    claim_attr = h.attributes[0]
    assert claim_attr.grade < Grade.VERBATIM        # a dream outranks no witness
    assert set(claim_attr.source_episode_ids) <= set(dinner_eps + sleep_eps)


def test_one_coincidence_is_not_a_cause():
    w = World()
    w.live("d-0", "wifi:home", 21 * MS_H, [_topic_claim(0, "late dinner", 0)])
    w.live("s-0", "wifi:home", 23 * MS_H, [_topic_claim(0, "poor sleep", 0)])
    r = ScriptedReasoner(script={"late dinner": '{"kind":"causal","claim":"x"}'})
    w.night(2 * MS_DAY, reasoner=r)
    assert not [n for n in w.g.nodes.values() if n.kind == NodeKind.HYPOTHESIS]
    assert not any("memory consolidation function" in p for p in r.prompts)
    # Node hunger may legitimately use the same night reasoner; the causal
    # proposal itself remains below its structural rhythm gate.


# ---- "always running in background, but better nightly" ----

def test_routine_crystallizes_and_deviation_is_loud():
    w = World()
    model = RoutineModel()
    for day in range(5):                            # five office lunches, same slot
        w.live("lunch-%d" % day, "wifi:office", day * MS_DAY + 13 * MS_H,
               [make_claim(text="lunch order: ramen",
                           t_ms=day * MS_DAY + 13 * MS_H + 100,
                           place="wifi:office", frame="l%d" % day, app="DoorDash")])
    report = w.night(6 * MS_DAY, model=model)

    schemas = [w.g.get_node(s) for s in report.schemas_touched]
    assert schemas, "five same-slot days must crystallize a schema"
    s = schemas[0]
    assert s.kind == NodeKind.PATTERN and s.derivation_depth == 1
    assert any("usually at wifi:office" in v for v in values_of(s, "routine"))

    # prediction is now LIVE: the office at 13h is boring; a cafe is loud
    t_next = 6 * MS_DAY + 13 * MS_H
    assert surprise("wifi:office", t_next, model) < 0.2
    assert surprise("wifi:cafe-new", t_next, model) > 0.6


def test_unchanged_episodes_do_not_recrystallize_every_night_slice():
    """Production regression: all historical schemas were reinforced every
    30 seconds, so an unchanged world dirtied retrieval and blocked status."""
    w = World()
    for day in range(5):
        w.live("lunch-%d" % day, "wifi:office",
               day * MS_DAY + 13 * MS_H,
               [make_claim(text="lunch", t_ms=day * MS_DAY + 13 * MS_H + 1,
                           place="wifi:office", frame="f%d" % day)])

    first = w.night(6 * MS_DAY)
    second = w.night(6 * MS_DAY + 30_000)

    assert first.schemas_touched
    assert second.schemas_touched == []
    assert second.succession_observed == 0
    assert w.g.get_meta("night_schema_episodes")


# ---- "the marinated content that's not useful is discarded, NOT the raw" ----

def test_derive_prune_rederive_cycle():
    w = World()
    for day in range(3):
        w.live("r-%d" % day, "wifi:office", day * MS_DAY + 13 * MS_H,
               [make_claim(text="lunch order: ramen",
                           t_ms=day * MS_DAY + 13 * MS_H + 100,
                           place="wifi:office", frame="r%d" % day, app="DoorDash")])
    w.night(3 * MS_DAY)
    patterns = [n for n in w.g.nodes.values()
                if n.kind == NodeKind.PATTERN and n.parent_ids]
    assert patterns
    victim = patterns[0]

    # the derivation goes cold and unloved → the night forgets it
    victim.activation = 0.0
    victim.last_active_t_ms = 0
    w.g.upsert_node(victim)
    far = 90 * MS_DAY
    report2 = w.night(far)
    assert victim.id in report2.pruned or w.g.get_node(victim.id) is None

    # the raw is untouched, so re-living the routine RE-DERIVES the pattern:
    for day in (91, 92, 93):
        w.live("r2-%d" % day, "wifi:office", day * MS_DAY + 13 * MS_H,
               [make_claim(text="lunch order: ramen",
                           t_ms=day * MS_DAY + 13 * MS_H + 100,
                           place="wifi:office", frame="q%d" % day, app="DoorDash")])
    report3 = w.night(94 * MS_DAY)
    reborn = [n for n in w.g.nodes.values()
              if n.kind == NodeKind.PATTERN and n.parent_ids]
    assert reborn, "pruning meta must never cost knowledge raw can regrow"


# ---- the two derivation kinds were shadowing each other -------------------
#
# FOUND 2026-08-28 by reading the founder's live graph: 100 of 100 hypotheses
# were transitive co-occurrence and ZERO were causal, against 28 PRECEDES
# edges that cleared the precedence bar. Two separate mechanisms, pulling in
# opposite directions, each invisible on its own.

def _rhythm_world():
    """Four nights of dinner→sleep, with a third thing present at both.

    The third thing gives the pair a shared co-occurrence neighbour, so
    TRANSITIVE has something to compose; the four-night repetition gives them
    a PRECEDES edge above MIN_PRECEDENCE_COUNT, so CAUSAL is reachable. One
    pair, both kinds — which is the case neither used to survive.
    """
    w = World()
    for day in range(4):
        w.live("dinner-%d" % day, "wifi:home", day * MS_DAY + 21 * MS_H,
               [_topic_claim(day, "late dinner", 21 * MS_H + 60_000),
                _topic_claim(day, "kitchen radio", 21 * MS_H + 61_000)])
        w.live("sleep-%d" % day, "wifi:home", day * MS_DAY + 23 * MS_H,
               [_topic_claim(day, "poor sleep", 23 * MS_H + 60_000),
                _topic_claim(day, "kitchen radio", 23 * MS_H + 61_000)])
    return w


def _named(world, label):
    from tracemem.nodes.accrual import label_of
    for n in world.g.nodes.values():
        if label_of(n) == label:
            return n
    raise AssertionError("no node labelled %r" % label)


def test_succession_does_not_switch_off_co_occurrence_composition():
    """THE ORACLE. N0 hands PRECEDES to the nodes of consecutive episodes and
    N3 composes afterwards, every night. A lookup that keeps one edge type per
    neighbour therefore loses the CO_OCCURS edge for precisely the pairs that
    have a rhythm — the pairs most worth a thought."""
    from tracemem.nodes.combination.kinds import transitive as trans
    from tracemem.nodes.marination.succession import succession_pass

    w = _rhythm_world()
    a, b = _named(w, "late dinner notes"), _named(w, "poor sleep notes")

    before = trans.detect(a, b, w.g)
    assert not before.is_none, "fixture is wrong: nothing to compose"

    succession_pass(w.episodes, w.g)

    after = trans.detect(a, b, w.g)
    assert not after.is_none, (
        "adding a PRECEDES edge must not hide the CO_OCCURS edge that is "
        "still there")
    assert after.claim_text == before.claim_text, "same graph, same belief"


def test_a_pair_that_composes_still_gets_a_causal_turn():
    """THE ORACLE. Time-ordering and co-occurrence are different claims about
    the same pair. Gating the reasoner behind 'only if nothing else fired'
    means that in any graph where things are usually seen together — which is
    every graph built from one screen — the causal kind never runs."""
    w = _rhythm_world()
    dream = json.dumps({
        "kind": "causal",
        "claim": "late dinners precede poor sleep for you",
        "supported_by": ["dinner-0", "sleep-0"],
        "rationale": "4/4 nights"})
    r = ScriptedReasoner(script={"late dinner": dream})

    report = w.night(5 * MS_DAY, reasoner=r)

    assert report.by_kind.get("transitive"), "the shared neighbour composed"
    assert report.by_kind.get("causal"), (
        "the rhythm must still reach the reasoner: a pair that co-occurs is "
        "not a pair with nothing left to say")

    said = [a.value for n in w.g.nodes.values() for a in n.attributes
            if n.derivation_depth >= 1]
    assert any("precede" in s for s in said), "the causal belief was kept"
    assert any("repeatedly present with" in s for s in said), "so was the other"


def test_the_ledger_remembers_the_best_outcome_for_a_pair():
    """A kind that ran last and declined must not erase the record that an
    earlier kind produced a belief. Only the fingerprint gates revisiting, so
    this string is purely for whoever reads the ledger — and a ledger that
    says 'none' about a pair that produced a belief is worse than no ledger."""
    from tracemem.nodes.marination.nightly import _better

    assert _better("none", "accepted") == "accepted"
    assert _better("accepted", "none") == "accepted"
    assert _better("accepted", "rejected") == "accepted"
    assert _better("rejected", "none") == "rejected"
    assert _better("none", "none") == "none"


def test_an_expired_slice_never_reaches_the_unbounded_tail(monkeypatch):
    """WITNESSED 2026-10-07: when the slice deadline fired, the pattern and
    pair loops `break` — and run_night then fell through into the person
    judge, preferences, merges, prune and reconfirm, none of which poll the
    lease. On the founder's 635k-claim store that held the answer thread for
    minutes (questions timed out at 120s whenever the screen locked). An
    expired slice must return RESUMABLE; the tail runs in a later slice."""
    import tracemem.nodes.marination.nightly as nightly

    tail = []
    monkeypatch.setattr(nightly, "preference_pass",
                        lambda *a, **k: tail.append("preference") or [])

    class Expiring(FakeLease):
        def __init__(self, polls_before_expiry):
            super().__init__()
            self.left = polls_before_expiry

        def preempted(self):
            self.left -= 1
            return self.left < 0

    w = World()
    report = run_night(w.g, None, None, Expiring(1), FakeClock(10_000),
                       reasoner=ScriptedReasoner(default="no"),
                       new_episodes=w.episodes,
                       claims_by_episode=w.claims_by_episode)
    assert report.status == "RESUMABLE"
    assert tail == [], "an expired slice ran the unbounded tail"

    # and with time left, the tail still runs
    report = run_night(w.g, None, None, FakeLease(), FakeClock(20_000),
                       reasoner=ScriptedReasoner(default="no"),
                       new_episodes=w.episodes,
                       claims_by_episode=w.claims_by_episode)
    assert tail == ["preference"]


def test_mining_a_giant_episode_is_chunked_not_swallowed():
    """WITNESSED 2026-10-07 on the founder's 635k-claim store: one mining task
    for a hungry node read EVERY claim of EVERY episode it appears in —
    `[c for ep in eps for c in claims_by_episode.get(ep)]` — decrypting one
    claim at a time on the thread that answers questions. Every ask timed
    out at 120s while the screen was locked. The night polls its deadline
    between tasks, so a task must be bounded; and bounding must not cost
    recall, so the chunk position is durable and the next slice resumes."""
    from tracemem.app.hub import _EpisodeClaimMap
    from tracemem.nodes.marination import mining

    w = World()
    for day in range(2):
        w.live("ep-%d" % day, "wifi:home", day * MS_DAY, [ghost_claim(day)])
    fillers = [make_claim(text="unrelated screen text %d" % i,
                          t_ms=2 * MS_DAY + 1000 + i, place="wifi:home",
                          frame="f-%d" % i, app=None, modality=Modality.CAMERA)
               for i in range(3000)]
    intro = make_claim(text="this is my neighbor Priya", t_ms=2 * MS_DAY + 900,
                       place="wifi:home", frame="k-big", modality=Modality.MIC,
                       perceiver="asr", app=None)
    w.live("ep-big", "wifi:home", 2 * MS_DAY + 500,
           [ghost_claim(2)] + fillers + [intro])

    class CountingLog:
        def __init__(self, claims):
            self.by_id = {c.id: c for c in claims}
            self.gets = 0

        def get(self, cid):
            self.gets += 1
            return self.by_id.get(cid)

    log = CountingLog([c for cs in w.claims_by_episode.values() for c in cs])
    for i, e in enumerate(w.episodes):
        w.g.persist_episode(i, e, claim_ids=[c.id for c in
                                             w.claims_by_episode[e.id]])
    lazy = _EpisodeClaimMap(w.g, log)
    for e in w.episodes:
        lazy.remember(e.id)

    ghost = [n for n in w.g.nodes.values()
             if n.kind == NodeKind.ENTITY and not names_of(n)][0]
    found = False
    for call in range(20):
        tasks = [t for t in mining.mining_tasks(w.g, 3 * MS_DAY)
                 if t.node_id == ghost.id and t.key == "seen_as"]
        if not tasks:
            break
        before = log.gets
        got = mining.mine(tasks[0], w.g, lazy)
        assert log.gets - before <= mining.MINING_CHUNK_CLAIMS, \
            "one task decrypted %d claims" % (log.gets - before)
        if any(k == "seen_as" and v == "Priya" for k, v in got):
            found = True
            break
    assert found, "chunking lost the introduction"
    assert call <= 3004 // mining.MINING_CHUNK_CLAIMS + 1

    # and unchanged raw is never re-chewed: no decrypts at all
    before = log.gets
    for t in mining.mining_tasks(w.g, 3 * MS_DAY):
        if t.node_id == ghost.id and t.key == "seen_as":
            mining.mine(t, w.g, lazy)
    assert log.gets == before


def _giant_world():
    """A ghost whose only introduction sits behind 3,000 filler claims, on a
    lazy evidence map that counts how often membership is walked."""
    from tracemem.app.hub import _EpisodeClaimMap

    w = World()
    for day in range(2):
        w.live("ep-%d" % day, "wifi:home", day * MS_DAY, [ghost_claim(day)])
    fillers = [make_claim(text="unrelated screen text %d" % i,
                          t_ms=2 * MS_DAY + 1000 + i, place="wifi:home",
                          frame="f-%d" % i, app=None, modality=Modality.CAMERA)
               for i in range(3000)]
    intro = make_claim(text="this is my neighbor Priya",
                       t_ms=2 * MS_DAY + 5000, place="wifi:home",
                       frame="k-big", modality=Modality.MIC,
                       perceiver="asr", app=None)
    w.live("ep-big", "wifi:home", 2 * MS_DAY + 500,
           [ghost_claim(2)] + fillers + [intro])

    class Log:
        def __init__(self, claims):
            self.by_id = {c.id: c for c in claims}

        def get(self, cid):
            return self.by_id.get(cid)

    log = Log([c for cs in w.claims_by_episode.values() for c in cs])
    for i, e in enumerate(w.episodes):
        w.g.persist_episode(i, e, claim_ids=[c.id for c in
                                             w.claims_by_episode[e.id]])

    class Counting(_EpisodeClaimMap):
        walks = 0

        def claim_ids(self, episode_id):
            Counting.walks += 1
            return super().claim_ids(episode_id)

    lazy = Counting(w.g, log)
    for e in w.episodes:
        lazy.remember(e.id)
    ghost = [n for n in w.g.nodes.values()
             if n.kind == NodeKind.ENTITY and not names_of(n)][0]
    return w, lazy, Counting, ghost


def test_a_long_sweep_walks_its_evidence_once_not_once_per_chunk():
    """WITNESSED 2026-10-14 on the live store: night slices budgeted at 10s
    ran 16-24s and finished nothing (hunger examined 23, completed 0; the
    watchman then stood the night down as STUCK with 155 tasks open).

    Every mine() call rebuilt the node's ENTIRE evidence-id list and a
    fingerprint over it, to decrypt one 400-claim chunk.  For a node whose
    episodes hold hundreds of thousands of claims that is O(all its raw) per
    chunk, so a sweep cost (raw / 400) full walks.  Given a stop signal that
    does not fire, one call must sweep to the end on one walk."""
    from tracemem.nodes.marination import mining

    w, lazy, Counting, ghost = _giant_world()
    task = [t for t in mining.mining_tasks(w.g, 3 * MS_DAY)
            if t.node_id == ghost.id and t.key == "seen_as"][0]
    chunks = []
    got = mining.mine(task, w.g, lazy, stop=lambda: False,
                      on_chunk=lambda: chunks.append(1))
    assert ("seen_as", "Priya") in got
    assert chunks, "the introduction was in the first chunk; the fixture " \
        "proves nothing about sweeping"
    assert Counting.walks == len(task.episode_ids), (
        "membership walked %d times for %d episodes"
        % (Counting.walks, len(task.episode_ids)))


def test_an_interrupted_sweep_resumes_without_rewalking_unchanged_evidence():
    """The slice deadline cuts a sweep; the next slice resumes at the durable
    cursor.  While membership is unchanged (the screen is locked: nothing new
    binds) the evidence list from the last slice is still true, and walking
    it again was most of what each slice cost."""
    from tracemem.nodes.marination import mining

    w, lazy, Counting, ghost = _giant_world()
    task = [t for t in mining.mining_tasks(w.g, 3 * MS_DAY)
            if t.node_id == ghost.id and t.key == "seen_as"][0]
    got, slices = [], 0
    while not got and slices < 20:
        slices += 1
        budget = iter([False, True])          # two chunks, then the deadline
        got = mining.mine(task, w.g, lazy, stop=lambda: next(budget, True))
    assert ("seen_as", "Priya") in got and slices > 1
    assert Counting.walks == len(task.episode_ids)

    # A settled task on unchanged membership costs no walk at all.
    Counting.walks = 0
    for t in mining.mining_tasks(w.g, 3 * MS_DAY):
        if t.node_id == ghost.id and t.key == "seen_as":
            mining.mine(t, w.g, lazy, stop=lambda: False)
    assert Counting.walks == 0


def test_a_sweep_that_advanced_its_cursor_is_progress_not_stuck():
    """A dry chunk writes a durable cursor: the next slice starts further
    along.  The watchman's STUCK guard counted only fields that changed the
    graph, so three slices of honest sweeping read as a barren night and it
    stood down with the work a third done."""
    from tracemem.app.night_watchman import NightWatchman
    from tracemem.nodes.marination.nightly import NightReport

    assert "hunger_chunks" in NightWatchman._PROGRESS_FIELDS
    r = NightReport()
    r.hunger_chunks = 3
    assert NightWatchman._forward_progress(NightWatchman, r) == 3


def test_mining_that_used_up_a_slice_lets_the_next_slice_combine(monkeypatch):
    """WITNESSED 2026-10-09: 24 night slices, 0 combinations attempted. N2
    (mining) runs before N3 (combination), and one mining reasoner call
    outlasted the whole slice — every slice, while the model server was down
    and each call sat in 40s of retry backoff. N3 was never reached. A slice
    cut off inside N2 must hand the next slice to combination."""
    import tracemem.nodes.marination.nightly as nightly

    w = World()
    for day in range(2):
        w.live("ep-%d" % day, "wifi:home", day * MS_DAY, [ghost_claim(day)])
    assert nightly.mining_tasks(w.g, 3 * MS_DAY), "fixture needs hunger"

    class Lease(FakeLease):
        expired = False

        def preempted(self):
            return self.expired

    lease = Lease()
    calls = []

    def slow_mine(*a, **k):
        calls.append(1)
        lease.expired = True           # the call outlived the slice
        return []

    monkeypatch.setattr(nightly, "mine", slow_mine)

    r1 = run_night(w.g, None, None, lease, FakeClock(3 * MS_DAY),
                   reasoner=ScriptedReasoner(default="no"),
                   new_episodes=w.episodes,
                   claims_by_episode=w.claims_by_episode)
    assert r1.status == "RESUMABLE" and calls == [1]

    lease.expired = False
    r2 = run_night(w.g, None, None, lease, FakeClock(3 * MS_DAY + 1),
                   reasoner=ScriptedReasoner(default="no"),
                   new_episodes=w.episodes,
                   claims_by_episode=w.claims_by_episode)
    assert calls == [1], "the next slice spent itself on mining again"
    assert r2.hunger_examined == 0
    assert r2.status == "COMPLETE", "combination and the tail got the slice"

    lease.expired = False
    run_night(w.g, None, None, lease, FakeClock(3 * MS_DAY + 2),
              reasoner=ScriptedReasoner(default="no"),
              new_episodes=w.episodes, claims_by_episode=w.claims_by_episode)
    assert calls == [1, 1], "mining resumes on the slice after"
