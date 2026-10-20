"""OWNS: the night — the full marination economy (spec 04 §7, I4/I5/I6,
rebuilt after the 2026-07-19 audit found the engine's holes).

Five phases, in an order that is itself the design:
  N0 SUCCESSION  — consecutive episodes hand PRECEDES to each other, so
                   causal thought becomes reachable (was dead code).
  N1 CRYSTALLIZE — the routine model refits on the day's episodes and
                   (slot, place) rhythms crystallize into schema nodes:
                   prediction's write-path (P5.4).
  N2 MINE        — the re-marination loop: hungry nodes re-read their OWN
                   raw claims, aimed by their agendas. Raw is kept for
                   exactly this pass (law #9's purpose, executed).
  N3 COMBINE     — pattern scan + pair combination (deterministic kinds
                   free; causal spends the tokens) over a graph that N0-N2
                   just enriched — which is why combination can now find
                   things worth finding.
  N4 FORGET      — prune cold re-derivable meta; reconfirm what fresh raw
                   supports. Safe because N2 proves re-derivation works.

Ledger-checkpointed, budgeted, preemptible; an unchanged store yields an
empty night (I4). NightReport carries per-phase counts — the tuning signal.
"""
from __future__ import annotations

import time as _time

import json
from dataclasses import dataclass, field
from typing import Dict, List

from tracemem.domain import Edge, EdgeType
from tracemem.nodes.combination.candidate_selector import (
    evidence_fingerprint, select_candidates,
)
from tracemem.nodes.combination.judge import judge
from tracemem.nodes.combination.kinds import causal as causal_kind
from tracemem.nodes.combination.kinds import contradiction as contra_kind
from tracemem.nodes.combination.kinds import pattern as pattern_kind
from tracemem.nodes.combination.kinds import transitive as trans_kind
from tracemem.nodes.graph_store import pair_key
from tracemem.nodes.birth.person_judge import JUDGE_TOKENS as _JUDGE_TOKENS
from tracemem.nodes.marination.background import CORROBORATING_EPISODES
from tracemem.nodes.marination.mining import (
    GRAMMAR_VERSION, MINING_TOKENS, mine, mining_tasks,
)
from tracemem.nodes.marination.identity import auto_merge_pass, same_as_pass
from tracemem.nodes.marination.preference import preference_pass
from tracemem.nodes.marination.schemas import schema_pass
from tracemem.nodes.marination.semantic import semantic_pass
from tracemem.nodes.marination.succession import succession_pass
from tracemem.nodes.retention.pruner import prune, reconfirm_scan
from tracemem.nodes.engine_version import engine_fingerprint

_ENGINE_VERSION = engine_fingerprint()


def _episode_delta(graph_store, key, episodes):
    """Return episode work not durably completed by this engine version."""
    getm = getattr(graph_store, "get_meta", None)
    raw = getm(key) if getm is not None else None
    seen = set()
    try:
        doc = json.loads(raw) if raw else {}
        if doc.get("engine") == _ENGINE_VERSION:
            seen = set(doc.get("episode_ids") or ())
    except (TypeError, ValueError, AttributeError):
        seen = set()
    fresh = [e for e in episodes if e.id not in seen]

    def complete(done):
        setm = getattr(graph_store, "set_meta", None)
        if setm is None:
            return
        seen.update(e.id for e in done)
        setm(key, json.dumps({"engine": _ENGINE_VERSION,
                              "episode_ids": sorted(seen)},
                             separators=(",", ":")))

    return fresh, complete


@dataclass
class NightReport:
    status: str = "COMPLETE"          # COMPLETE | PAUSED | RESUMABLE
    succession_observed: int = 0
    schemas_touched: List[str] = field(default_factory=list)
    mined: List[tuple] = field(default_factory=list)      # (node_id, key, value)
    hunger_examined: int = 0
    hunger_completed: int = 0
    hunger_unresolved: int = 0
    hunger_grammar: str = ""
    hunger_model_calls: int = 0
    attempted: int = 0
    accepted: int = 0
    rejected: int = 0
    by_kind: Dict[str, int] = field(default_factory=dict)
    pruned: List[str] = field(default_factory=list)
    reconfirmed: List[str] = field(default_factory=list)
    semantic_edges: int = 0
    # Person surfaces the model judged this pass: (surface, is_person).
    people_judged: List[tuple] = field(default_factory=list)
    # Surfaces removed from the entity tier because the model said they are
    # not people. Counted as forward progress: the graph got smaller and
    # truer, which is work.
    people_unbirthed: List[str] = field(default_factory=list)
    # Nodes whose pattern scan was completed and LEDGERED this pass. This is
    # real forward progress even when nothing is accepted: the ledger entry
    # means this node is never scanned again until its evidence changes, so
    # the next slice starts further along. Without it in the report the
    # stand-down guard sees `attempted=1553 accepted=0`, calls a scan that is
    # steadily working through 800 nodes "barren", and stands the night down
    # three slices into a job it was about to finish.
    scanned: int = 0
    # Dry mining chunks whose durable cursor advanced: the next slice starts
    # further into the sweep. Forward progress for the same reason `scanned`
    # is; without it a long honest sweep read as a barren night (2026-10-14).
    hunger_chunks: int = 0
    preferences: List[str] = field(default_factory=list)
    identity_links: int = 0
    merges: List[tuple] = field(default_factory=list)
    tokens_spent: int = 0
    # (phase, time.monotonic()) at the start of each phase. The watchman
    # turns these into per-phase milliseconds for its log line: slices kept
    # expiring with attempted=0 and nothing said WHICH phase ate them.
    phase_starts: List[tuple] = field(default_factory=list)


def run_night(graph_store, claim_store, budget, lease, clock,
              reasoner=None, policy=None, new_episodes=(),
              claims_by_episode=None, routine_model=None,
              budget_b: int = 1000, embedder=None) -> NightReport:
    from tracemem.llm.reasoner_ollama import Interruptible, ReasonerCancelled
    report = NightReport()
    # A waiting person cancels a generation mid-stream (see Interruptible);
    # only a lease that can tell a person from a deadline gets this.
    interrupted = getattr(lease, "interrupted", None)
    if (reasoner is not None and callable(interrupted)
            and hasattr(reasoner, "stream")):
        reasoner = Interruptible(reasoner, interrupted)
    try:
        return _run_night(report, graph_store, claim_store, budget, lease,
                          clock, reasoner, policy, new_episodes,
                          claims_by_episode, routine_model, budget_b, embedder)
    except ReasonerCancelled:
        report.status = "RESUMABLE"
        return report


def _run_night(report, graph_store, claim_store, budget, lease, clock,
               reasoner, policy, new_episodes, claims_by_episode,
               routine_model, budget_b, embedder) -> NightReport:
    from tracemem.nodes.retention.policy import RetentionPolicy
    policy = policy or RetentionPolicy()
    claims_by_episode = claims_by_episode or {}

    if not lease.is_uncontended():
        report.status = "PAUSED"
        return report

    with lease.acquire("reasoner", role="DREAM", priority=0):
        t = clock.now_ms()
        episodes = list(new_episodes)

        # N0/N1 use a durable per-episode receipt. Replaying every historical
        # schema each 30-second slice changed activation, dirtied retrieval,
        # and held the answer thread for 20+ seconds with no new evidence.
        schema_fresh, schema_complete = _episode_delta(
            graph_store, "night_schema_episodes", episodes)

        report.phase_starts.append(('N0', _time.monotonic()))
        # N0 — succession: make time flow through the graph
        if schema_fresh:
            report.succession_observed = succession_pass(episodes, graph_store)

        report.phase_starts.append(('N1', _time.monotonic()))
        # N1 — crystallize: refit prediction, grow schemas
        if routine_model is not None and schema_fresh:
            routine_model.fit(episodes)
        report.schemas_touched = schema_pass(
            episodes, graph_store, t,
            changed_episode_ids=[e.id for e in schema_fresh])
        schema_complete(schema_fresh)
        if lease.preempted():
            report.status = "RESUMABLE"
            return report

        report.phase_starts.append(('N2', _time.monotonic()))
        # N2 — mine: hungry nodes re-read their own raw (the re-marination)
        #
        # MINING MAY NOT STARVE COMBINATION (2026-10-09). N2 runs before N3,
        # and one mining reasoner call can outlast a whole slice. When it did
        # — every slice, while the model server was down and each call sat
        # in 40s of retry backoff — every slice expired inside N2 and N3 was
        # never reached: 24 slices, 0 combinations attempted, "stuck". A
        # slice that was cut off inside N2 is written down, and the NEXT
        # slice skips N2 and goes straight to combination; the one after
        # mines again. Starvation becomes alternation. A night that is never
        # cut off (tests, a long idle window) mines on every pass as before.
        set_meta = getattr(graph_store, "set_meta", None)
        get_meta = getattr(graph_store, "get_meta", None)
        # Meta values come back as strings ("0" is truthy), so compare.
        starved = (str(get_meta("night_n2_cut_off", 0)) == "1"
                   if get_meta else False)
        if starved and set_meta is not None:
            set_meta("night_n2_cut_off", 0)
        tasks = [] if starved else mining_tasks(graph_store, t)
        report.hunger_grammar = GRAMMAR_VERSION
        if set_meta is not None:
            # Durable queue witness. The tasks themselves are reconstructed
            # from durable node hunger + episode membership after a restart;
            # this count makes an interrupted slice observable rather than a
            # queue that exists only in an operator's imagination.
            set_meta("night_hunger_grammar", GRAMMAR_VERSION)
            set_meta("night_hunger_pending", len(tasks))
        for index, task in enumerate(tasks):
            if lease.preempted():
                if set_meta is not None:
                    set_meta("night_hunger_pending", len(tasks) - index)
                    set_meta("night_n2_cut_off", 1)
                report.status = "RESUMABLE"
                return report
            model = (reasoner if reasoner is not None and
                     (budget is None or budget.may_spend(MINING_TOKENS))
                     else None)

            def _spent_mining_call():
                report.hunger_model_calls += 1
                if budget is not None:
                    budget.spend(MINING_TOKENS)
                    report.tokens_spent += MINING_TOKENS

            before_receipt = graph_store.mining_fingerprint(task.node_id,
                                                             task.key)
            def _swept_chunk():
                report.hunger_chunks += 1

            got = mine(task, graph_store, claims_by_episode, reasoner=model,
                       on_reasoner_call=_spent_mining_call,
                       stop=lease.preempted, on_chunk=_swept_chunk)
            report.hunger_examined += 1
            after_receipt = graph_store.mining_fingerprint(task.node_id,
                                                            task.key)
            if after_receipt is not None and after_receipt != before_receipt:
                report.hunger_completed += 1
            report.mined.extend((task.node_id, k, v) for k, v in got)
        report.hunger_unresolved = len(mining_tasks(graph_store, t))
        if set_meta is not None and not starved:
            set_meta("night_hunger_pending", 0)
            set_meta("night_hunger_unresolved", report.hunger_unresolved)
            set_meta("night_hunger_examined", report.hunger_examined)

        report.phase_starts.append(('N2.5', _time.monotonic()))
        # N2.5 — semantic wiring: meaning becomes a force in the graph, so
        # combination can pair things that are ALIKE, not only co-seen. Only
        # when an embedder is supplied (CPU-pinned in prod); bounded to the hot
        # set. Runs before N3 so combination reads the fresh SEMANTIC edges.
        if embedder is not None:
            hot_now = graph_store.nodes_with_activation_ge(0.3, t)
            # WHEN THIS LAST RAN, so an unchanged world deepens nothing. See
            # the long note in semantic.semantic_pass: without it the same
            # pairs were reinforced once per 30-second night slice.
            getm = getattr(graph_store, "get_meta", None)
            since = None
            if getm is not None:
                raw = getm("semantic_pass_t_ms")
                since = int(raw) if raw is not None else None
            # AND SKIPPED ENTIRELY WHEN NOTHING MOVED. `since_t_ms` stopped
            # the pass from DEEPENING an unchanged world, but it still did
            # all the arithmetic to discover there was nothing to do: up to
            # 400 nodes compared pairwise is ~80,000 cosines over 384-dim
            # vectors, in Python, every 30-second slice. Measured: that alone
            # exhausted the 2s budget before the pattern scan was reached, so
            # slices alternated between "did a little" and "expired doing
            # nothing" and a pass could still never COMPLETE. A similarity
            # cannot change unless one of its two labels did.
            moved = (since is None
                     or any(n.last_active_t_ms > since for n in hot_now))
            if moved:
                report.semantic_edges = semantic_pass(
                    graph_store, embedder, hot_now, t, since_t_ms=since)
                setm = getattr(graph_store, "set_meta", None)
                if setm is not None:
                    setm("semantic_pass_t_ms", t)

        report.phase_starts.append(('N3', _time.monotonic()))
        # N3 — combine (pattern scan free; pairs; causal is the only spender)
        #
        # THE PATTERN SCAN IS LEDGERED, exactly as the pair scan already was,
        # and that is what ends the third recurrence of the livelock.
        #
        # Measured on the founder's live graph, 2026-08-20: every slice
        # reported `attempted=1553 accepted=0`, identically, forever. Those
        # 1,553 attempts were all HERE — re-detecting patterns that already
        # exist as nodes and having the judge reject every one as a
        # duplicate. The budget was gone before `select_candidates` was even
        # called, so not one pair was ever processed, the pair ledger never
        # advanced, and the next slice repeated the identical work. "Nothing
        # ever completes" was not a scale problem; it was this loop spending
        # the whole slice re-deriving what it had already derived.
        #
        # The fingerprint is the node's identity plus how much evidence it
        # carries, so a node that GAINS an attribute is re-scanned (its
        # patterns may have changed) and a node that has not moved is not.
        # Same contract as evidence_fingerprint for pairs.
        for hot in graph_store.nodes_with_activation_ge(0.3, t):
            if lease.preempted():
                report.status = "RESUMABLE"
                break
            pkey = "pattern:%s" % hot.id
            pfingerprint = evidence_fingerprint(hot, hot)
            if graph_store.ledger_fresh(pkey, pfingerprint):
                continue
            for prop in pattern_kind.detect(hot, graph_store):
                if prop.is_none:
                    continue
                report.attempted += 1
                _settle(prop, graph_store, report, t)
            graph_store.record_ledger(pkey, "scanned", pfingerprint)
            report.scanned += 1

        for a, b, _prio in select_candidates(graph_store, t, budget_b,
                                             lease=lease):
            if budget is not None and not budget.may_spend(0):
                report.status = "RESUMABLE"
                break
            if lease.preempted():
                report.status = "RESUMABLE"
                break
            key = pair_key(a.id, b.id)
            fingerprint = evidence_fingerprint(a, b)
            # WHAT THE LEDGER REMEMBERS ABOUT THIS PAIR. Only the fingerprint
            # decides whether the pair is revisited (`ledger_fresh`), so this
            # string is for whoever reads the ledger later — and it must not
            # be trampled by a kind that ran afterwards and declined. An
            # accepted belief outranks a rejection, which outranks silence.
            outcome = "none"
            for prop in (trans_kind.detect(a, b, graph_store),
                         contra_kind.detect(a, b, graph_store)):
                if not prop.is_none:
                    report.attempted += 1
                    outcome = _better(outcome,
                                      _settle(prop, graph_store, report, t))
            # CAUSAL IS NOT THE CONSOLATION PRIZE (2026-08-28).
            #
            # This used to read `if reasoner is not None and outcome ==
            # "none"` — the causal kind got a turn only when transitive AND
            # contradiction had both declined. That reads as a cost guard and
            # is not one: `causal_kind.propose` already refuses without a
            # model call unless the pair carries a PRECEDES edge with at
            # least MIN_PRECEDENCE_COUNT evidence, so the GPU was never at
            # risk from this line.
            #
            # What it actually did was starve the only kind that can produce
            # something neither parent says. MEASURED on the founder's graph:
            # 44 PRECEDES edges exist and 28 of them clear the precedence bar,
            # and the derived tier holds ZERO causal nodes — 100 of 100
            # hypotheses are transitive co-occurrence. In a graph built from
            # one screen almost every hot pair shares a neighbour, so
            # transitive fires (or is rejected as a duplicate, which also sets
            # `outcome`) on nearly every pair, and the reasoner was never
            # asked. The napkin's "1+1 = 4, ≠ 2" was gated behind "only if
            # 1+1 did not already make 2".
            #
            # Time-ordering and co-occurrence are different claims about the
            # same pair and both are worth having. The structural gate inside
            # `propose` stays the spend guard, where it belongs.
            if reasoner is not None:
                if budget is None or budget.may_spend(1500):
                    prop = causal_kind.propose(a, b, graph_store, reasoner)
                    if not prop.is_none:
                        if budget is not None:
                            budget.spend(1500)
                            report.tokens_spent += 1500
                        report.attempted += 1
                        outcome = _better(
                            outcome, _settle(prop, graph_store, report, t))
            graph_store.record_ledger(key, outcome, fingerprint)

        # A SPENT SLICE STOPS HERE (2026-10-07). The two loops above `break`
        # on preemption, and everything below them — the person judge (a
        # model call per surface), preferences, merges, prune, reconfirm —
        # polls nothing. Falling through meant an expired 10-second slice ran
        # an unbounded tail on the one thread that answers questions; on the
        # founder's 635k-claim store every ask timed out at 120s whenever the
        # screen locked and the night got its turn. The tail is idempotent
        # and receipt-driven, so the next slice simply picks it up.
        if report.status == "RESUMABLE" or lease.preempted():
            report.status = "RESUMABLE"
            return report

        report.phase_starts.append(('N3.5', _time.monotonic()))
        # N3.5 — understand the person through their VOLITIONAL actions:
        # repeated chosen (not imposed) engagement → a corrigible preference.
        # Silent when no volition signal exists, so it never guesses.
        report.phase_starts.append(('N3.4', _time.monotonic()))
        # N3.4 — IS THAT CAPITALISED WORD A PERSON? Asked once per surface,
        # of the local model, here in the night where model calls belong
        # under the one-GPU law. The regex that proposes people cannot tell
        # "Marta" from "Springfield" — measured on the founder's graph, 110
        # surfaces born as people and roughly 30 of them people. Until a
        # surface is judged it waits in the corroboration room, in raw,
        # losing nothing. See nodes/birth/person_judge.py.
        if reasoner is not None and hasattr(graph_store, "pending_by_kind"):
            from tracemem.nodes.birth.person_judge import adjudicate

            def _samples(key):
                out = []
                for sighting in graph_store.sightings(key)[:4]:
                    cid = sighting.get("claim_id")
                    claim = (claim_store.get(cid)
                             if (claim_store is not None and cid) else None)
                    if claim is not None:
                        out.append(claim.text)
                return out

            from tracemem.nodes.birth.person_judge import (
                enforce, unjudged_node_surfaces)

            # BOTH SOURCES. The waiting room holds mentions not yet born; the
            # graph holds names born BEFORE the gate existed, which would
            # otherwise never be offered for judgement and would sit there
            # forever. Eight of them did.
            pending = (graph_store.pending_by_kind(
                "person", min_episodes=CORROBORATING_EPISODES)
                + unjudged_node_surfaces(graph_store))
            decided = adjudicate(graph_store, pending, reasoner, _samples,
                                 budget=budget, stop=lease.preempted)
            report.tokens_spent += decided["asked"] * _JUDGE_TOKENS
            # A verdict takes effect on the night it is reached, not only on
            # future births.
            enforced = enforce(graph_store)
            report.people_unbirthed = enforced["unbirthed"]
            report.people_judged = (
                [(s, True) for s in decided["people"]]
                + [(s, False) for s in decided["not_people"]])

        report.preferences = preference_pass(graph_store, claims_by_episode, t)

        report.phase_starts.append(('N3.6', _time.monotonic()))
        # N3.6 — cross-hemisphere identity: same-named entities get a weak
        # 'might be' link (never a merge — fusing identities is a separate,
        # high-threshold, deliberate act).
        report.identity_links = same_as_pass(graph_store, t)
        # ...and autonomously fuse only the overwhelmingly-corroborated ones
        # (audited); everything below the auto bar stays surfaced for review.
        report.merges = auto_merge_pass(graph_store, t)

        report.phase_starts.append(('N4', _time.monotonic()))
        # N4 — forget safely, reconfirm honestly
        report.pruned = prune(graph_store, policy, t)
        reconfirm_fresh, reconfirm_complete = _episode_delta(
            graph_store, "night_reconfirm_episodes", episodes)
        report.reconfirmed = reconfirm_scan(graph_store, reconfirm_fresh,
                                            claims_by_episode)
        reconfirm_complete(reconfirm_fresh)
    return report


# The ledger's outcome column, ordered. See the note at its call site: an
# accepted belief outranks a rejection, which outranks a kind that declined.
_OUTCOME_RANK = {"none": 0, "rejected": 1, "accepted": 2}


def _better(current: str, result: str) -> str:
    """The more informative of two settle results for one pair."""
    if _OUTCOME_RANK.get(result, 0) > _OUTCOME_RANK.get(current, 0):
        return result
    return current


def _settle(prop, graph_store, report, t_ms) -> str:
    j = judge(prop, graph_store, t_ms)
    report.by_kind[prop.kind] = report.by_kind.get(prop.kind, 0) + 1
    if not j.accepted:
        report.rejected += 1
        return "rejected"
    # born WARM: a fresh derivation is the most recently thought thing in the
    # graph — born cold it would be pruned by the same night that made it
    # (witnessed: the eat-your-newborns loop, 2026-07-19 gauntlet).
    j.node.activation = max(j.node.activation, 0.6)
    j.node.last_active_t_ms = t_ms
    graph_store.upsert_node(j.node)
    for pid in j.node.parent_ids:
        graph_store.upsert_edge(Edge(src=j.node.id, dst=pid,
                                     type=EdgeType.DERIVED_FROM,
                                     weight=1.0, evidence_count=1,
                                     last_reinforced_t_ms=t_ms))
        for other in (o for o in j.node.parent_ids if o != pid):
            parent_edge = graph_store.edge_between(pid, other)
            if parent_edge is not None:
                parent_edge.reinforce(t_ms, delta=0.5)   # Hebbian (04 §5)
                graph_store.upsert_edge(parent_edge)
    report.accepted += 1
    return "accepted"
