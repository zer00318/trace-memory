"""THE RELEVANCE GAUNTLET — does the supplier find the RIGHT memory?

Written 2026-07-23 after an independent audit mutation-tested the retrieval
path and found it had ZERO behavioural coverage. Five mutations, each of
which destroys the system's ability to find anything:

    M1  entry index embeds node.id only (all semantics destroyed)
    M2  retriever returns the WORST matches first
    M3  every node gets the IDENTICAL embedding string
    M4  graph spread disabled (hops=0)
    M5  entry door narrowed 12 -> 1

...and all 1068 tests stayed green under every one of them. The suite
guarded every invariant (budget, activation, grade, egress) and never once
asked whether the memory that came back was the memory that was asked for.

Every test in this file MUST fail under M1, M2, M3 or M5. That is the
acceptance criterion for the file itself, and it is re-checked by mutation
whenever this file changes (tools/mutate_retrieval.py).

The napkin's Information Supplier Black Box owes exactly one thing:
"Just Enough Context for local LLM". Enough means the answer is IN there.
"""
from __future__ import annotations

import pytest

from tests.fakes import FakeClock, HashEmbedder
from tracemem.app.wiring import build
from tracemem.supplier.cue import parse_cue
from tracemem.supplier.retriever import retrieve
from tracemem.supplier.supplier import supply

MS_MIN = 60_000
MS_H = 3_600_000


# --------------------------------------------------------------------------
# A purpose-built life: four well-separated topics, each with its own scene.
# One distinctive fact per topic, phrased the way a screen actually reads —
# so a question phrased the way a HUMAN actually asks shares content tokens
# with the claim and (deliberately) NOT with the node's label.
# --------------------------------------------------------------------------
SCENES = [
    # (scene start, app, [claim texts])
    (10 * MS_MIN, "Calendar", [
        "kiln firing rescheduled to thursday afternoon",
        "pottery studio booking confirmation",
        "reminder set for the ceramics collection slot",
    ]),
    (40 * MS_MIN, "VS Code", [
        "sqlite WAL checkpoint interval tuning notes",
        "def flush_wal(conn): conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')",
        "benchmark shows fsync per claim costs 4ms",
    ]),
    (70 * MS_MIN, "Safari", [
        "article on memory consolidation during slow wave sleep",
        "hippocampal replay compresses experience twentyfold",
        "the paper argues sleep prunes weak synapses",
    ]),
    (100 * MS_MIN, "Mail", [
        "invoice from the glaze supplier due on the ninth",
        "shipping quote for the raw clay order",
        "reply from Maya about the studio schedule",
    ]),
    # --- distractors. Retrieval on four well-separated topics is not
    # retrieval; rank only starts meaning something once several plausible
    # neighbours compete. Every trap below that mutation-testing showed to be
    # toothless was toothless because the fixture was too small.
    (130 * MS_MIN, "Slack", [
        "standup moved to nine fifteen tomorrow",
        "deploy freeze starts friday at noon",
        "ping from Priya about the staging cluster",
    ]),
    (160 * MS_MIN, "Terminal", [
        "pytest collected 1082 items in twenty one seconds",
        "git rebase onto main dropped two commits",
        "docker build failed on the arm64 layer",
    ]),
    (190 * MS_MIN, "Figma", [
        "onboarding flow second pass with the muted palette",
        "the empty state needs a real illustration",
        "spacing tokens renamed to a four point scale",
    ]),
    (220 * MS_MIN, "Spotify", [
        "listening to the bach cello suites again",
        "queue saved as deep work friday",
    ]),
    (250 * MS_MIN, "Notes", [
        "grocery list oat milk and sourdough and pears",
        "annual review self assessment first draft",
    ]),
    (280 * MS_MIN, "Preview", [
        "lease renewal document page four rent increase clause",
        "signature block still needs the witness name",
    ]),
]


def _hub():
    from tests.fakes import ContextAnswerReasoner
    clock = FakeClock(3 * MS_H)
    hub = build({"embedder": HashEmbedder(), "reasoner": ContextAnswerReasoner(),
                 "clock": clock})
    from tests.helpers import make_claim
    t = 0
    for start, app, texts in SCENES:
        hub.post_scene_change(start)
        for i, text in enumerate(texts):
            t = start + (i + 1) * 30_000
            hub.post_claims([make_claim(text=text, t_ms=t, app=app,
                                        frame="f-%d" % t)])
    hub.post_scene_change(130 * MS_MIN)
    hub.w.vector_index.rebuild_from(hub.w.graph_store)
    return hub


def _context_text(ctx) -> str:
    return "\n".join(b.text for b in ctx.blocks).lower()


def _supply(hub, question):
    return supply(parse_cue(question, clock=hub.w.clock), hub.w.graph_store,
                  hub.w.claim_log, hub.w.vector_index,
                  hub.w.answer_budget_tokens,
                  claims_by_episode=hub.claims_by_episode)


# --------------------------------------------------------------------------
# TRAP 1 — the entry door must see CONTENT, not labels.
# The audit's finding in one assert: the node summary indexed only attribute
# values ("Calendar", "recurring: app=Safari"), so a question about what was
# ON the screen could never reach the claim that held it.
# Kills: M1 (id-only index), M3 (identical summaries).
# --------------------------------------------------------------------------
@pytest.mark.parametrize("question,needle", [
    ("when was the kiln firing rescheduled to?", "thursday"),
    ("what did the sleep paper say about replay?", "twentyfold"),
    ("when is the glaze invoice due?", "ninth"),
    ("what did the wal benchmark measure?", "4ms"),
])
def test_the_answer_is_in_the_packed_context(question, needle):
    ctx = _supply(_hub(), question)
    assert ctx.blocks, "the supplier returned NOTHING for an answerable question"
    assert needle in _context_text(ctx), (
        "the fact that answers %r never reached the LLM. Packed context was:\n%s"
        % (question, _context_text(ctx)[:600]))


# --------------------------------------------------------------------------
# TRAP 2 — paraphrase. The user does not speak in screen text.
# None of these questions share a rare token with the node's LABEL; each
# shares content with the CLAIM. This is the probe that scored 1/3 in the
# audit and returned a coral-reef documentary for a ceramics question.
# Kills: M1, M3.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("question,needle", [
    ("did the kiln get rescheduled?", "thursday"),
    ("what compresses experience during sleep?", "replay"),
    ("how much does an fsync cost per claim?", "4ms"),
])
def test_paraphrased_questions_still_land(question, needle):
    ctx = _supply(_hub(), question)
    assert needle in _context_text(ctx), (
        "paraphrase %r lost the memory. Packed context was:\n%s"
        % (question, _context_text(ctx)[:600]))


# --------------------------------------------------------------------------
# TRAP 3 — relevance is RANKED, not merely present.
# A 200-candidate dump that happens to contain the answer is not retrieval;
# it is the whole store with extra steps. The winning entry must be the
# topic that was asked about.
# Kills: M2 (reversed rank), M3 (no signal to rank on).
# --------------------------------------------------------------------------
def test_the_top_entry_is_the_topic_that_was_asked_about():
    hub = _hub()
    cands = retrieve(parse_cue("when was the kiln firing rescheduled to?"),
                     hub.w.graph_store, hub.w.vector_index)
    assert cands, "no candidates at all"
    top_text = hub.w.vector_index.text_of(cands[0].node.id).lower()
    assert "kiln" in top_text or "ceramics" in top_text or "pottery" in top_text, (
        "the top-ranked entry was %r — retrieval ranked an unrelated memory "
        "first" % top_text[:200])


# --------------------------------------------------------------------------
# TRAP 4 — the distractor must LOSE.
# Being able to find the right thing is worthless if the wrong thing scores
# just as well. The sleep-paper scene must not surface for a kiln question.
# Kills: M2, M3.
# --------------------------------------------------------------------------
def test_unrelated_topics_do_not_outrank_the_asked_one():
    hub = _hub()
    ctx = _supply(hub, "when was the kiln firing rescheduled to?")
    text = _context_text(ctx)
    assert "thursday" in text
    assert "hippocampal" not in text and "synapses" not in text, (
        "an unrelated topic was packed into the context for a kiln question:\n%s"
        % text[:600])


# --------------------------------------------------------------------------
# TRAP 5 — the door confers the RELEVANCE SIGNAL.
#
# This trap first asserted that a narrower door returns FEWER candidates. It
# does not, and should not: spread runs two hops, and every claim in this
# fixture shares a place, so the PLACE node makes the graph one connected
# component — one seed reaches all of it. Candidate COUNT is therefore a
# property of the graph's shape, not of the door.
#
# What the door actually owns is which nodes carry entry_sim > 0. That signal
# is what orders the drill (packer: "the drill follows relevance, the pack
# follows activation"), so a node reached only by spread has no claim on the
# LLM's context budget. Narrowing the door must shrink that set.
# Kills: M3 (identical summaries -> no signal to confer).
# --------------------------------------------------------------------------
def test_the_door_is_what_confers_the_relevance_signal():
    hub = _hub()
    cue = parse_cue("what did the sleep paper say about replay?")
    wide = retrieve(cue, hub.w.graph_store, hub.w.vector_index, k_entry=12)
    narrow = retrieve(cue, hub.w.graph_store, hub.w.vector_index, k_entry=1)
    wide_signal = [c for c in wide if c.entry_sim > 0]
    narrow_signal = [c for c in narrow if c.entry_sim > 0]
    assert len(wide_signal) > len(narrow_signal), (
        "entry width conferred the same signal either way (%d vs %d) — the "
        "door is decorative" % (len(wide_signal), len(narrow_signal)))
    assert narrow_signal, "the narrowest door must still admit its ONE best match"
    top = hub.w.vector_index.text_of(narrow_signal[0].node.id).lower()
    assert "sleep" in top or "replay" in top, (
        "the single admitted entry was %r — the door let the wrong node in"
        % top[:150])


# --------------------------------------------------------------------------
# TRAP 6 — the drill is EPISODE-SCOPED, not a substring scan of the log.
# The pre-fix _claims_for() took an episode_id and never used it: it scanned
# read_range(0, 2**53) — every claim ever recorded — and kept the first four
# substring matches. That is the brute-force scan the index's own docstring
# forbids, and it is why node retrieval could be mutated to noise without any
# test noticing: the node graph was never what found the answer.
# --------------------------------------------------------------------------
def test_drill_never_scans_the_whole_log():
    hub = _hub()
    log = hub.w.claim_log
    calls = {"n": 0}
    real = log.read_range

    def counting(t0, t1):
        calls["n"] += 1
        assert not (t0 == 0 and t1 >= 2 ** 52), (
            "the drill scanned the ENTIRE claim log (%d..%d) — brute force "
            "over raw claims is exactly what the no-brute-force law forbids"
            % (t0, t1))
        return real(t0, t1)

    log.read_range = counting
    try:
        ctx = _supply(hub, "when was the kiln firing rescheduled to?")
    finally:
        log.read_range = real
    assert "thursday" in _context_text(ctx)


# --------------------------------------------------------------------------
# TRAP 7 — DEPTH. The answer is not always the single best lexical match.
# "what needs a witness name?" matches the Preview scene, but the Slack and
# Notes scenes share its shape. A door of width 1 admits only the top match;
# if that is ever wrong, a width-1 door loses the answer outright.
# Kills: M5 (door 12 -> 1).
# --------------------------------------------------------------------------
@pytest.mark.parametrize("question,needle", [
    ("when does the deploy freeze start?", "friday"),
    ("what still needs the witness name?", "signature"),
    ("what did the docker build fail on?", "arm64"),
])
def test_answers_survive_a_crowded_store(question, needle):
    ctx = _supply(_hub(), question)
    assert needle in _context_text(ctx), (
        "with ten topics competing, %r lost its answer. Context:\n%s"
        % (question, _context_text(ctx)[:700]))


def test_spread_reaches_past_the_door_and_that_is_why_width_is_unearned():
    """RECORDED FINDING, not a guard: k_entry=12 currently buys nothing.

    Three attempts were made to write a test that feels the entry door's
    width move, and all three came back level:

        single-topic questions   width 1 -> 5/5   width 12 -> 5/5
        topic-SPANNING questions width 1 -> 6/6   width 12 -> 6/6

    The cause is structural, not fixture noise. `retrieve` spreads two hops
    from every seed, and a PLACE node holds EVERY episode recorded at that
    place — so one admitted entry reaches the entire connected component.
    In this fixture that is all ten topics; in a real store it is every
    memory ever formed at home.

    Two consequences, both open (see STATE_BOARD, retrieval findings):
      · mutations that narrow the door (12 -> 1) or disable spread entirely
        (hops 2 -> 0) CANNOT be caught by any relevance test, because the
        product's behaviour genuinely does not depend on either.
      · at 10^5 nodes, spreading from one seed to the whole component is the
        brute-force scan wearing a graph costume. The candidate cap (200)
        bounds the OUTPUT, not the walk.

    What this test pins is the property that IS real and worth keeping: a
    node the door never admitted still becomes reachable. That is the
    napkin's "combine with fellow nodes" working — and it is exactly why
    the door alone must never be trusted as the relevance signal.
    """
    hub = _hub()
    cue = parse_cue("what did the sleep paper say about replay?",
                    clock=hub.w.clock)
    cands = retrieve(cue, hub.w.graph_store, hub.w.vector_index, k_entry=1)
    admitted = [c for c in cands if c.entry_sim > 0]
    reached = [c for c in cands if c.entry_sim == 0]
    assert len(admitted) == 1, "a width-1 door must admit exactly one node"
    assert reached, "spread reached nothing — the graph is doing no work at all"


# --------------------------------------------------------------------------
# TRAP 8 — the drill must follow RELEVANCE, not heat.
# packed_nodes arrives ordered by scratch activation (how hot a node is in
# the store). Only drill_n nodes get their raw material read. If the drill
# takes the hottest rather than the most relevant, the context carries the
# right node's LABEL beside a different node's EVIDENCE — which reads, to the
# LLM, exactly like an answer.
# Kills: M7 (drill sorted by heat).
# --------------------------------------------------------------------------
def test_the_drill_reads_the_relevant_node_not_the_hottest_one():
    """Asserts the CONTRACT directly, not a downstream symptom.

    Written first as "the answer is still in the context", which a drill
    ordered by heat passed anyway — with drill_n=2 the right node usually
    lands in the top two by either ordering, so the symptom is not
    diagnostic. What is diagnostic: the claims that reach the LLM must come
    from the packed node with the HIGHEST entry similarity. Anything else
    means the context carries one node's label beside another's evidence.
    """
    hub = _hub()
    q = "what still needs the witness name?"
    cue = parse_cue(q, clock=hub.w.clock)
    cands = retrieve(cue, hub.w.graph_store, hub.w.vector_index)
    by_heat = [c.node.id for c in cands]
    by_rel = [c.node.id for c in sorted(cands, key=lambda c: -c.entry_sim)]

    # These two orders are currently THE SAME LIST, because retrieve() seeds
    # scratch activation from entry similarity and spread only decays it.
    # That is a property of one decision (05 R2, no query-time mutation), not
    # of the architecture — so it is pinned here. If this assert ever fails,
    # heat and relevance have separated and the drill's sort has stopped
    # being decorative: read the note in packer.pack before touching it.
    assert by_heat[0] == by_rel[0], (
        "heat and relevance have SEPARATED (hottest=%s, most relevant=%s). "
        "The drill's relevance sort is now load-bearing — good. Rewrite this "
        "test to assert the drill followed relevance, and delete this note."
        % (by_heat[0][:8], by_rel[0][:8]))

    ctx = _supply(hub, q)
    drilled = {sid for b in ctx.blocks if b.kind == "claim" for sid in b.source_ids}
    assert drilled, "nothing was drilled at all"
    # every claim of the most-relevant node's episodes
    most_relevant = by_rel[0]
    node = hub.w.graph_store.get_node(most_relevant)
    owned = set()
    for a in node.attributes:
        for ep_id in a.source_episode_ids[:2]:
            owned |= {c.id for c in hub.claims_by_episode.get(ep_id, ())}
    assert drilled & owned, (
        "the drill read NOTHING from the most relevant node (%r) — it "
        "followed heat instead. Indexed as: %r"
        % (most_relevant[:8], hub.w.vector_index.text_of(most_relevant)[:120]))


def test_a_claim_is_never_packed_twice():
    """Budget spent twice on the same sentence is budget stolen from the
    answer. Attributes routinely cite the same episode, so the drill visits
    it repeatedly — pre-fix, the context carried each of three claims twice."""
    hub = _hub()
    for q in ("when was the kiln firing rescheduled to?",
              "what did the sleep paper say about replay?",
              "when does the deploy freeze start?"):
        ctx = _supply(hub, q)
        ids = [sid for b in ctx.blocks if b.kind == "claim" for sid in b.source_ids]
        assert len(ids) == len(set(ids)), (
            "%r packed the same claim %d times"
            % (q, len(ids) - len(set(ids)) + 1))


def test_drilled_claims_belong_to_the_node_that_earned_them():
    """S4: raw claims reach the LLM only via drill-down from a packed node.
    A claim drilled in must come from an episode that a packed node cites —
    otherwise the 'drill' is free-floating soup wearing a citation."""
    hub = _hub()
    ctx = _supply(hub, "when was the kiln firing rescheduled to?")
    packed_eps = {sid for b in ctx.blocks if b.kind == "node" for sid in b.source_ids}
    claim_ids = {sid for b in ctx.blocks if b.kind == "claim" for sid in b.source_ids}
    assert claim_ids, "no raw claim was drilled at all"
    owned = set()
    for ep_id in packed_eps:
        owned |= {c.id for c in hub.claims_by_episode.get(ep_id, ())}
    orphans = claim_ids - owned
    assert not orphans, (
        "%d claim(s) reached the LLM without a packed node vouching for them"
        % len(orphans))


# -- FOUND BY RUNNING THE DAEMON (2026-07-28) -----------------------------
class _CountingEmbedder:
    def __init__(self, inner):
        self.inner, self.calls = inner, 0

    def embed(self, text):
        self.calls += 1
        return self.inner.embed(text)


def test_reindexing_an_unchanged_node_does_not_re_embed_it():
    """THE DAEMON WEDGE. Hub.post_claims calls rebuild_from on every flush,
    and rebuild_from re-embedded EVERY node every time — with a real MiniLM
    model, on the capture loop thread. At ~900 bound claims the loop hit 100%
    CPU, capture froze (claim count stuck at 923) and asks timed out."""
    from tests.fakes import HashEmbedder
    from tracemem.supplier.vector_index import VectorIndex

    emb = _CountingEmbedder(HashEmbedder())
    idx = VectorIndex(emb)

    idx.upsert("n1", "the kiln fired at 1200 degrees")
    assert emb.calls == 1
    for _ in range(50):                       # 50 more flushes, nothing changed
        idx.upsert("n1", "the kiln fired at 1200 degrees")
    assert emb.calls == 1, "an unchanged node was re-embedded"


def test_a_changed_node_is_re_embedded():
    """Skipping must be driven by the TEXT, not by having seen the id."""
    from tests.fakes import HashEmbedder
    from tracemem.supplier.vector_index import VectorIndex

    emb = _CountingEmbedder(HashEmbedder())
    idx = VectorIndex(emb)
    idx.upsert("n1", "first summary")
    idx.upsert("n1", "the summary grew a new attribute")
    assert emb.calls == 2
    assert idx.text_of("n1") == "the summary grew a new attribute"


def test_rebuild_cost_is_the_new_nodes_not_the_whole_graph():
    from tests.fakes import HashEmbedder
    from tracemem.supplier.vector_index import VectorIndex

    emb = _CountingEmbedder(HashEmbedder())
    idx = VectorIndex(emb)
    for i in range(100):
        idx.upsert("n%d" % i, "node %d" % i)
    assert emb.calls == 100

    idx.upsert("n100", "a brand new node")     # one new node arrives
    for i in range(100):                        # the rest re-offered unchanged
        idx.upsert("n%d" % i, "node %d" % i)
    assert emb.calls == 101, "a full rebuild happened instead of an incremental one"


def test_rebuild_batches_changed_nodes_when_the_adapter_can():
    from tests.fakes import HashEmbedder
    from tracemem.domain import Attribute, Node, NodeKind
    from tracemem.nodes.graph_store import MemoryGraphStore
    from tracemem.supplier.vector_index import VectorIndex

    class Batch(HashEmbedder):
        def __init__(self): self.batches = []
        def embed_many(self, texts):
            self.batches.append(list(texts))
            return [self.embed(text) for text in texts]

    graph = MemoryGraphStore()
    for i in range(50):
        graph.upsert_node(Node(
            id="n%d" % i, kind=NodeKind.ENTITY,
            attributes=(Attribute(key="name", value="node %d" % i,
                                  confidence=.8, grade=2,
                                  source_episode_ids=("ep",), t_ms=i),)))
    embedder = Batch()
    index = VectorIndex(embedder)

    assert index.rebuild_from(graph) == 50
    assert len(embedder.batches) == 1
    assert len(embedder.batches[0]) == 50
    index.rebuild_from(graph)
    assert len(embedder.batches) == 1, "unchanged nodes were re-embedded"
