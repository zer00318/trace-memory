"""THE IDENTITY GAUNTLET — birth v2's crown (spec 04 birth, resolver laws 1-4).

Every test here is a corruption class that a naive birth layer commits
silently. If these pass, the graph's foundation can be trusted enough to
dream on.
"""
from __future__ import annotations

from tests.helpers import make_claim, judged_people
from tracemem.domain import Episode, Grade, Modality, NodeKind, TimeRange
from tracemem.nodes.accrual import (
    accrue, agenda, current_value, names_of, sightings,
)
from tracemem.nodes.birth.entity_extractor import extract_mentions
from tracemem.nodes.graph_store import MemoryGraphStore
from tracemem.nodes.marination.background import upkeep

MS_DAY = 86_400_000


def ep(i, place="wifi:home", t0=0):
    return Episode(id="ep-%d" % i, t_range=TimeRange(t0, t0 + 3_600_000),
                   place_key=place, claim_ids=())


def office_claims(i, text, t_ms, app="VS Code"):
    return [make_claim(text=text, t_ms=t_ms, place="wifi:office",
                       frame="f-%d" % t_ms, app=app)]


def run_episode(g, episode, claims):
    e = episode.with_claims([c.id for c in claims])
    return upkeep(e, claims, g)


def persons(g):
    return [n for n in g.nodes.values() if n.kind == NodeKind.ENTITY]


# ---- law 1: person nodes are instances, not names ----

def test_same_person_same_context_resolves_to_one_node():
    g = MemoryGraphStore()
    judged_people(g, "Sam", "Priya", "Hana", "Nina", "Mira", "Alex",
                  "Sammy", "Maya")
    for day in range(3):
        e = ep(day, place="wifi:office", t0=day * MS_DAY)
        run_episode(g, e, office_claims(day, "pairing with Sam on the parser",
                                        day * MS_DAY + 1000))
    sams = [n for n in persons(g) if "Sam" in names_of(n)]
    assert len(sams) == 1
    assert sightings(sams[0]) == 3            # evidence accrued, not duplicated


def test_two_sams_in_disjoint_contexts_stay_two_nodes():
    g = MemoryGraphStore()
    judged_people(g, "Sam", "Priya", "Hana", "Nina", "Mira", "Alex",
                  "Sammy", "Maya")
    # work-Sam: office + VS Code, three days running
    for day in range(3):
        e = ep(day, place="wifi:office", t0=day * MS_DAY)
        run_episode(g, e, office_claims(day, "review with Sam", day * MS_DAY + 1000))
    # gym-Sam: totally different world
    for day in range(3):
        e = ep(100 + day, place="wifi:gym", t0=day * MS_DAY + 7_200_000)
        run_episode(g, e, [make_claim(text="spotting with Sam at the rack",
                                      t_ms=day * MS_DAY + 7_201_000,
                                      place="wifi:gym", frame="g-%d" % day,
                                      app="Fitness")])
    sams = [n for n in persons(g) if "Sam" in names_of(n)]
    assert len(sams) == 2, "disjoint-context Sams must never merge (law 1)"


# ---- law 2: anonymous people are per-context ghosts, not one global bucket ----

def test_recurring_ghost_in_same_context_is_one_node():
    g = MemoryGraphStore()
    judged_people(g, "Sam", "Priya", "Hana", "Nina", "Mira", "Alex",
                  "Sammy", "Maya")
    for day in range(3):
        e = ep(day, place="wifi:home", t0=day * MS_DAY)
        run_episode(g, e, [make_claim(text="a person is at the kitchen table",
                                      t_ms=day * MS_DAY + 500, place="wifi:home",
                                      frame="k-%d" % day, app=None,
                                      modality=Modality.CAMERA, grade=Grade.GIST,
                                      perceiver="gist")])
    ghosts = [n for n in persons(g) if not names_of(n)]
    assert len(ghosts) == 1
    assert sightings(ghosts[0]) == 3


def test_ghosts_in_unrelated_contexts_stay_separate():
    g = MemoryGraphStore()
    judged_people(g, "Sam", "Priya", "Hana", "Nina", "Mira", "Alex",
                  "Sammy", "Maya")
    run_episode(g, ep(1, place="wifi:home"),
                [make_claim(text="a person at the table", t_ms=1000,
                            place="wifi:home", frame="h1", grade=Grade.GIST,
                            perceiver="gist", modality=Modality.CAMERA)])
    run_episode(g, ep(2, place="wifi:cafe-far"),
                [make_claim(text="a person reading nearby", t_ms=2_000_000,
                            place="wifi:cafe-far", frame="c1", grade=Grade.GIST,
                            perceiver="gist", modality=Modality.CAMERA)])
    ghosts = [n for n in persons(g) if not names_of(n)]
    assert len(ghosts) == 2, "strangers in unrelated places must not blend (law 2)"


# ---- law 3: uncertainty asks, it never merges ----

def test_recurring_ghost_raises_a_who_is_this_question():
    g = MemoryGraphStore()
    judged_people(g, "Sam", "Priya", "Hana", "Nina", "Mira", "Alex",
                  "Sammy", "Maya")
    all_q = []
    for day in range(3):
        e = ep(day, place="wifi:home", t0=day * MS_DAY)
        all_q += run_episode(g, e, [make_claim(
            text="a person is at the kitchen table", t_ms=day * MS_DAY + 500,
            place="wifi:home", frame="k-%d" % day, grade=Grade.GIST,
            # A KITCHEN TABLE IS NOT A SCREEN. Since 2026-08-24 "a person" is
            # only a person when a sense with a body saw one — on screen the
            # same words are a notification ("someone commented on your
            # post"). This fixture always meant a room.
            perceiver="gist", modality=Modality.CAMERA)])
    assert all_q, "a recurring unknown person must generate a question"
    assert any("who" in q.text.lower() for q in all_q)
    ghost = [n for n in persons(g) if not names_of(n)][0]
    assert "who is this?" in agenda(ghost)


def test_ambiguous_named_mention_names_both_candidates():
    g = MemoryGraphStore()
    judged_people(g, "Sam", "Priya", "Hana", "Nina", "Mira", "Alex",
                  "Sammy", "Maya")
    # two established Sams in different worlds
    for day in range(3):
        run_episode(g, ep(day, place="wifi:office", t0=day * MS_DAY),
                    office_claims(day, "standup with Sam", day * MS_DAY + 1000))
        run_episode(g, ep(50 + day, place="wifi:gym", t0=day * MS_DAY + 7_200_000),
                    [make_claim(text="lifting with Sam", t_ms=day * MS_DAY + 7_201_000,
                                place="wifi:gym", frame="g%d" % day, app="Fitness")])
    # now: "Sam" with NO disambiguating context (new cafe, new app)
    qs = run_episode(g, ep(99, place="wifi:cafe-new", t0=9 * MS_DAY),
                     [make_claim(text="coffee with Sam", t_ms=9 * MS_DAY + 100,
                                 place="wifi:cafe-new", frame="cn", app="Messages")])
    assert len([n for n in persons(g) if "Sam" in names_of(n)]) == 2  # no 3rd Sam, no merge
    assert qs and any(len(q.about_node_ids) == 2 for q in qs), \
        "the ambiguity question must name both Sams so an answer settles the fork"


# ---- delta idempotence: the hub may replay; the graph may not inflate ----

def test_upkeep_replay_adds_nothing():
    g = MemoryGraphStore()
    judged_people(g, "Sam", "Priya", "Hana", "Nina", "Mira", "Alex",
                  "Sammy", "Maya")
    e = ep(1, place="wifi:office")
    claims = office_claims(1, "pairing with Maya on tracemem", 1000)
    run_episode(g, e, claims)
    nodes_before = {nid: len(n.attributes) for nid, n in g.nodes.items()}
    edges_before = {k: e.weight for k, e in g.edges.items()}
    run_episode(g, e, claims)                       # exact replay
    run_episode(g, e, claims)                       # and again
    assert {nid: len(n.attributes) for nid, n in g.nodes.items()} == nodes_before
    assert {k: e.weight for k, e in g.edges.items()} == edges_before


def test_episode_growth_processes_only_the_delta():
    """Two episodes to get Maya through the corroboration gate (a name seen
    once is deliberately not durable — see CORROBORATING_EPISODES), and then
    the property this test has always been about: a GROWN episode replays its
    head without counting it twice."""
    g = MemoryGraphStore()
    judged_people(g, "Sam", "Priya", "Hana", "Nina", "Mira", "Alex",
                  "Sammy", "Maya")
    e0 = ep(0, place="wifi:office")
    run_episode(g, e0, office_claims(0, "with Maya in the editor", 500))
    e = ep(1, place="wifi:office")
    first = office_claims(1, "with Maya in the editor", 1000)
    run_episode(g, e, first)
    maya = [n for n in persons(g) if "Maya" in names_of(n)][0]
    s1 = sightings(maya)
    assert s1 == 2, "promotion must cite the sighting that waited, not just the one that freed it"
    late = first + office_claims(1, "Maya: ship it", 2000)
    run_episode(g, e, late)                         # grown episode, replayed head
    assert sightings(maya) == s1                    # same episode: no double count
    assert "seen_as" in [a.key for a in maya.attributes]


# ---- accrual: history survives disagreement; better witness wins the view ----

def test_accrual_keeps_history_and_grade_decides_current():
    from tests.helpers import make_claim as mk
    from tracemem.domain import Confidence, Node
    n = Node(id="x", kind=NodeKind.OBJECT)
    accrue(n, "color", "blue", Grade.GIST, Confidence(0.6), "e1", 1000)
    accrue(n, "color", "red", Grade.VERBATIM, Confidence(0.9), "e2", 500)
    assert set(a.value for a in n.attributes if a.key == "color") == {"blue", "red"}
    assert current_value(n, "color") == "red"       # verbatim beats gist despite age


# ---- extraction grammars earn their keep ----

def test_extractor_finds_speakers_signoffs_and_stoplists_days():
    e = ep(1)
    claims = [
        make_claim(text="Maya: can you review by Tuesday?", t_ms=1000, frame="a"),
        make_claim(text="sounds good — Sam", t_ms=2000, frame="b"),
        make_claim(text="meeting moved to Monday", t_ms=3000, frame="c"),
    ]
    e = e.with_claims([c.id for c in claims])
    surfaces = {m.surface for m in extract_mentions(e, claims) if m.kind == "person"}
    assert "Maya" in surfaces and "Sam" in surfaces
    assert "Tuesday" not in surfaces and "Monday" not in surfaces


def test_physical_grammars_do_not_fire_on_a_screen():
    """MEASURED on the founder's graph 2026-08-24 — every object and topic
    node born from OCR, in full:

        object  black box · black mar · blue and · green clay ·
                green curtai · red and · white cropped ...   (25 of 25)
        topic   "their accessibility contexts and m" ...      (3 of 4)

    `_OBJECT_RE` reads "a red mug" and `_READING_RE` reads "reading the
    Economist": physical-world grammars, fed browser OCR, which DOCTRINE §3
    predicted on 2026-07-29. They surfaced as COMPANY in dossiers — "Hana
    was seen alongside green clay" — the memory reporting a stranger's shirt
    as her associate.

    Not deleted: unfed. A camera claim may still mint an object, so the
    grammar turns itself back on when the phone pillar lands.
    """
    from tests.helpers import make_claim
    from tracemem.domain import Modality
    from tracemem.domain.episode import Episode
    from tracemem.domain.time import TimeRange
    from tracemem.nodes.birth.entity_extractor import extract_mentions

    def mentions(modality):
        c = make_claim(text="a red mug beside reading the Economist daily",
                       t_ms=1000, modality=modality)
        ep = Episode(id="e1", t_range=TimeRange(1000, 1001),
                     place_key="system", claim_ids=(c.id,))
        return {m.kind for m in extract_mentions(ep, [c])}

    assert "object" not in mentions(Modality.SCREEN)
    assert "topic" not in mentions(Modality.SCREEN)

    seen = mentions(Modality.CAMERA)
    assert "object" in seen and "topic" in seen


def test_the_word_someone_on_a_screen_is_not_a_person_in_the_room():
    """The third instance of one root cause, after objects and topics.

    `_ANON_PERSON_RE` is right for a camera — a VLM describing a frame says
    "a person in a blue shirt", and that is a human who was really there.
    Applied to screen text it matches the WORD: "someone commented on your
    post" is a notification, not company. 43 anonymous "people" on the
    founder's graph were 43 occurrences of those words on screen, each then
    feeding the co-occurrence graph as though a stranger had been present.
    """
    from tests.helpers import make_claim
    from tracemem.domain import Modality
    from tracemem.domain.episode import Episode
    from tracemem.domain.time import TimeRange
    from tracemem.nodes.birth.entity_extractor import extract_mentions

    def anon(modality):
        c = make_claim(text="someone commented on your post", t_ms=1000,
                       modality=modality)
        ep = Episode(id="e1", t_range=TimeRange(1000, 1001),
                     place_key="system", claim_ids=(c.id,))
        return [m for m in extract_mentions(ep, [c]) if m.anonymous]

    assert anon(Modality.SCREEN) == []
    assert anon(Modality.CAMERA), "a camera seeing a person must still count"
