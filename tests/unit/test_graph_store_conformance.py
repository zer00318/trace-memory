"""ORACLE for Real store / WO-080 (05_nodes).

PROOF: "Full suite green on both backends; migration round-trip test green."

This file is the first half: every assertion runs against BOTH stores from
one parametrized fixture, so "the swap cannot change behavior" is checked
rather than asserted. The second half — the whole existing suite against
SQLite — is driven by conftest.py's TRACE_GRAPH_STORE switch and its result
is pasted on the board.

The test that matters most here is
`test_the_prefilter_never_hides_a_qualifying_node`. The SQLite
implementation pre-filters on stored activation before applying the decay
curve, which is sound only because decay never increases a value. If that
reasoning were wrong, nodes would silently vanish from recall — no error,
no crash, just a memory that quietly forgets things it holds. That is the
exact failure shape this codebase treats as most dangerous, so it gets a
test that tries to produce it on purpose.
"""
import pytest

from tracemem.domain import Edge, EdgeType, Node
from tracemem.domain.confidence import Confidence
from tracemem.domain.grade import Grade
from tracemem.domain.activation import HALF_LIFE_MS, activation_now
from tracemem.domain.node import Attribute, NodeKind
from tracemem.nodes.graph_store import MemoryGraphStore
from tracemem.nodes.sqlite_graph_store import SqliteGraphStore, migrate

DAY = 24 * 3600 * 1000


@pytest.fixture(params=["memory", "sqlite"])
def store(request):
    return MemoryGraphStore() if request.param == "memory" else SqliteGraphStore()


def entity(node_id, activation=0.0, last=0, depth=0, attrs=()):
    return Node(id=node_id, kind=NodeKind.ENTITY, activation=activation,
                last_active_t_ms=last, derivation_depth=depth,
                attributes=list(attrs))


def pattern(node_id, parents, activation=0.0, last=0):
    return Node(id=node_id, kind=NodeKind.PATTERN, activation=activation,
                last_active_t_ms=last, derivation_depth=1,
                parent_ids=tuple(parents))


def attr(key="name", value="Ada"):
    return Attribute(key=key, value=value, grade=Grade.VERBATIM,
                     confidence=Confidence(0.9),
                     source_episode_ids=("ep-1",), t_ms=1000)


# -- nodes -----------------------------------------------------------------
def test_a_missing_node_is_none_not_an_error(store):
    assert store.get_node("nobody") is None


def test_round_trip_preserves_every_field(store):
    node = entity("n1", activation=0.42, last=5000, attrs=[attr()])
    node.salience_ema = 0.7
    store.upsert_node(node)

    got = store.get_node("n1")
    assert got.id == "n1"
    assert got.kind is NodeKind.ENTITY
    assert got.activation == pytest.approx(0.42)
    assert got.last_active_t_ms == 5000
    assert got.salience_ema == pytest.approx(0.7)
    assert got.derivation_depth == 0


def test_attributes_survive_with_their_epistemics(store):
    """Provenance is per-attribute (N2). A store that dropped grade or the
    source episode ids would turn testimony into an unsourced assertion."""
    store.upsert_node(entity("n1", attrs=[attr("name", "Ada"),
                                          attr("role", "engineer")]))
    got = store.get_node("n1")
    assert len(got.attributes) == 2
    a = got.attributes[0]
    assert a.key == "name" and a.value == "Ada"
    assert a.grade is Grade.VERBATIM
    assert a.confidence.value == pytest.approx(0.9)
    assert a.source_episode_ids == ("ep-1",)
    assert a.t_ms == 1000


def test_parent_ids_survive(store):
    store.upsert_node(entity("p1"))
    store.upsert_node(pattern("pat1", ["p1"]))
    assert store.get_node("pat1").parent_ids == ("p1",)


def test_builder_version_survives_the_round_trip(store):
    """A derived node's vintage (SCHEMA §3.4) must persist, or replay could
    never reproduce a specific past version's output."""
    store.upsert_node(entity("e"))            # entity: empty vintage
    p = pattern("pat", ["e"])
    p.builder_version = "9.9.9"
    store.upsert_node(p)
    assert store.get_node("e").builder_version == ""
    assert store.get_node("pat").builder_version == "9.9.9"


def test_upsert_replaces_rather_than_duplicates(store):
    store.upsert_node(entity("n1", activation=0.1))
    store.upsert_node(entity("n1", activation=0.9))
    assert store.get_node("n1").activation == pytest.approx(0.9)


# -- delete_meta -----------------------------------------------------------
def test_deleting_a_depth_zero_node_is_refused(store):
    """The entity tier is raw-adjacent and not prunable here. Both stores
    must refuse identically, or pruning behaves differently in production
    than in every test."""
    store.upsert_node(entity("n1"))
    with pytest.raises(ValueError):
        store.delete_meta("n1")
    assert store.get_node("n1") is not None


def test_deleting_a_meta_node_removes_its_edges_too(store):
    store.upsert_node(entity("a"))
    store.upsert_node(pattern("m", ["a"]))
    store.upsert_edge(Edge(src="a", dst="m", type=EdgeType.DERIVED_FROM))

    store.delete_meta("m")
    assert store.get_node("m") is None
    assert store.edges_of("a") == []


def test_deleting_an_absent_node_is_silent(store):
    store.delete_meta("ghost")


# -- retire_meta (the moat: forget by tombstoning, not vaporizing) ----------
def test_retiring_a_meta_node_removes_it_from_live_but_keeps_the_belief(store):
    store.upsert_node(entity("a"))
    store.upsert_node(pattern("m", ["a"], activation=0.4, last=1000))
    store.upsert_edge(Edge(src="a", dst="m", type=EdgeType.DERIVED_FROM))

    store.retire_meta("m", t_ms=9999)
    # gone from the live graph, exactly like delete
    assert store.get_node("m") is None
    assert store.edges_of("a") == []
    # but the belief survives in the graveyard, with when it died
    graveyard = {n.id: (n, t) for n, t in store.retired()}
    assert "m" in graveyard
    retired_node, retired_t = graveyard["m"]
    assert retired_node.kind is NodeKind.PATTERN
    assert retired_node.parent_ids == ("a",)
    assert retired_t == 9999


def test_retiring_a_depth_zero_node_is_refused(store):
    store.upsert_node(entity("n1"))
    with pytest.raises(ValueError):
        store.retire_meta("n1", t_ms=1)
    assert store.get_node("n1") is not None


def test_retiring_an_absent_node_is_silent(store):
    store.retire_meta("ghost", t_ms=1)
    assert store.retired() == []


# -- merge_into (deliberate identity merge) --------------------------------
def test_merge_into_rewires_edges_and_tombstones_the_absorbed(store):
    store.upsert_node(entity("keep"))
    store.upsert_node(entity("absorb"))
    store.upsert_node(entity("friend"))
    store.upsert_edge(Edge(src="absorb", dst="friend", type=EdgeType.CO_OCCURS,
                           weight=2.0))

    store.merge_into("keep", "absorb", t_ms=5)
    assert store.get_node("absorb") is None                 # gone from live
    # absorb's edge to friend is now keep's edge to friend
    assert store.edge_between("keep", "friend", EdgeType.CO_OCCURS) is not None
    assert store.edges_of("absorb") == []
    # audited: the absorbed node is in the graveyard, not vaporized
    assert "absorb" in {n.id for n, _t in store.retired()}


def test_merge_into_drops_edges_that_would_become_self_loops(store):
    store.upsert_node(entity("keep"))
    store.upsert_node(entity("absorb"))
    store.upsert_edge(Edge(src="keep", dst="absorb", type=EdgeType.SAME_AS,
                           weight=0.9))
    store.merge_into("keep", "absorb", t_ms=5)
    assert store.edge_between("keep", "keep") is None       # no self-edge minted


# -- edges -----------------------------------------------------------------
def test_edge_round_trip(store):
    store.upsert_edge(Edge(src="a", dst="b", type=EdgeType.CO_OCCURS,
                           weight=0.75, evidence_count=4,
                           last_reinforced_t_ms=9))
    e = store.edge_between("a", "b")
    assert e.src == "a" and e.dst == "b"
    assert e.type is EdgeType.CO_OCCURS
    assert e.weight == pytest.approx(0.75)
    assert e.evidence_count == 4
    assert e.last_reinforced_t_ms == 9


def test_edge_between_is_symmetric(store):
    store.upsert_edge(Edge(src="a", dst="b", type=EdgeType.CO_OCCURS))
    assert store.edge_between("b", "a") is not None


def test_edge_between_can_filter_by_type(store):
    store.upsert_edge(Edge(src="a", dst="b", type=EdgeType.CO_OCCURS))
    assert store.edge_between("a", "b", EdgeType.CO_OCCURS) is not None
    assert store.edge_between("a", "b", EdgeType.CONTRADICTS) is None


def test_edges_of_finds_both_directions(store):
    store.upsert_edge(Edge(src="a", dst="b", type=EdgeType.CO_OCCURS))
    store.upsert_edge(Edge(src="c", dst="a", type=EdgeType.PRECEDES))
    assert len(store.edges_of("a")) == 2


def test_upserting_the_same_triple_replaces_it(store):
    store.upsert_edge(Edge(src="a", dst="b", type=EdgeType.CO_OCCURS, weight=0.1))
    store.upsert_edge(Edge(src="a", dst="b", type=EdgeType.CO_OCCURS, weight=0.9))
    assert len(store.edges_of("a")) == 1
    assert store.edge_between("a", "b").weight == pytest.approx(0.9)


def test_neighbors_one_hop_and_two(store):
    for nid in ("a", "b", "c"):
        store.upsert_node(entity(nid))
    store.upsert_edge(Edge(src="a", dst="b", type=EdgeType.CO_OCCURS))
    store.upsert_edge(Edge(src="b", dst="c", type=EdgeType.CO_OCCURS))

    assert {n.id for n in store.neighbors("a", hops=1)} == {"b"}
    assert {n.id for n in store.neighbors("a", hops=2)} == {"b", "c"}


def test_neighbors_never_includes_the_node_itself(store):
    store.upsert_node(entity("a"))
    store.upsert_node(entity("b"))
    store.upsert_edge(Edge(src="a", dst="b", type=EdgeType.CO_OCCURS))
    assert all(n.id != "a" for n in store.neighbors("a", hops=3))


def test_an_edge_to_an_unknown_node_does_not_invent_one(store):
    """Edges can outlive their endpoints mid-write. Neighbors must skip the
    missing rather than fabricate a Node with no attributes."""
    store.upsert_node(entity("a"))
    store.upsert_edge(Edge(src="a", dst="vanished", type=EdgeType.CO_OCCURS))
    assert store.neighbors("a") == []


# -- activation ------------------------------------------------------------
def test_activation_floor_uses_decayed_not_stored_value(store):
    """A node last active a fortnight ago at 0.8 is not still at 0.8."""
    store.upsert_node(entity("old", activation=0.8, last=0))
    store.upsert_node(entity("fresh", activation=0.8, last=14 * DAY))

    got = {n.id for n in store.nodes_with_activation_ge(0.5, t_ms=14 * DAY)}
    assert got == {"fresh"}


def test_the_prefilter_never_hides_a_qualifying_node(store):
    """THE dangerous one. SQLite pre-filters on STORED activation before
    applying the decay curve; that is sound only because decay never
    increases a value. If it were unsound, nodes would silently disappear
    from recall — no error, just a memory that quietly forgets.

    Swept across the boundary at many ages, comparing against the domain
    function directly rather than against the other store, so a shared
    mistake could not hide.
    """
    floor = 0.5
    now = 30 * DAY
    expected = set()
    for i, stored in enumerate([0.05, 0.49, 0.5, 0.51, 0.9, 1.0]):
        for j, age_days in enumerate([0, 1, 7, 29]):
            nid = "n-%d-%d" % (i, j)
            last = now - age_days * DAY
            store.upsert_node(entity(nid, activation=stored, last=last))
            if activation_now(stored, last, now, HALF_LIFE_MS) >= floor:
                expected.add(nid)

    got = {n.id for n in store.nodes_with_activation_ge(floor, now)}
    assert got == expected
    assert expected, "the sweep must actually include qualifying nodes"


def test_a_floor_of_zero_returns_everything_stored(store):
    for i in range(5):
        store.upsert_node(entity("n%d" % i, activation=0.0, last=0))
    assert len(store.nodes_with_activation_ge(0.0, t_ms=99 * DAY)) == 5


def test_reading_before_the_last_active_time_does_not_amplify(store):
    """activation_now returns the stored value when asked about the past; it
    must never report MORE than was stored."""
    store.upsert_node(entity("n", activation=0.6, last=10 * DAY))
    got = store.nodes_with_activation_ge(0.6, t_ms=1 * DAY)
    assert [n.id for n in got] == ["n"]
    assert got[0].activation == pytest.approx(0.6)


# -- ledger ----------------------------------------------------------------
def test_ledger_is_not_fresh_until_recorded(store):
    assert store.ledger_fresh("a|b", "fp") is False


def test_ledger_fresh_requires_the_same_fingerprint(store):
    """I4 idempotence: same pair, NEW evidence, must not be treated as
    already done — otherwise a combination never re-runs when the facts
    beneath it change."""
    store.record_ledger("a|b", "merged", "fp-1")
    assert store.ledger_fresh("a|b", "fp-1") is True
    assert store.ledger_fresh("a|b", "fp-2") is False


def test_recording_twice_overwrites(store):
    store.record_ledger("a|b", "merged", "fp-1")
    store.record_ledger("a|b", "rejected", "fp-2")
    assert store.ledger_fresh("a|b", "fp-2") is True


# -- durability + migration (sqlite only) ---------------------------------
def test_a_sqlite_store_survives_reopen(tmp_path):
    path = str(tmp_path / "graph.sqlite3")
    first = SqliteGraphStore(path)
    first.upsert_node(entity("n1", activation=0.5, last=7, attrs=[attr()]))
    first.upsert_edge(Edge(src="n1", dst="n2", type=EdgeType.CO_OCCURS))
    first.record_ledger("n1|n2", "merged", "fp")
    first.close()

    second = SqliteGraphStore(path)
    assert second.get_node("n1").attributes[0].value == "Ada"
    assert second.edge_between("n1", "n2") is not None
    assert second.ledger_fresh("n1|n2", "fp") is True


def test_the_store_gets_the_products_one_durability_policy(tmp_path):
    """A second store quietly choosing different pragmas is how two halves of
    one machine end up with two different crash stories."""
    from tracemem.text import durability
    store = SqliteGraphStore(str(tmp_path / "g.sqlite3"))
    got = durability.describe(store._db)
    assert got["journal_mode"] == "WAL"
    assert got["synchronous"] == 1


def test_migration_round_trip_moves_everything(tmp_path):
    """THE ORACLE's second clause."""
    mem = MemoryGraphStore()
    mem.upsert_node(entity("a", activation=0.3, last=100, attrs=[attr()]))
    mem.upsert_node(entity("b"))
    mem.upsert_node(pattern("m", ["a"], activation=0.2, last=50))
    mem.upsert_edge(Edge(src="a", dst="b", type=EdgeType.CO_OCCURS, weight=0.4))
    mem.upsert_edge(Edge(src="a", dst="m", type=EdgeType.DERIVED_FROM))
    mem.record_ledger("a|b", "merged", "fp-1")

    target = SqliteGraphStore(str(tmp_path / "migrated.sqlite3"))
    counts = migrate(mem, target)
    assert counts == {"nodes": 3, "edges": 2, "ledger": 1, "tombstones": 0}

    for node_id in ("a", "b", "m"):
        before, after = mem.get_node(node_id), target.get_node(node_id)
        assert after.kind is before.kind
        assert after.activation == pytest.approx(before.activation)
        assert after.parent_ids == before.parent_ids
        assert len(after.attributes) == len(before.attributes)
    assert target.edge_between("a", "b").weight == pytest.approx(0.4)
    assert target.ledger_fresh("a|b", "fp-1") is True


def test_migration_of_an_empty_store_is_not_an_error(tmp_path):
    counts = migrate(MemoryGraphStore(),
                     SqliteGraphStore(str(tmp_path / "empty.sqlite3")))
    assert counts == {"nodes": 0, "edges": 0, "ledger": 0, "tombstones": 0}


def test_migration_carries_the_tombstone_graveyard(tmp_path):
    """The moat (retired beliefs) must survive a backend swap, or exporting
    a store would silently drop your history."""
    mem = MemoryGraphStore()
    mem.upsert_node(entity("a"))
    mem.upsert_node(pattern("m", ["a"], activation=0.4, last=10))
    mem.retire_meta("m", t_ms=777)

    target = SqliteGraphStore(str(tmp_path / "m.sqlite3"))
    counts = migrate(mem, target)
    assert counts["tombstones"] == 1
    graveyard = {n.id: t for n, t in target.retired()}
    assert graveyard == {"m": 777}


# -- the compatibility shim, and its loud edge ----------------------------
def test_the_dict_shim_exposes_what_production_code_actually_uses():
    """Eight production modules iterate graph_store.nodes.values() directly,
    so the real protocol is wider than the method list. The swap has to
    honour the dict, not just the methods."""
    store = SqliteGraphStore()
    store.upsert_node(entity("a"))
    store.upsert_node(entity("b"))
    store.upsert_edge(Edge(src="a", dst="b", type=EdgeType.CO_OCCURS))
    store.record_ledger("a|b", "merged", "fp")

    assert {n.id for n in store.nodes.values()} == {"a", "b"}
    assert len(store.edges) == 1
    assert store.ledger["a|b"] == ("merged", "fp")


def test_writing_through_the_shim_raises_rather_than_vanishing():
    """Each property builds a fresh dict, so a write would mutate a
    temporary and be lost with no error at all — a night's work silently
    discarded. The proxy turns that into an exception the first time
    anybody tries it."""
    store = SqliteGraphStore()
    store.upsert_node(entity("a"))
    with pytest.raises(TypeError):
        store.nodes["b"] = entity("b")
    with pytest.raises(TypeError):
        store.ledger["k"] = ("x", "y")


def test_iter_nodes_streams_without_materialising_the_graph():
    """The shape the eight sweep sites should move to."""
    store = SqliteGraphStore()
    for i in range(10):
        store.upsert_node(entity("n%d" % i))
    ids = [n.id for n in store.iter_nodes()]
    assert len(ids) == 10 and ids == sorted(ids)


# -- the corroboration waiting room ----------------------------------------
#
# Both backends, because the durability is the whole point: an ephemeral
# waiting room forgets every held sighting on restart, so nothing could ever
# reach a second episode and the person tier would quietly empty. A silent
# emptying is the worst failure shape this codebase recognises.

def test_an_unseen_surface_has_no_sightings(store):
    assert store.sightings("person:hana") == []


def test_a_sighting_round_trips_with_the_fields_promotion_needs(store):
    store.record_sighting("person:hana", "ep-1", {
        "episode_id": "ep-1", "claim_id": "c-1", "t_ms": 1000,
        "grade": 4, "confidence": 0.9, "surface": "Hana", "kind": "person"})

    got = store.sightings("person:hana")
    assert len(got) == 1
    s = got[0]
    # Retroactive accrual needs every one of these; a backend that drops any
    # of them promotes a node that cannot cite the sighting that waited.
    assert s["episode_id"] == "ep-1" and s["claim_id"] == "c-1"
    assert s["t_ms"] == 1000 and s["grade"] == 4
    assert s["confidence"] == 0.9 and s["surface"] == "Hana"


def test_re_recording_the_same_episode_does_not_add_a_second_sighting(store):
    """Corroboration a replay can manufacture is not corroboration. upkeep is
    delta-driven and re-runs over grown episodes, so this idempotence is what
    stops every one-off name from promoting itself on restart."""
    for _ in range(4):
        store.record_sighting("person:northside", "ep-1", {
            "episode_id": "ep-1", "claim_id": "c-1", "t_ms": 1000,
            "grade": 4, "confidence": 1.0, "surface": "Northside",
            "kind": "person"})

    assert len(store.sightings("person:northside")) == 1


def test_distinct_episodes_accumulate(store):
    for eid in ("ep-1", "ep-2", "ep-3"):
        store.record_sighting("person:hana", eid, {
            "episode_id": eid, "claim_id": "c-" + eid, "t_ms": 1000,
            "grade": 4, "confidence": 1.0, "surface": "Hana",
            "kind": "person"})

    assert {s["episode_id"] for s in store.sightings("person:hana")} == {
        "ep-1", "ep-2", "ep-3"}


def test_surfaces_do_not_share_a_waiting_room(store):
    store.record_sighting("person:hana", "ep-1", {
        "episode_id": "ep-1", "claim_id": "c-1", "t_ms": 1, "grade": 4,
        "confidence": 1.0, "surface": "Hana", "kind": "person"})
    store.record_sighting("person:thinking", "ep-2", {
        "episode_id": "ep-2", "claim_id": "c-2", "t_ms": 2, "grade": 4,
        "confidence": 1.0, "surface": "Thinking", "kind": "person"})

    assert len(store.sightings("person:hana")) == 1
    assert len(store.sightings("person:thinking")) == 1


def test_held_sightings_survive_a_restart():
    """The one that in-memory cannot answer. A sighting recorded before a
    reboot must still be waiting after it, or a name mentioned on Monday and
    again on Friday never corroborates across the restart in between."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "graph.sqlite3"
        first = SqliteGraphStore(str(path))
        first.record_sighting("person:hana", "ep-1", {
            "episode_id": "ep-1", "claim_id": "c-1", "t_ms": 1000,
            "grade": 4, "confidence": 0.9, "surface": "Hana",
            "kind": "person"})
        del first

        second = SqliteGraphStore(str(path))
        assert [s["episode_id"] for s in second.sightings("person:hana")] == ["ep-1"]
