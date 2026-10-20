"""Black-box restart oracles for versioned late-boundary reconciliation."""
import pytest

from tests.fakes import ContextAnswerReasoner, FakeClock, HashEmbedder
from tests.helpers import make_claim
from tracemem.app.wiring import build
from tracemem.binder.reconcile import (
    BoundaryReconciliationError, reconcile_pending,
)
from tracemem.domain.episode import Episode
from tracemem.domain.time import TimeRange
from tracemem.nodes.sqlite_graph_store import SqliteGraphStore, migrate
from tracemem.text.claim_log import ClaimLog


def _hub(raw, graph):
    return build({"store_path": str(raw), "graph_store_path": str(graph),
                  "embedder": HashEmbedder(),
                  "reasoner": ContextAnswerReasoner(),
                  "clock": FakeClock(10_000), "verify_answers": False})


def _close(hub):
    hub.w.claim_log._db.close()
    hub.w.graph_store.close()


def test_late_boundary_rekeys_atomically_and_old_citations_resolve(tmp_path):
    raw_path, graph_path = tmp_path / "raw.db", tmp_path / "graph.db"
    first = _hub(raw_path, graph_path)
    first.post_scene_change(1_000)
    claims = [make_claim("before split", t_ms=500, frame="a"),
              make_claim("after split", t_ms=1_500, frame="b")]
    first.post_claims(claims)
    old_ids = {ep.id for ep in first.episodes}
    before_chain = first.w.claim_log.chain_head()

    first.post_scene_change(750)  # behind already projected time -> quarantine
    assert first.w.graph_store.unresolved_boundaries() == [750]
    _close(first)

    raw, graph = ClaimLog(str(raw_path)), SqliteGraphStore(str(graph_path))
    report = reconcile_pending(graph, raw)

    assert report["changed"] is True and report["version"] == 1
    assert report["claims"] == 2
    assert graph.unresolved_boundaries() == []
    assert graph.boundary_version() == 1
    assert raw.chain_head() == before_chain
    active = graph.load_episodes()
    active_ids = {ep.id for ep in active.values()}
    assert active_ids and active_ids.isdisjoint(old_ids)
    assert all(graph.resolve_episode_ids(old) for old in old_ids)
    assert set().union(*(set(graph.resolve_episode_ids(old)) for old in old_ids)) \
        <= active_ids
    for ep in active.values():
        assert graph.upkeep_seen(ep.id) == set(graph.claim_ids_for_episode(ep.id))

    copied = SqliteGraphStore(str(tmp_path / "copied-graph.db"))
    migrate(graph, copied)
    assert copied.boundary_version() == 1
    assert copied.unresolved_boundaries() == []
    assert {ep.id for ep in copied.load_episodes().values()} == active_ids
    assert all(copied.resolve_episode_ids(old) for old in old_ids)
    copied.close()
    raw._db.close()
    graph.close()

    restarted = _hub(raw_path, graph_path)
    assert {ep.id for ep in restarted.episodes} == active_ids
    # A node still citing a historical ID can traverse its redirects to raw.
    recovered = {c.id for old in old_ids
                 for c in restarted.claims_by_episode.get(old, ())}
    assert recovered == {c.id for c in claims}
    restarted.post_scene_change(750)  # rediscovery is not a new quarantine
    assert restarted.w.graph_store.unresolved_boundaries() == []
    assert reconcile_pending(restarted.w.graph_store,
                             restarted.w.claim_log)["changed"] is False
    new_place = make_claim("new place after v1", t_ms=2_500, frame="c",
                           place="brand-new-place")
    restarted.post_claims([new_place])
    new_ep = next(ep for ep in restarted.episodes
                  if new_place.id in ep.claim_ids)
    assert new_ep.id not in old_ids
    _close(restarted)

    one_more_restart = _hub(raw_path, graph_path)
    assert any(ep.id == new_ep.id for ep in one_more_restart.episodes)
    _close(one_more_restart)


def test_missing_raw_membership_refuses_to_retire_active_projection(tmp_path):
    raw_path, graph_path = tmp_path / "raw.db", tmp_path / "graph.db"
    raw = ClaimLog(str(raw_path))
    graph = SqliteGraphStore(str(graph_path))
    ep = Episode(id="old", place_key="p", t_range=TimeRange(1, 1),
                 claim_ids=("missing",))
    graph.persist_episode(0, ep, ["missing"])
    graph.add_boundary(1, "late_scene_pending")

    with pytest.raises(BoundaryReconciliationError):
        reconcile_pending(graph, raw)

    assert graph.boundary_version() == 0
    assert graph.unresolved_boundaries() == [1]
    assert {e.id for e in graph.load_episodes().values()} == {"old"}


def test_cli_maintenance_route_uses_the_offline_lock_and_durable_projection(
        tmp_path):
    from tracemem.__main__ import main

    raw_path, graph_path = tmp_path / "raw.db", tmp_path / "graph.db"
    lock_path = tmp_path / "trace.lock"
    raw = ClaimLog(str(raw_path))
    claim = make_claim("late scene", t_ms=500, frame="a")
    raw.append(claim)
    graph = SqliteGraphStore(str(graph_path))
    graph.persist_episode(0, Episode(
        id="old", place_key=claim.coord.world.place_key,
        t_range=TimeRange(500, 500), claim_ids=(claim.id,)), [claim.id])
    graph.add_boundary(400, "late_scene_pending")
    graph.close()
    raw._db.close()

    assert main(["--store", str(raw_path), "--graph", str(graph_path),
                 "--lock", str(lock_path), "--plaintext",
                 "--reconcile-boundaries"]) == 0
    reopened = SqliteGraphStore(str(graph_path))
    assert reopened.unresolved_boundaries() == []
    assert reopened.boundary_version() == 1
    reopened.close()
