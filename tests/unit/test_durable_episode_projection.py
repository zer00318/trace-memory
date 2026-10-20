"""Restart-level oracles for the Binder's durable projection."""
from tests.fakes import ContextAnswerReasoner, FakeClock, HashEmbedder
from tests.helpers import make_claim
from tracemem.app.wiring import build
from tracemem.nodes.sqlite_graph_store import SqliteGraphStore


def _hub(raw, graph):
    return build({"store_path": str(raw), "graph_store_path": str(graph),
                  "embedder": HashEmbedder(),
                  "reasoner": ContextAnswerReasoner(),
                  "clock": FakeClock(1_000_000),
                  "verify_answers": False})


def _close(hub):
    hub.w.claim_log._db.close()
    hub.w.graph_store.close()


def test_boundaries_episodes_and_membership_survive_restart(tmp_path):
    raw, graph = tmp_path / "raw.sqlite3", tmp_path / "graph.sqlite3"
    first = _hub(raw, graph)
    first.post_scene_change(100_000)
    first.post_scene_change(200_000)
    claim = make_claim("durable moment", t_ms=150_000, frame="f-old")
    first.post_claims([claim])
    old_episode = next(ep for ep in first.episodes if claim.id in ep.claim_ids)
    _close(first)

    second = _hub(raw, graph)

    assert second._scene_changes == [100_000, 200_000]
    assert any(ep.id == old_episode.id for ep in second.episodes)
    restored = second.claims_by_episode.get(old_episode.id)
    assert restored is not None
    assert [c.id for c in restored] == [claim.id]
    _close(second)


def test_restart_cannot_reuse_an_old_episode_id_for_a_new_moment(tmp_path):
    raw, graph = tmp_path / "raw.sqlite3", tmp_path / "graph.sqlite3"
    first = _hub(raw, graph)
    # More than enough persisted scene cuts to recreate the ordinal-reuse
    # failure from the audit without building a million forced-grid entries.
    for t_ms in range(10_000, 1_010_000, 10_000):
        first.post_scene_change(t_ms)
    old_claim = make_claim("old", t_ms=15_000, frame="f-old")
    first.post_claims([old_claim])
    old_ids = {ep.id for ep in first.episodes}
    _close(first)

    second = _hub(raw, graph)
    second.post_scene_change(1_020_000)
    new_claim = make_claim("new", t_ms=1_025_000, frame="f-new")
    second.post_claims([new_claim])
    new_episode = next(ep for ep in second.episodes if new_claim.id in ep.claim_ids)

    assert new_episode.id not in old_ids
    assert second.w.graph_store.claim_ids_for_episode(new_episode.id) == [new_claim.id]
    _close(second)


def test_existing_upkeep_membership_is_adopted_without_rewriting_raw(tmp_path):
    path = tmp_path / "graph.sqlite3"
    graph = SqliteGraphStore(str(path))
    graph._db.execute("insert into upkeep_seen values ('legacy-ep','legacy-claim')")
    graph._db.commit()
    graph.close()

    reopened = SqliteGraphStore(str(path))

    assert reopened.claim_ids_for_episode("legacy-ep") == ["legacy-claim"]
    assert "legacy-ep" in reopened.episode_ids()
    reopened.close()


def test_late_boundary_is_quarantined_instead_of_rekeying_live_episodes(tmp_path):
    raw, graph = tmp_path / "raw.sqlite3", tmp_path / "graph.sqlite3"
    hub = _hub(raw, graph)
    hub.post_claims([make_claim("already bound", t_ms=200_000, frame="f")])
    before = {key: ep.id for key, ep in hub._episodes.items()}

    hub.post_scene_change(100_000)

    assert {key: ep.id for key, ep in hub._episodes.items()} == before
    assert hub._scene_changes == []
    assert hub.w.graph_store.load_boundaries("late_scene_pending") == [100_000]
    _close(hub)


def test_live_batch_hydrates_only_its_touched_historical_episode(tmp_path):
    """Lifetime membership must not become the cost of every live flush."""
    raw, graph_path = tmp_path / "raw.sqlite3", tmp_path / "graph.sqlite3"
    graph = SqliteGraphStore(str(graph_path))
    from tracemem.domain.episode import Episode
    from tracemem.domain.time import TimeRange
    for i in range(40):
        ep = Episode(id="historic-%d" % i, place_key="place-%d" % i,
                     t_range=TimeRange(i, i), claim_ids=())
        graph.persist_episode(0, ep, ["old-%d" % i])
    graph.close()

    hub = _hub(raw, graph_path)
    claim = make_claim("one live claim", t_ms=500_000, frame="live")
    matching = Episode(id="matching", place_key=claim.coord.world.place_key,
                       t_range=TimeRange(0, 0), claim_ids=())
    hub.w.graph_store.persist_episode(0, matching, [])
    # Re-open so the just-persisted matching episode is part of Hub's durable
    # projection exactly as it would be after a process restart.
    _close(hub)
    hub = _hub(raw, graph_path)

    loads = []
    original = hub._claims_by_episode._load

    def counted(episode_id):
        loads.append(episode_id)
        return original(episode_id)

    hub._claims_by_episode._load = counted
    hub.post_claims([claim])

    assert loads == ["matching"]
    _close(hub)


def test_vector_summary_decrypts_only_its_bounded_prefix_after_restart(tmp_path):
    """A 600-char index surface must not hydrate a lifetime episode."""
    from types import SimpleNamespace

    raw, graph_path = tmp_path / "raw.sqlite3", tmp_path / "graph.sqlite3"
    first = _hub(raw, graph_path)
    claims = [make_claim("evidence-%03d-%s" % (i, "x" * 70),
                         t_ms=100_000 + i, frame="f-%d" % i)
              for i in range(200)]
    first.post_claims(claims)
    episode = next(ep for ep in first.episodes if claims[0].id in ep.claim_ids)
    _close(first)

    second = _hub(raw, graph_path)
    reads = []
    original = second.w.claim_log.get

    def counted(claim_id):
        reads.append(claim_id)
        return original(claim_id)

    second.w.claim_log.get = counted
    node = SimpleNamespace(attributes=[SimpleNamespace(
        source_episode_ids=(episode.id,))])
    text = second._node_evidence(node)

    assert len(text) <= second.w.vector_index.EVIDENCE_CHARS
    assert 1 <= len(reads) <= second.w.vector_index.EVIDENCE_CLAIMS, (
        "indexing decrypted %d claims for a 600-character surface" % len(reads))
    # The bounded path must not populate the complete lazy episode cache.
    assert episode.id not in second._claims_by_episode
    _close(second)


def test_bounded_summary_applies_the_membership_limit_inside_the_graph():
    """Slicing after claim_ids_for_episode already paid the lifetime cost."""
    from tracemem.app.hub import _EpisodeClaimMap

    class Graph:
        def episode_ids(self): return ["ep"]
        def resolve_episode_ids(self, _ep): return ["ep-v2"]
        def claim_ids_for_episode(self, _ep):
            raise AssertionError("complete membership must stay in SQLite")
        def evidence_claim_ids(self, episode_ids, limit):
            assert episode_ids == ["ep-v2"]
            assert 1 <= limit <= 8
            return ["c1"]

    claim = make_claim("small bounded evidence", t_ms=1, frame="f")

    class Log:
        def get(self, claim_id):
            assert claim_id == "c1"
            return claim

    mapping = _EpisodeClaimMap(Graph(), Log())
    assert mapping.bounded_text("ep", 600) == "small bounded evidence"
