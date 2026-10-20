"""A node's value is not its label — and the guard must know which question it asks.

MEASURED on the live daemon 2026-08-23, three of three entity questions:

    $ ./trace "who is Juno?"          -> (not in memory)
    $ ./trace "what websites do I use?"  -> (not in memory)

Juno is a node: judged a person by the model, seen in seven episodes,
wired to Ben and lichess.org, named by three derived beliefs. The memory
held all of it. The packer rendered `[ENTITY 1a2b3c] seen_as=Juno` and the
needle prompt refused, correctly — a name restated answers nothing.
"""
import pytest

from tracemem.domain import (Confidence, Edge, EdgeType, Grade, Node,
                             NodeKind)
from tracemem.domain.node import Attribute
from tracemem.llm.answer import Answer
from tracemem.llm.bluff_check import guard, is_grounded
from tracemem.nodes.graph_store import MemoryGraphStore
from tracemem.supplier.dossier import (build, find_subjects, render,
                                       supply_subject)


def _n(nid, kind, key, value, eps=("e1", "e2"), t=1000, parents=()):
    return Node(id=nid, kind=kind, activation=0.5, last_active_t_ms=t,
                derivation_depth=1 if parents else 0, parent_ids=parents,
                attributes=[Attribute(key, value, Grade.VERBATIM,
                                      Confidence(0.9), eps, t)])


def _graph():
    g = MemoryGraphStore()
    g.upsert_node(_n("p1", NodeKind.ENTITY, "seen_as", "Juno",
                     eps=("e1", "e2", "e3")))
    g.upsert_node(_n("s1", NodeKind.SITE, "site", "lichess.org"))
    g.upsert_node(_n("s2", NodeKind.SITE, "site", "youtube.com"))
    g.upsert_node(_n("b1", NodeKind.PATTERN, "claim",
                     "recurring: seen_as=Juno observed in 3 distinct moments",
                     parents=("p1",)))
    for a, b in (("p1", "s1"), ("p1", "s2")):
        g.upsert_edge(Edge(src=a, dst=b, type=EdgeType.CO_OCCURS, weight=3.0,
                           evidence_count=3, last_reinforced_t_ms=1000))
    return g


def test_a_question_that_names_a_thing_finds_it():
    g = _graph()
    assert [n.id for n in find_subjects("who is Juno?", g)] == ["p1"]
    assert [n.id for n in find_subjects("what is lichess.org to me?", g)] == ["s1"]


def test_a_question_that_names_nothing_returns_nothing():
    """"This is not my question" and "I have nothing for your question" are
    different, and only the second is an answer. Returning a dossier for a
    question that names nothing would hijack every other retrieval path."""
    g = _graph()
    assert find_subjects("what did I eat", g) == []
    assert supply_subject("what did I eat", g, 4000) is None


def test_stopwords_do_not_drag_in_the_whole_graph():
    g = _graph()
    g.upsert_node(_n("s3", NodeKind.SITE, "site", "the"))
    assert "s3" not in [n.id for n in find_subjects("what is the plan", g)]


def test_the_dossier_carries_what_a_label_cannot():
    g = _graph()
    d = build(g.get_node("p1"), g)
    assert d.sightings == 3
    assert {lab for lab, _t, _w in d.neighbours} == {"lichess.org", "youtube.com"}
    assert any("recurring" in b for b in d.beliefs)


class _Judge:
    """Answers the two guard prompts the way a real model would.

    The synthesis check no longer asks for a verdict, it asks the model to
    NAME the unsupported statement — pointing at a lie is much harder to do
    spuriously than saying "no", which the real model was doing two times in
    three on true answers.
    """

    def __init__(self):
        self.seen = []

    def complete(self, prompt):
        self.seen.append(prompt)
        if "UNSUPPORTED:" in prompt:
            answer = prompt.split("ANSWER:")[-1]
            return "NONE" if "Juno" in answer else "it names Priya"
        return "no"          # the needle check: the dossier does not "answer"


def test_the_needle_guard_would_destroy_a_true_summary():
    """THE BUG. `is_supported` asks whether the FRAGMENT directly contains
    what the QUESTION asks for. A dossier does not say what lichess.org
    *means* to someone, so the needle check answers no — and a true, cited,
    useful answer is replaced by "not in memory"."""
    j = _Judge()
    ans = Answer(text="Juno was seen in 3 moments [p1].",
                 citations=("p1",), abstained=False)
    blocks = {"p1": "[p1] Juno — an entity, seen in 3 moment(s)"}
    assert guard(ans, "who is Juno?", blocks, j, mode="needle").abstained


def test_the_synthesis_guard_keeps_it_and_still_catches_a_lie():
    """The never-lie property is not weakened, it is aimed: for a summary the
    question that catches a fabrication is "does the answer state anything
    the fragment does not"."""
    j = _Judge()
    blocks = {"p1": "[p1] Juno — an entity, seen in 3 moment(s)"}

    true_answer = Answer(text="Juno was seen in 3 moments [p1].",
                         citations=("p1",), abstained=False)
    assert not guard(true_answer, "who is Juno?", blocks, j,
                     mode="synthesis").abstained

    fabricated = Answer(text="Priya is your dentist [p1].",
                        citations=("p1",), abstained=False)
    assert guard(fabricated, "who is Priya?", blocks, j,
                 mode="synthesis").abstained


def test_synthesis_is_checked_against_union_of_every_cited_block():
    class UnionReasoner:
        def __init__(self):
            self.prompts = []

        def complete(self, prompt):
            self.prompts.append(prompt)
            return ("NONE" if "seen in seven moments" in prompt and
                    "alongside Maya" in prompt else "alongside Maya")

    answer = Answer("Juno was seen in seven moments alongside Maya.",
                    ("p1", "p2"), False)
    reasoner = UnionReasoner()
    kept = guard(answer, "who is Juno?", {
        "p1": "Juno was seen in seven moments.",
        "p2": "Juno was alongside Maya.",
    }, reasoner, mode="synthesis")
    assert kept is answer
    assert len(reasoner.prompts) == 1


def test_synthesis_fails_closed_when_a_citation_has_no_block():
    class Approves:
        def complete(self, prompt):
            return "NONE"

    answer = Answer("Juno was seen in seven moments.",
                    ("p1", "missing"), False)
    assert guard(answer, "who is Juno?", {"p1": "seven moments"},
                 Approves(), mode="synthesis").abstained


def test_grounding_fails_closed_on_a_checker_that_says_nothing():
    """An empty or unreadable reply must not read as approval."""
    class Mute:
        def complete(self, prompt):
            return "   \n  "

    assert is_grounded("anything", "a fragment", Mute()) is False


def test_the_refusal_names_what_it_thinks_is_unsupported():
    """A verdict with a reason is inspectable; a bare "no" is a coin flip.
    Measured 2026-08-24: the same question against an unchanged graph refused
    on two runs of three, on an answer that added nothing."""
    from tracemem.llm.bluff_check import unsupported_in

    class Picky:
        def complete(self, prompt):
            return "the answer says she is his dentist"

    assert unsupported_in("x", "y", Picky()) == "the answer says she is his dentist"

    class Fine:
        def complete(self, prompt):
            return "NONE"

    assert unsupported_in("x", "y", Fine()) == ""


def test_the_weakest_beliefs_are_ranked_last_not_first():
    """On the live dossier for Juno, co-occurrence hypotheses filled the
    whole answer — "Juno and window_stack api: both repeatedly present
    with youtube.com" — and crowded out the part a person wanted, that she
    was seen in three moments alongside Adele. All true, none informative.
    Ranked last rather than dropped: they are real derivations."""
    g = _graph()
    for i in range(4):
        g.upsert_node(_n("w%d" % i, NodeKind.HYPOTHESIS, "claim",
                         "Juno and thing%d: both repeatedly present with x" % i,
                         parents=("p1",)))
    d = build(g.get_node("p1"), g)
    assert d.beliefs, "a dossier with derivations must show some"
    assert "recurring" in d.beliefs[0], d.beliefs


def test_a_dossier_still_quotes_the_screen_on_a_freshly_booted_daemon():
    """Boot became O(new claims) because an all-claims bind took two hours
    and blocked the eye. The cost: `claims_by_episode` is nearly empty on a
    fresh daemon, so a dossier had attributes and neighbours and never a
    quote. It reads the claim log around the node's own timestamps instead."""
    from tests.helpers import make_claim

    class Log:
        def __init__(self, claims):
            self._c = claims

        def read_range(self, t0, t1, place_key=None):
            return [c for c in self._c if t0 <= c.coord.t_ms <= t1]

    g = _graph()
    log = Log([make_claim(text="coffee with Juno about the kiln", t_ms=1000),
               make_claim(text="unrelated chrome label", t_ms=1010)])

    d = build(g.get_node("p1"), g, claims_by_episode=None, claim_log=log)
    assert any("Juno" in e for e in d.evidence)
    assert not any("unrelated" in e for e in d.evidence)


def test_a_dossier_does_not_list_the_things_that_are_everywhere():
    """`dev:mac-1` is in 76.8% of episodes and `Brave Browser` in 76.2%.
    Telling someone their friend was "seen alongside your own computer" is
    noise, and the combination tier already refuses to reason about those
    nodes — the dossier should not quote them either."""
    from tracemem.nodes.combination.candidate_selector import (
        MIN_EPISODES_FOR_UBIQUITY)

    g = _graph()
    eps = tuple("e%d" % i for i in range(MIN_EPISODES_FOR_UBIQUITY + 5))
    g.upsert_node(_n("dev", NodeKind.PLACE, "place", "dev:mac-1", eps=eps))
    g.upsert_node(_n("dup", NodeKind.SITE, "site", "lichess.org"))  # same label
    for other in ("dev", "dup"):
        g.upsert_edge(Edge(src="p1", dst=other, type=EdgeType.CO_OCCURS,
                           weight=9.0, evidence_count=9,
                           last_reinforced_t_ms=1000))
    labels = [lab for lab, _t, _w in build(g.get_node("p1"), g).neighbours]
    assert "dev:mac-1" not in labels
    assert labels.count("lichess.org") == 1, "the memory stuttered: %s" % labels


def test_when_the_guard_refuses_the_prose_the_evidence_is_still_shown():
    """MEASURED: "who is Juno?" answered on one run and returned
    "not in memory" on the next, from the same graph, twice — because the
    grounding check is a MODEL CALL and it is not deterministic.

    For a needle question "not in memory" is the right answer: there was
    nothing there. For a summary it is a lie of a different kind — the memory
    is holding a dossier with three sightings, two neighbours and three
    derived beliefs, and telling the founder it holds nothing.

    The fallback cannot fabricate: its text is the CONTEXT ITSELF, rendered
    rather than generated. Less fluent, never less true.
    """
    from tracemem.app.hub import Hub, Wiring
    from tracemem.supplier.packer import Context, CtxBlock
    from tracemem.domain.grading import grade_for

    hub = Hub(Wiring(claim_log=None, graph_store=None, vector_index=None,
                     reasoner=None, spotlight=None, clock=None))
    ctx = Context((CtxBlock(
        kind="dossier",
        text="[abc12345] Juno — an entity, seen in 3 moment(s)\n"
             "        alongside: youtube.com, lichess.org",
        grade=grade_for("ax"), source_ids=("abc12345",), t_ms=1000),), 40)

    out = hub._evidence_only(ctx)
    assert out.abstained is False
    assert "Juno" in out.text and "lichess.org" in out.text
    assert out.citations == ("abc12345",)
    # Every line it shows came from the context — nothing generated.
    body = out.text.split("evidence:\n\n", 1)[1]
    for line in body.splitlines():
        assert line in ctx.blocks[0].text


def test_the_evidence_fallback_is_bounded():
    """A day's timeline must not become the answer."""
    from tracemem.app.hub import Hub, Wiring
    from tracemem.supplier.packer import Context, CtxBlock
    from tracemem.domain.grading import grade_for

    hub = Hub(Wiring(claim_log=None, graph_store=None, vector_index=None,
                     reasoner=None, spotlight=None, clock=None))
    big = "\n".join("[id%03d] moment number %d" % (i, i) for i in range(200))
    ctx = Context((CtxBlock("moment", big, grade_for("ax"), ("id000",), 1),),
                  100)
    out = hub._evidence_only(ctx)
    assert out.text.count("\n") < 30
    assert "more line(s)" in out.text


def test_the_dossier_does_not_put_the_schema_in_the_evidence():
    """`NodeKind.ENTITY` is a database type, not something observed.

    Rendered as "Juno — an entity" the model restated it as a fact
    ("Juno is an entity"), and the never-lie checker named THAT sentence
    as the unsupported one — deterministically, on three consecutive runs.
    So the product kept falling back to raw evidence on a question it could
    answer, because its own vocabulary had leaked into the evidence.

    Kinds that mean something to a reader keep their word: "a site" and
    "an app" tell a person what they are looking at.
    """
    g = _graph()
    person = render([build(g.get_node("p1"), g)])
    assert "an entity" not in person
    assert "a person" in person

    site = render([build(g.get_node("s1"), g)])
    assert "a site" in site


def test_no_renderer_leaks_a_database_type_into_the_evidence():
    """ONE TABLE FOR THE WHOLE PRODUCT.

    `NodeKind.ENTITY` reached the model as evidence from `dossier.render`,
    and the never-lie checker destroyed 5 of 6 answers because the model
    restated it as a fact. Two more renderers had the same leak — the needle
    path's `[ENTITY 1a2b3c]` and the ranking's "ENTITY, ordered by..." —
    which is why the mapping now lives beside NodeKind rather than in
    whichever file noticed first.
    """
    from tracemem.domain import NodeKind
    from tracemem.domain.node import human_kind
    from tracemem.nodes.accrual import label_of
    from tracemem.supplier import ranking
    from tracemem.supplier.packer import _render_node

    assert human_kind(NodeKind.ENTITY) == "person"
    assert human_kind(NodeKind.SITE) == "site"      # already a human word
    assert human_kind("entity") == "person"         # accepts the raw value

    g = _graph()
    rendered = [
        render([build(g.get_node("p1"), g)]),
        _render_node(g.get_node("p1")),
        ranking.render(NodeKind.ENTITY,
                       ranking.rank(g, NodeKind.ENTITY)),
    ]
    for text in rendered:
        assert "entity" not in text.lower(), text
    del label_of


def test_the_checker_ignores_statements_about_the_evidence_itself():
    """TWO OF THIS SYSTEM'S OWN INSTRUCTIONS WERE CONTRADICTING EACH OTHER.

    `DOSSIER_RULES` tells the model: "if the dossier does not answer the
    question, say exactly what it does show and what it does not". The
    grounding checker then flagged "The dossier does not show anything else
    about Hana" as an unsupported statement — deterministically, on three
    consecutive runs — and the whole answer was thrown away.

    Pedantically the checker was right: a statement about the ABSENCE of
    information is not present in the fragment. But a claim about the
    EVIDENCE cannot be a fabrication about the founder's life, and the guard
    exists to catch the second thing.
    """
    from tracemem.llm.bluff_check import GROUND_PROMPT

    assert "IGNORE statements about the dossier" in GROUND_PROMPT
    assert "cannot be a fabrication" in GROUND_PROMPT

    # And a real fabrication is still named.
    class Picky:
        def complete(self, prompt):
            return "the answer says Hana is his dentist"

    from tracemem.llm.bluff_check import unsupported_in
    assert unsupported_in("Hana is his dentist", "Hana — a person",
                          Picky())
