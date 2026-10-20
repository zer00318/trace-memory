"""The cockpit's progress comes from executable invariants, not status JSON."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import cockpit  # noqa: E402


def _wire(tmp_path, monkeypatch):
    store = tmp_path / "raw.sqlite3"
    graph = tmp_path / "graph.sqlite3"
    monkeypatch.setattr(cockpit, "STORE", store)
    monkeypatch.setattr(cockpit, "GRAPH", graph)
    monkeypatch.setattr(cockpit, "SOCK", tmp_path / "no-daemon.sock")
    return store, graph


def test_executable_checks_name_current_truth_spine_failures(tmp_path, monkeypatch):
    store, graph = _wire(tmp_path, monkeypatch)
    con = sqlite3.connect(store)
    con.execute("create table claim (id text, identity_version integer, "
                "bidder text, modality text)")
    con.executemany("insert into claim values (?,?,?,?)", [
        ("legacy", 1, "eye", "screen"),
        ("grade", 2, "founder", "screen"),
    ])
    con.commit()
    con.close()

    con = sqlite3.connect(graph)
    con.execute("create table upkeep_seen (claim_id text)")
    con.execute("insert into upkeep_seen values ('legacy')")
    con.execute("create table node (attributes text)")
    con.execute("insert into node values (?)", (json.dumps([
        {"value": "private semantic memory"}]),))
    con.execute("create table tombstone (id text, kind text, retired_t_ms integer, attributes text)")
    con.commit()
    con.close()

    checks = {c["name"]: c for c in cockpit.executable_checks()}

    assert checks["Raw observation identity"]["state"] == "fail"
    assert checks["Durable binding"]["state"] == "fail"
    assert checks["Binding backlog"]["state"] == "fail"
    assert checks["Founder truth reaches graph"]["state"] == "fail"
    assert checks["Semantic graph confidentiality"]["state"] == "fail"
    # In-memory proofs are unit tests, not production invariants: they moved
    # to tests/unit/test_living_node_behaviour.py (review, 2026-10-15).
    for synthetic in ("Living evidence invalidates work", "Confidence accrues",
                      "Citation closure"):
        assert synthetic not in checks
    assert checks["Mac eye cadence and attribution"]["state"] == "fail"
    assert "signed daemon is unreachable" in checks[
        "Mac eye cadence and attribution"]["detail"]
    assert checks["Night hunger re-reads durable text"]["state"] == "fail"
    assert checks["Ambient ear production path"]["state"] == "fail"
    assert ("installed artifact lacks" in
            checks["Ambient ear production path"]["detail"] or
            "produced no claims" in
            checks["Ambient ear production path"]["detail"])
    assert checks["Macro visual understanding"]["state"] == "fail"
    assert "production-proven" in checks["Macro visual understanding"]["detail"]
    assert checks["Sandboxed helper production path"]["state"] == "fail"
    assert checks["Browsable live graph"]["state"] == "fail"
    assert checks["Eyes and ears in production"]["state"] == "fail"


def test_retired_beliefs_are_browsable_from_durable_rows(tmp_path, monkeypatch):
    _, graph = _wire(tmp_path, monkeypatch)
    con = sqlite3.connect(graph)
    con.execute("create table tombstone (id text, kind text, retired_t_ms integer, attributes text)")
    con.execute("insert into tombstone values (?,?,?,?)", (
        "retired-node-123", "hypothesis", 1234,
        json.dumps([{"value": "Old belief", "grade": 3, "t_ms": 10,
                     "source_episode_ids": ["ep-1"]}]),))
    con.commit()
    con.close()

    retired = cockpit._retired_beliefs()

    assert retired == [{"id": "retired-node-123", "kind": "hypothesis",
                        "label": "Old belief", "retired_t_ms": 1234,
                        "source_episode_ids": ["ep-1"]}]


def test_campaign_percentages_do_not_drive_the_top_line():
    assert "of executable napkin invariants passing" in cockpit.HTML
    assert "const total=pct(TREE)" not in cockpit.HTML
    assert "Planning map — shown for navigation, never used as progress" in cockpit.HTML


def test_completed_legacy_audit_is_truthful_progress_not_a_raw_rewrite(
        tmp_path, monkeypatch):
    store, graph = _wire(tmp_path, monkeypatch)
    con = sqlite3.connect(store)
    con.execute("create table claim (id text, identity_version integer, "
                "bidder text, modality text)")
    con.executemany("insert into claim values (?,?,?,?)", [
        ("old", 1, "eye", "screen"), ("new", 2, "eye", "screen")])
    con.execute("create table claim_identity_audit (claim_id text primary key, "
                "candidate_v2_id text, wire_sha256 text, status text)")
    con.execute("insert into claim_identity_audit values "
                "('old','candidate','digest','valid_v1')")
    con.commit()
    con.close()
    con = sqlite3.connect(graph)
    con.execute("create table node (attributes text)")
    con.commit()
    con.close()

    identity = {c["name"]: c for c in cockpit.executable_checks()}[
        "Raw observation identity"]

    assert identity["state"] == "pass"
    assert "legacy mapped 1/1" in identity["detail"]
    assert "remain unknowable" in identity["detail"]
