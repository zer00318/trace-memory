"""ORACLE for P1 — the derived graph survives a restart, and replay is safe.

Two properties, and the second is the one that bites.

1. A graph store path makes the graph DURABLE. Without this, every belief the
   night derives dies at process exit and the founder can never grade a digest
   the next morning — which is the whole point of the digest.

2. REPLAY DOES NOT INFLATE THE GRAPH. `Brain.backfill` re-binds every claim in
   the store on every boot. That was harmless while the graph was ephemeral,
   because the graph it would have corrupted died with the process. On a
   durable graph it becomes a silent corruption: `marination.background._edge`
   calls `Edge.reinforce(t_ms, delta=0.5)`, which is NOT idempotent, so
   co-occurrence weight and evidence_count would compound on every restart —
   a graph that grows more confident the more often you reboot, with nothing
   anywhere reporting it.

   The delta ledger is what stands between those two facts, so it had to move
   off the process and onto the store. These tests are what prove it did.
"""
import pytest

from tracemem.app.wiring import build
from tracemem.domain import EdgeType
from tracemem.domain.episode import Episode
from tracemem.domain.time import TimeRange
from tracemem.nodes.graph_store import MemoryGraphStore
from tracemem.nodes.marination.background import upkeep
from tracemem.nodes.sqlite_graph_store import SqliteGraphStore
from tests.fakes import HashEmbedder, ScriptedReasoner
from tests.helpers import make_claim


def _episode(ep_id="ep-1", claims=()):
    return Episode(id=ep_id, t_range=TimeRange(0, 10_000),
                   place_key="wifi:aaaa",
                   claim_ids=tuple(c.id for c in claims))


# -- wiring ----------------------------------------------------------------

def test_no_path_still_builds_in_memory(tmp_path):
    """The wiring oracle: the whole graph must construct on a laptop with zero
    configuration. A path opts INTO durability; it is never required.

    Compared against `type(MemoryGraphStore())` rather than a literal class,
    because conftest's TRACE_GRAPH_STORE=sqlite switch rebinds that name to an
    in-memory SqliteGraphStore. Asserting the concrete class would make this
    test fail under the very switch that exists to prove the backends are
    interchangeable — the assertion here is "the DEFAULT path was taken",
    not "this exact class was used".
    """
    hub = build({"embedder": HashEmbedder(), "reasoner": ScriptedReasoner([])})
    assert type(hub.w.graph_store) is type(MemoryGraphStore())  # noqa: E721


def test_a_graph_path_makes_the_graph_durable(tmp_path):
    path = str(tmp_path / "graph.sqlite3")
    hub = build({"embedder": HashEmbedder(), "reasoner": ScriptedReasoner([]),
                 "graph_store_path": path})
    assert isinstance(hub.w.graph_store, SqliteGraphStore)


def test_an_explicit_store_still_wins_over_a_path(tmp_path):
    """Tests and callers that hand in their own store must keep getting it."""
    mine = MemoryGraphStore()
    hub = build({"embedder": HashEmbedder(), "reasoner": ScriptedReasoner([]),
                 "graph_store": mine,
                 "graph_store_path": str(tmp_path / "unused.sqlite3")})
    assert hub.w.graph_store is mine


def test_beliefs_survive_a_reopen(tmp_path):
    path = str(tmp_path / "graph.sqlite3")
    claims = [make_claim("with Priya at the kiln", t_ms=1000)]
    first = build({"embedder": HashEmbedder(), "reasoner": ScriptedReasoner([]),
                   "graph_store_path": path})
    upkeep(_episode(claims=claims), claims, first.w.graph_store)
    born = set(first.w.graph_store.nodes)
    assert born, "the fixture must actually birth something"
    first.w.graph_store.close()

    second = build({"embedder": HashEmbedder(), "reasoner": ScriptedReasoner([]),
                    "graph_store_path": path})
    assert set(second.w.graph_store.nodes) == born


# -- replay safety: THE dangerous one --------------------------------------

@pytest.mark.parametrize("store_factory", [
    pytest.param(lambda p: MemoryGraphStore(), id="memory"),
    pytest.param(lambda p: SqliteGraphStore(p), id="sqlite"),
])
def test_upkeep_replay_adds_no_edge_weight(tmp_path, store_factory):
    """Calling upkeep twice with the same claims must be a no-op the second
    time. Accrual is idempotent underneath; edge reinforcement is NOT, so the
    delta ledger is the only thing preventing compounding."""
    store = store_factory(str(tmp_path / "g.sqlite3"))
    claims = [make_claim("coffee with Priya", t_ms=1000),
              make_claim("Priya said the kiln moved", t_ms=2000)]
    ep = _episode(claims=claims)

    upkeep(ep, claims, store)
    before = {(e.src, e.dst, e.type): (e.weight, e.evidence_count)
              for e in store.edges.values()}
    assert before, "the fixture must actually wire something"

    upkeep(ep, claims, store)               # the replay
    after = {(e.src, e.dst, e.type): (e.weight, e.evidence_count)
             for e in store.edges.values()}
    assert after == before


def test_the_delta_ledger_survives_a_restart(tmp_path):
    """THE regression this whole change exists to prevent.

    Two SEPARATE store objects over one file — which is exactly what a daemon
    restart is. If the ledger lived in the process, the second boot would see
    every claim as fresh and reinforce every edge again.
    """
    path = str(tmp_path / "g.sqlite3")
    claims = [make_claim("coffee with Priya", t_ms=1000),
              make_claim("Priya said the kiln moved", t_ms=2000)]
    ep = _episode(claims=claims)

    first = SqliteGraphStore(path)
    upkeep(ep, claims, first)
    before = {(e.src, e.dst, e.type): (e.weight, e.evidence_count)
              for e in first.edges.values()}
    assert before
    first.close()

    second = SqliteGraphStore(path)         # the reboot
    assert second.upkeep_seen(ep.id) == {c.id for c in claims}
    upkeep(ep, claims, second)              # backfill re-binds everything
    after = {(e.src, e.dst, e.type): (e.weight, e.evidence_count)
             for e in second.edges.values()}
    assert after == before, "a restart inflated the graph"


def test_a_genuinely_new_claim_still_gets_processed_after_a_restart(tmp_path):
    """The ledger must suppress REPLAY, never real growth — otherwise the fix
    for inflation would quietly become a memory that stops learning."""
    path = str(tmp_path / "g.sqlite3")
    old = [make_claim("coffee with Priya", t_ms=1000)]
    ep = _episode(claims=old)

    first = SqliteGraphStore(path)
    upkeep(ep, old, first)
    first.close()

    second = SqliteGraphStore(path)
    fresh = make_claim("met Priya at the studio", t_ms=3000)
    grown = _episode(claims=old + [fresh])
    upkeep(grown, old + [fresh], second)
    assert fresh.id in second.upkeep_seen(grown.id)


def test_mining_fingerprints_survive_a_restart(tmp_path):
    """Same law for the re-marination ledger: unchanged raw is never
    re-chewed, and a restart must not reset that."""
    path = str(tmp_path / "g.sqlite3")
    first = SqliteGraphStore(path)
    first.set_mining_fingerprint("node-1", "seen_as", "fp-abc")
    first.close()

    second = SqliteGraphStore(path)
    assert second.mining_fingerprint("node-1", "seen_as") == "fp-abc"
    assert second.mining_fingerprint("node-1", "color") is None
