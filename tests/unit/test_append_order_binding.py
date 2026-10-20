"""Restart oracles for append-sequence catch-up and per-claim completion."""
from tracemem.__main__ import _binding_backlog
from tracemem.__main__ import run
from tracemem.app.brain import Brain
from tracemem.app.wiring import build
from tests.fakes import ContextAnswerReasoner, FakeClock, HashEmbedder
from tests.helpers import make_claim


def _hub(raw, graph, now=1_000_000):
    return build({"store_path": str(raw), "graph_store_path": str(graph),
                  "embedder": HashEmbedder(),
                  "reasoner": ContextAnswerReasoner(),
                  "clock": FakeClock(now), "verify_answers": False})


def _close(hub):
    hub.w.claim_log._db.close()
    hub.w.graph_store.close()


def test_late_event_time_is_found_by_append_order_after_restart(tmp_path):
    raw, graph = tmp_path / "raw.sqlite3", tmp_path / "graph.sqlite3"
    first = _hub(raw, graph)
    current = make_claim("current", t_ms=900_000, frame="current")
    first.post_claims([current])
    assert first.w.graph_store.get_meta("bind_watermark_seq") == "1"

    # A phone/file sync lands second but truthfully keeps its older event time.
    late = make_claim("synced from yesterday", t_ms=100_000, frame="late")
    first.w.claim_log.append(late)
    _close(first)

    second = _hub(raw, graph)
    backlog = _binding_backlog(second.w.graph_store, second.w.claim_log)
    assert [c.id for c in backlog] == [late.id]

    brain = Brain(second, second.w.claim_log.append,
                  second.w.clock.now_ms)
    brain.backfill(backlog)
    assert second.w.graph_store.binding_seen([late.id]) == {late.id}
    assert second.w.graph_store.get_meta("bind_watermark_seq") == "2"
    _close(second)


def test_episode_membership_is_not_mistaken_for_completed_upkeep(tmp_path):
    raw, graph = tmp_path / "raw.sqlite3", tmp_path / "graph.sqlite3"
    first = _hub(raw, graph)
    claim = make_claim("persisted before crash", t_ms=200_000, frame="crash")
    first.w.claim_log.append(claim)

    # This is the precise crash window: Binder membership committed, upkeep
    # and its later completion receipt did not.
    from tracemem.domain.episode import Episode
    from tracemem.domain.time import TimeRange
    ep = Episode(id="crash-episode", place_key=claim.coord.world.place_key,
                 t_range=TimeRange(200_000, 200_000), claim_ids=(claim.id,))
    first.w.graph_store.persist_episode(0, ep, [claim.id])
    _close(first)

    second = _hub(raw, graph)
    backlog = _binding_backlog(second.w.graph_store, second.w.claim_log)
    Brain(second, second.w.claim_log.append,
          second.w.clock.now_ms).backfill(backlog)

    assert second.w.graph_store.binding_seen([claim.id]) == {claim.id}
    assert any(claim.id in second.w.graph_store.upkeep_seen(e.id)
               for e in second.episodes)
    _close(second)


def test_founder_ask_uses_the_same_live_binding_seam(tmp_path):
    raw, graph = tmp_path / "raw.sqlite3", tmp_path / "graph.sqlite3"
    hub = _hub(raw, graph)
    brain = Brain(hub, hub.w.claim_log.append, hub.w.clock.now_ms)

    brain.record_ask("What did I decide about the kiln?")

    founder = hub.w.claim_log._db.execute(
        "select id from claim where bidder='founder'").fetchone()[0]
    assert hub.w.graph_store.binding_seen([founder]) == {founder}
    assert hub.w.graph_store.get_meta("bind_watermark_seq") == "1"
    _close(hub)


def test_daemon_start_and_stop_witnesses_reach_binding_before_exit(tmp_path):
    raw, graph = tmp_path / "raw.sqlite3", tmp_path / "graph.sqlite3"
    hub = _hub(raw, graph)
    brain = Brain(hub, hub.w.claim_log.append, hub.w.clock.now_ms)

    run(store_path=str(raw), store=hub.w.claim_log, brain=brain,
        lock_path=str(tmp_path / "trace.lock"), seconds=0.05,
        tick_seconds=0.01, eyelid=False)

    claim_ids = [r[0] for r in hub.w.claim_log._db.execute(
        "select id from claim order by rowid")]
    assert len(claim_ids) == 2
    assert hub.w.graph_store.binding_seen(claim_ids) == set(claim_ids)
    assert int(hub.w.graph_store.get_meta("bind_watermark_seq")) == 2
    _close(hub)
