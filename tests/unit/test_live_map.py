"""The user-facing graph is topology plus followable raw evidence."""
from __future__ import annotations

import json
import sys

import pytest

from tests.helpers import make_claim
from tracemem.app.ask_socket import AskSocket
from tracemem.app.live_map import detail, snapshot
from tracemem.domain import Confidence, Edge, EdgeType, Grade, Node, NodeKind
from tracemem.domain.episode import Episode
from tracemem.domain.node import Attribute
from tracemem.domain.time import TimeRange
from tracemem.nodes.graph_store import MemoryGraphStore
from tracemem.nodes.sqlite_graph_store import SqliteGraphStore
from tracemem.text.claim_log import ClaimLog


def _attr(value, episode="ep-1", key="seen_as"):
    return Attribute(key=key, value=value, grade=Grade.VERBATIM,
                     confidence=Confidence(0.9),
                     source_episode_ids=(episode,), t_ms=2000,
                     first_t_ms=1000)


def _graph_and_log(tmp_path):
    claim = make_claim("Maya moved the kiln firing to Thursday",
                       t_ms=1500, frame="map-frame")
    log = ClaimLog(str(tmp_path / "raw.sqlite3"))
    log.append(claim)
    graph = MemoryGraphStore()
    graph.persist_episode(0, Episode(
        id="ep-1", t_range=TimeRange(1000, 2000),
        place_key=None, claim_ids=()), [claim.id])
    maya = Node(id="person-maya", kind=NodeKind.ENTITY,
                activation=0.8, last_active_t_ms=2000,
                attributes=[_attr("Maya")])
    kiln = Node(id="object-kiln", kind=NodeKind.OBJECT,
                activation=0.5, last_active_t_ms=1500,
                attributes=[_attr("kiln", key="object")])
    old = Node(id="belief-old", kind=NodeKind.HYPOTHESIS,
               derivation_depth=1, parent_ids=(maya.id, kiln.id),
               attributes=[_attr("Maya owns the kiln", key="hypothesis")])
    for node in (maya, kiln, old):
        graph.upsert_node(node)
    graph.upsert_edge(Edge(maya.id, kiln.id, EdgeType.CO_OCCURS,
                           weight=2.0, evidence_count=3))
    graph.retire_meta(old.id, 3000)
    return graph, log, claim


def test_snapshot_contains_current_edges_and_retired_beliefs(tmp_path):
    graph, _log, _claim = _graph_and_log(tmp_path)

    got = snapshot(graph)

    by_id = {n["id"]: n for n in got["nodes"]}
    assert set(by_id) == {"person-maya", "object-kiln", "belief-old"}
    assert by_id["belief-old"]["retired"] is True
    assert any(e["src"] == "person-maya" and e["dst"] == "object-kiln"
               for e in got["edges"])
    assert any(e.get("historic") and e["src"] == "belief-old"
               for e in got["edges"])


def test_search_and_cap_cannot_hide_the_retired_moat(tmp_path):
    graph, _log, _claim = _graph_and_log(tmp_path)
    for i in range(200):
        graph.upsert_node(Node(id="site-%03d" % i, kind=NodeKind.SITE,
                               activation=1.0,
                               attributes=[_attr("site %03d" % i,
                                                 key="site")]))

    capped = snapshot(graph, limit=20)
    assert len(capped["nodes"]) == 20
    assert any(n["retired"] for n in capped["nodes"])
    searched = snapshot(graph, query="Maya owns")
    assert [n["id"] for n in searched["nodes"]] == ["belief-old"]


def test_belief_detail_resolves_exact_supporting_raw(tmp_path):
    graph, log, claim = _graph_and_log(tmp_path)

    got = detail(graph, log, "person-maya")

    assert got["label"] == "Maya"
    assert got["retired"] is False
    assert got["evidence"][0]["claim_id"] == claim.id
    assert got["evidence"][0]["text"] == claim.text
    assert got["neighbours"][0]["id"] == "object-kiln"

    retired = detail(graph, log, "belief-old")
    assert retired["retired"] is True
    assert {n["id"] for n in retired["neighbours"]} == {
        "person-maya", "object-kiln"}
    assert retired["evidence"][0]["claim_id"] == claim.id


def test_belief_detail_is_bounded_before_raw_decryption(tmp_path):
    """A graph node may cite a lifetime-sized episode; the face shows forty.

    The bound has to apply to membership and decryptions, not merely to the
    final JSON list, because detail runs on the daemon's one owner thread.
    """
    graph, log, _claim = _graph_and_log(tmp_path)
    for i in range(200):
        c = make_claim("evidence %03d" % i, t_ms=2000 + i,
                       frame="many-%03d" % i)
        log.append(c)
        graph.persist_episode(0, Episode(
            id="ep-1", t_range=TimeRange(1000, 3000),
            place_key=None, claim_ids=()), [c.id])

    calls = {"get": 0}
    original = log.get

    def counted(claim_id):
        calls["get"] += 1
        return original(claim_id)

    log.get = counted
    got = detail(graph, log, "person-maya")

    assert len(got["evidence"]) == 40
    assert got["evidence_truncated"] is True
    assert calls["get"] == 41


def test_private_socket_exposes_map_and_belief_as_bounded_read_ops(tmp_path):
    class Brain:
        def live_map(self, query, include_retired, limit):
            return {"nodes": [{"id": "n1"}], "edges": [],
                    "query": query, "shown": 1}

        def belief(self, node_id):
            return {"id": node_id, "attributes": [], "evidence": []}

    socket = AskSocket(Brain(), path=tmp_path / "unused.sock")
    mapped = socket._handle(json.dumps(
        {"op": "map", "query": "maya", "limit": 10}).encode())
    opened = socket._handle(json.dumps(
        {"op": "belief", "id": "n1"}).encode())

    assert mapped["ok"] and mapped["query"] == "maya"
    assert opened["ok"] and opened["id"] == "n1"
    assert {"map", "belief"} <= set(socket.OPS)


def test_encrypted_live_and_retired_map_survives_graph_restart(tmp_path):
    raw_path, graph_path = tmp_path / "raw.sqlite3", tmp_path / "graph.sqlite3"
    claim = make_claim("Nia revised the kiln schedule", t_ms=1500,
                       frame="restart-map")
    raw = ClaimLog(str(raw_path))
    raw.append(claim)
    key = b"m" * 32
    graph = SqliteGraphStore(str(graph_path), key=key)
    graph.persist_episode(0, Episode(
        id="ep-1", t_range=TimeRange(1000, 2000), place_key=None,
        claim_ids=()), [claim.id])
    current = Node(id="person-nia", kind=NodeKind.ENTITY,
                   attributes=[_attr("Nia")])
    old = Node(id="old-schedule", kind=NodeKind.HYPOTHESIS,
               derivation_depth=1, parent_ids=(current.id,),
               attributes=[_attr("Nia preferred Monday", key="hypothesis")])
    graph.upsert_node(current)
    graph.upsert_node(old)
    graph.retire_meta(old.id, 3000)
    graph.close()
    raw._db.close()

    # The semantic label is ciphertext on disk, yet the daemon-side adapter
    # can reopen with its key and return both the live and historic belief.
    assert b"Nia preferred Monday" not in graph_path.read_bytes()
    reopened_graph = SqliteGraphStore(str(graph_path), key=key)
    reopened_raw = ClaimLog(str(raw_path))
    mapped = snapshot(reopened_graph)
    assert {n["label"] for n in mapped["nodes"]} == {
        "Nia", "Nia preferred Monday"}
    opened = detail(reopened_graph, reopened_raw, "old-schedule")
    assert opened["retired"] is True
    assert opened["evidence"][0]["claim_id"] == claim.id
    reopened_graph.close()
    reopened_raw._db.close()


@pytest.mark.skipif(sys.platform != "darwin", reason="native TRACE.app face")
def test_native_appkit_canvas_renders_current_and_retired_topology():
    from tracemem.app.graph_window import build_controller

    Controller = build_controller()
    controller = Controller.alloc().init()
    controller.canvas.setGraph_edges_([
        {"id": "current", "label": "Maya", "kind": "person",
         "retired": False, "evidence_count": 3},
        {"id": "retired", "label": "Old belief", "kind": "hypothesis",
         "retired": True, "evidence_count": 1},
    ], [{"src": "current", "dst": "retired", "weight": 1.2}])
    controller.canvas.display()                 # executes drawRect_ in AppKit

    assert set(controller.canvas.positions) == {"current", "retired"}
    controller.win.close()


def test_drilling_one_belief_never_decrypts_the_graveyard(tmp_path):
    """WITNESSED 2026-10-14: every drill took 2.0-5.5s against the cockpit's
    3s probe. `detail` asks `_find` about every neighbour, and an edge whose
    other end no longer exists (thousands of derived_from edges on the live
    graph) fell through to `graph.retired()` -- every tombstone decrypted,
    once per such edge. Asking about one id must cost one row."""
    graph = SqliteGraphStore(str(tmp_path / "g.sqlite3"), key=b"k" * 32)
    log = ClaimLog(str(tmp_path / "raw.sqlite3"))
    hub = Node(id="hub", kind=NodeKind.ENTITY, activation=0.9,
               last_active_t_ms=2000, attributes=[_attr("Hub")])
    graph.upsert_node(hub)
    for i in range(30):
        ghost = "gone-%d" % i                          # edge to nothing
        graph.upsert_edge(Edge(hub.id, ghost, EdgeType.DERIVED_FROM))
    for i in range(20):
        dead = Node(id="dead-%d" % i, kind=NodeKind.HYPOTHESIS,
                    derivation_depth=1, parent_ids=(hub.id,),
                    attributes=[_attr("old idea %d" % i, key="hypothesis")])
        graph.upsert_node(dead)
        graph.retire_meta(dead.id, 3000)
    graph.upsert_edge(Edge(hub.id, "dead-3", EdgeType.DERIVED_FROM))

    opened = []
    real = graph._node_from_row

    def counting(row):
        opened.append(row[0])
        return real(row)

    graph._node_from_row = counting
    out = detail(graph, log, hub.id)
    assert any(n["id"] == "dead-3" and n["retired"] for n in out["neighbours"])
    assert len(opened) <= 3, "%d node decrypts for one drill" % len(opened)


def test_an_unchanged_node_is_decrypted_once_across_whole_graph_reads(tmp_path):
    """WITNESSED 2026-10-15: `map` took 3.4-4.8s while the founder was active;
    every call decrypted every node's sealed attributes again although almost
    none had changed. A sealed value's ciphertext is unique per write, so the
    plaintext may be reused exactly while the stored bytes are identical."""
    graph = SqliteGraphStore(str(tmp_path / "g.sqlite3"), key=b"k" * 32)
    for i in range(12):
        graph.upsert_node(Node(id="n%d" % i, kind=NodeKind.ENTITY,
                               attributes=[_attr("thing %d" % i)]))
    opened = []
    real = graph._protector.open

    def counting(scope, row, value):
        opened.append(row)
        return real(scope, row, value)

    graph._protector.open = counting
    graph.all_nodes()
    first = len(opened)
    graph.all_nodes()
    assert first == 12 and len(opened) == 12, "second read decrypted again"

    changed = graph.get_node("n3")
    changed.attributes.append(_attr("renamed", key="label"))
    graph.upsert_node(changed)
    labels = {n.id: [a.value for a in n.attributes] for n in graph.all_nodes()}
    assert "renamed" in labels["n3"], "a rewritten row must never be served stale"
    assert len(opened) == 13
