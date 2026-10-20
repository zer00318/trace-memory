"""A capitalised word is not a person just because a regex found it.

MEASURED on the founder's graph, 2026-08-20: 110 surfaces had been born as
people and roughly 30 of them were people. The rest were categories the
regex cannot represent —

    cities      Springfield · Riverton · Lakeside · Norway · Peru
    apps        Claude · Instagram · Notes · Calendar · Google
    UI chrome   Trash · Watch · Edit · File · Folder · Next · Sign
    German      Kontakt · Bildquelle · Startseite
    fragments   Aug · Jul · Mar · Also · Correction · Overview

"Springfield" and "Marta" are the same shape. No stoplist fixes a category.
"""
import pytest

from tracemem.nodes.birth.person_judge import (VERDICT_KEY, adjudicate,
                                               is_person, verdict_for)
from tracemem.nodes.graph_store import MemoryGraphStore


class Judge:
    """Says yes only to the names it was told are names."""

    def __init__(self, people=(), fail=False):
        self.people = {p.lower() for p in people}
        self.fail = fail
        self.asked = []

    def complete(self, prompt):
        if self.fail:
            raise RuntimeError("ollama is not running")
        surface = prompt.split('is: "', 1)[1].split('"', 1)[0]
        self.asked.append(surface)
        return "yes" if surface.lower() in self.people else "no"


def test_a_city_is_not_a_person_and_a_name_is():
    j = Judge(people=["Marta"])
    assert is_person("Marta", ["coffee with Marta"], j) is True
    assert is_person("Springfield", ["flights to Springfield"], j) is False


def test_anything_not_yes_is_a_no():
    """The failure hierarchy puts never-lying above coverage: a memory
    holding eighty people who do not exist has broken its first promise, and
    every belief derived from them inherits it. A missing person costs
    coverage, which is the bottom of the list."""

    class Waffle:
        def complete(self, prompt):
            return "It could be either, depending on context."

    assert is_person("Source", ["the Source said"], Waffle()) is False


def test_the_verdict_is_recorded_so_the_question_is_asked_once():
    g = MemoryGraphStore()
    j = Judge(people=["Tamsin"])
    pending = [("person:tamsin", "Tamsin"), ("person:trash", "Trash")]

    first = adjudicate(g, pending, j, samples_for=lambda k: ["some text"])
    assert first["people"] == ["Tamsin"] and first["not_people"] == ["Trash"]
    assert verdict_for(g, "person:tamsin") is True
    assert verdict_for(g, "person:trash") is False

    second = adjudicate(g, pending, j, samples_for=lambda k: ["some text"])
    assert second["asked"] == 0, "the same surface was judged twice"
    assert len(j.asked) == 2


def test_an_unreachable_model_leaves_the_question_OPEN():
    """A model that cannot be reached must not become a cached NO. That would
    turn one outage into a permanently empty person tier, silently — the
    exact shape of every failure this project has had."""
    g = MemoryGraphStore()
    out = adjudicate(g, [("person:tamsin", "Tamsin")], Judge(fail=True),
                     samples_for=lambda k: [])
    assert out["asked"] == 0
    assert verdict_for(g, "person:tamsin") is None

    out = adjudicate(g, [("person:tamsin", "Tamsin")], Judge(people=["Tamsin"]),
                     samples_for=lambda k: [])
    assert verdict_for(g, "person:tamsin") is True


def test_an_unjudged_mention_never_becomes_a_node():
    """The gate: corroboration proves a surface was SEEN twice. It says
    nothing about whether the surface is a person — "Trash" was seen seven
    times."""
    from tests.helpers import make_claim
    from tracemem.domain.episode import Episode
    from tracemem.domain.time import TimeRange
    from tracemem.nodes.accrual import names_of
    from tracemem.nodes.marination.background import upkeep

    def run(g, ep_id, text, t_ms):
        c = make_claim(text=text, t_ms=t_ms)
        ep = Episode(id=ep_id, t_range=TimeRange(t_ms, t_ms + 1),
                     place_key="system", claim_ids=(c.id,))
        upkeep(ep, [c], g)

    g = MemoryGraphStore()
    run(g, "ep-1", "coffee with Tamsin", 1000)
    run(g, "ep-2", "thanks Tamsin for that", 2_000_000)
    assert not [n for n in g.nodes.values() if "Tamsin" in names_of(n)]

    g2 = MemoryGraphStore()
    g2.set_meta(VERDICT_KEY % "person:tamsin", "yes")
    run(g2, "ep-1", "coffee with Tamsin", 1000)
    run(g2, "ep-2", "thanks Tamsin for that", 2_000_000)
    assert [n for n in g2.nodes.values() if "Tamsin" in names_of(n)]


def test_names_born_before_the_gate_are_still_offered_for_judgement():
    """The waiting room only holds mentions that have NOT become nodes. A
    surface born before the gate existed would never be offered and would sit
    in the graph forever — eight of them did on the founder's graph."""
    from tracemem.domain import Confidence, Grade, Node, NodeKind
    from tracemem.domain.node import Attribute
    from tracemem.nodes.birth.person_judge import unjudged_node_surfaces

    g = MemoryGraphStore()
    g.upsert_node(Node(
        id="n1", kind=NodeKind.ENTITY, last_active_t_ms=1,
        attributes=[Attribute("seen_as", "Startseite", Grade.VERBATIM,
                              Confidence(0.9), ("e1",), 1)]))
    assert unjudged_node_surfaces(g) == [
        ("person:startseite", "Startseite")]

    g.set_meta(VERDICT_KEY % "person:startseite", "no")
    assert unjudged_node_surfaces(g) == []


def test_a_verdict_takes_effect_the_night_it_is_reached():
    """Gating future births alone would leave every false person already on
    the graph in place, forever."""
    from tracemem.domain import Confidence, Grade, Node, NodeKind
    from tracemem.domain.node import Attribute
    from tracemem.nodes.birth.person_judge import enforce

    def node(nid, *names):
        return Node(id=nid, kind=NodeKind.ENTITY, last_active_t_ms=1,
                    attributes=[Attribute("seen_as", n, Grade.VERBATIM,
                                          Confidence(0.9), ("e1",), 1)
                                for n in names])

    g = MemoryGraphStore()
    g.upsert_node(node("all-bad", "Springfield"))
    g.upsert_node(node("mixed", "Hana", "Trash"))
    g.upsert_node(node("untouched", "Tamsin"))
    for bad in ("springfield", "trash"):
        g.set_meta(VERDICT_KEY % ("person:%s" % bad), "no")
    g.set_meta(VERDICT_KEY % "person:hana", "yes")

    out = enforce(g)
    assert out["unbirthed"] == ["Springfield"]
    assert out["aliases_stripped"] == ["Trash"]
    assert g.get_node("all-bad") is None
    mixed = g.get_node("mixed")
    # DEMOTED, NOT DELETED. This used to assert the false alias was GONE. It
    # was the only path in the system that erased evidence with no trail —
    # and what it erased was a judgement about a PERSON'S NAME, so if the
    # judgement was wrong the founder had no way to see it had ever been
    # made. "Why did it stop calling her that?" had no answer, and neither
    # did "what else has it quietly decided?".
    #
    # `label_of` picks the highest-graded value, so a retracted alias can
    # never stand for the node again; it is still there to be read and
    # argued with, which is the difference between a memory that revised
    # itself and one that rewrote itself.
    from tracemem.domain.grading import retracted
    from tracemem.nodes.accrual import label_of

    by_value = {a.value: a for a in mixed.attributes}
    assert sorted(by_value) == ["Hana", "Trash"]
    assert by_value["Trash"].grade == retracted()
    assert by_value["Hana"].grade != retracted()
    assert label_of(mixed) == "Hana", \
        "a retracted alias came back as the node's label"
    assert g.get_node("untouched") is not None


def test_the_judge_spends_through_the_budget_or_not_at_all():
    """A budget with a way around it is not a budget — the heart's own test
    caught this pass spending outside the gate."""
    from tracemem.nodes.birth.person_judge import JUDGE_TOKENS

    class Broke:
        def may_spend(self, n): return False
        def spend(self, n): raise AssertionError("spent while broke")

    g = MemoryGraphStore()
    j = Judge(people=["Tamsin"])
    out = adjudicate(g, [("person:tamsin", "Tamsin")], j,
                     samples_for=lambda k: [], budget=Broke())
    assert out["asked"] == 0 and out["out_of_budget"] is True
    assert verdict_for(g, "person:tamsin") is None

    class Rich:
        def __init__(self): self.spent = 0
        def may_spend(self, n): return True
        def spend(self, n): self.spent += n

    rich = Rich()
    adjudicate(g, [("person:tamsin", "Tamsin")], j, samples_for=lambda k: [],
               budget=rich)
    assert rich.spent == JUDGE_TOKENS


def test_the_judge_stops_between_surfaces_when_asked():
    """WITNESSED 2026-10-07 on the founder's live daemon: the night's slice
    expired, yet the judge kept asking the model surface after surface on the
    one thread that answers questions — every ask timed out at 120s while the
    screen was locked. A slice is abandonable only at poll points; the judge
    had none."""
    g = MemoryGraphStore()
    j = Judge(people=["Tamsin"])
    pending = [("person:%d" % i, "Name%d" % i) for i in range(10)]
    calls = {"n": 0}

    def stop():
        calls["n"] += 1
        return calls["n"] > 2          # two surfaces, then the slice is spent

    out = adjudicate(g, pending, j, samples_for=lambda k: [], stop=stop)
    assert len(j.asked) == 2
    assert out["stopped"] is True


def test_failed_calls_count_against_the_limit():
    """A failing model left the question open (correct) but did not count as
    an attempt, so one outage walked the WHOLE waiting room — 1,291 pending
    mentions on the founder's graph — paying a model timeout for each, inside
    one night slice."""
    g = MemoryGraphStore()
    pending = [("person:%d" % i, "Name%d" % i) for i in range(500)]
    seen = []

    class Down:
        def complete(self, prompt):
            seen.append(prompt)
            raise RuntimeError("ollama is not running")

    out = adjudicate(g, pending, Down(), samples_for=lambda k: [], limit=40)
    assert len(seen) <= 40
    assert out["asked"] == 0
    assert all(verdict_for(g, k) is None for k, _ in pending)
