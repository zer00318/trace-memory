"""Black-box restart oracle for the napkin's night feedback loop."""
from tests.fakes import FakeClock, FakeLease
from tests.helpers import make_claim
from tracemem.app.hub import _EpisodeClaimMap
from tracemem.domain import (Attribute, Confidence, Episode, Grade, Modality,
                             Node, NodeKind, TimeRange)
from tracemem.nodes.accrual import names_of
from tracemem.nodes.marination.mining import GRAMMAR_VERSION
from tracemem.nodes.marination.nightly import run_night
from tracemem.nodes.sqlite_graph_store import SqliteGraphStore
from tracemem.text.claim_log import ClaimLog


def test_hunger_finds_retained_text_and_survives_two_restarts(tmp_path):
    """No in-process episode dictionary is allowed to make this pass.

    The claim, episode membership, hungry node, completion receipt, and mined
    truth all cross real SQLite closes and reopens.
    """
    raw_path = str(tmp_path / "raw.sqlite3")
    graph_path = str(tmp_path / "graph.sqlite3")
    claim = make_claim(
        "this is my neighbor Priya", t_ms=1_500, place="wifi:home",
        frame="phone-1", modality=Modality.MIC, perceiver="asr", app=None)
    episode = Episode(
        id="episode-old", place_key="wifi:home",
        t_range=TimeRange(1_000, 2_000), claim_ids=(claim.id,))
    ghost = Node(
        id="ghost-old", kind=NodeKind.ENTITY, activation=.9,
        last_active_t_ms=2_000,
        attributes=[Attribute(
            key="appearance", value="a person", grade=Grade.GIST,
            confidence=Confidence(.6), source_episode_ids=(episode.id,),
            t_ms=1_500)])

    raw = ClaimLog(raw_path)
    raw.append(claim)
    graph = SqliteGraphStore(graph_path)
    graph.persist_episode(0, episode, [claim.id])
    graph.upsert_node(ghost)
    graph.close()
    raw._db.close()

    # First restart: reconstruct the evidence traversal from durable stores,
    # then let hunger re-read the retained text.
    raw = ClaimLog(raw_path)
    graph = SqliteGraphStore(graph_path)
    evidence = _EpisodeClaimMap(graph, raw)
    report = run_night(
        graph, raw, None, FakeLease(), FakeClock(3_000),
        claims_by_episode=evidence)
    assert (ghost.id, "seen_as", "Priya") in report.mined
    assert report.hunger_grammar == GRAMMAR_VERSION
    assert graph.get_meta("night_hunger_pending") == "0"
    graph.close()
    raw._db.close()

    # Second restart: both the result and its completion receipt remain, and
    # unchanged evidence is not chewed again.
    raw = ClaimLog(raw_path)
    graph = SqliteGraphStore(graph_path)
    assert "Priya" in names_of(graph.get_node(ghost.id))
    second = run_night(
        graph, raw, None, FakeLease(), FakeClock(4_000),
        claims_by_episode=_EpisodeClaimMap(graph, raw))
    assert not any(item[:2] == (ghost.id, "seen_as") for item in second.mined)

