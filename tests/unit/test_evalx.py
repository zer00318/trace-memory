"""Falsification slice: fixture determinism, provenance rule, F1 purity,
the gate table, the novelty probe, and the harness end-to-end on fakes."""
from __future__ import annotations

import ast
import inspect
import json

import pytest

from tests.fakes import ContextAnswerReasoner, HashEmbedder
from tracemem.evalx import baseline_rag, compression_probe, context_recall, query_set
from tracemem.evalx.decision_gate import verdict
from tracemem.evalx.fixtures import synthetic_week
from tracemem.evalx.harness import run as harness_run
from tracemem.text.claim_log import ClaimLog


def test_fixture_is_seed_deterministic():
    a, b = synthetic_week(seed=7), synthetic_week(seed=7)
    assert [c.id for c in a.claims] == [c.id for c in b.claims]
    assert a.scene_changes == b.scene_changes


def test_query_set_provenance_rule():
    qs = query_set.parse_all(synthetic_week().questions)
    eligible = query_set.gate_eligible(qs)
    assert all(q.authored_from == "life" and not q.gap for q in eligible)
    assert any(q.authored_from == "store" for q in qs)      # exists, but excluded
    with pytest.raises(ValueError):
        query_set.parse_all([{"id": "x", "text": "?", "type": "weird",
                              "authored_from": "life"}])


def test_baseline_imports_nothing_from_the_cathedral():
    tree = ast.parse(inspect.getsource(baseline_rag))
    mods = {n.module for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and n.module}
    mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
             for a in n.names}
    forbidden = ("binder", "nodes", "supplier")
    assert not any(f in m for m in mods for f in forbidden), mods   # F1 structural


def test_decision_gate_full_table():
    assert verdict({"emergent_correct": 0.7, "factual_correct": 0.2}).code == "SHED_SUFFICES"
    assert verdict({"emergent_correct": 0.1, "factual_correct": 0.8}).code == "CATHEDRAL_JUSTIFIED"
    assert verdict({"emergent_correct": 0.1, "factual_correct": 0.5}).code == "FIX_UPSTREAM"
    assert verdict({"emergent_correct": 0.4, "factual_correct": 0.9}).code == "FIX_UPSTREAM"
    # ruling A4: the loop has an exit
    assert verdict({"emergent_correct": 0.1, "factual_correct": 0.5},
                   prior_upstream_loops=2).code == "CAPTURE_BOUND"
    assert verdict({"emergent_correct": 0.4, "factual_correct": 0.9},
                   prior_upstream_loops=2).code == "CAPTURE_BOUND"


def test_novelty_routine_plateaus_chaotic_does_not(tmp_path):
    emb = HashEmbedder()
    routine = ClaimLog(str(tmp_path / "r.db"))
    for c in synthetic_week().claims:
        routine.append(c)
    chaotic = ClaimLog(str(tmp_path / "c.db"))
    for c in synthetic_week(chaotic=True).claims:
        chaotic.append(c)
    r_curve = compression_probe.novelty_curve(routine, emb)
    c_curve = compression_probe.novelty_curve(chaotic, emb)
    assert compression_probe.plateaus(r_curve)
    assert not compression_probe.plateaus(c_curve)


def test_context_recall_hand_computed():
    queries = [{"id": "q1", "cue": "x", "relevant": {"a", "b", "c", "d"}}]
    report = context_recall.evaluate(
        queries,
        retrieve_fn=lambda cue: ["a", "b", "z"],            # found 2 of 4
        pack_fn=lambda cands, cue, budget: (["a"], 50),     # kept 1 of the 2
        budget=100)
    row = report.rows[0]
    assert row.coverage == pytest.approx(0.5)
    assert row.selection == pytest.approx(0.5)
    assert row.cost == pytest.approx(0.5)


def test_harness_emits_report_and_failures(tmp_path):
    week = synthetic_week()
    store = str(tmp_path / "snapshot.db")
    log = ClaimLog(store)
    for c in week.claims:
        log.append(c)
    qpath = str(tmp_path / "questions.json")
    with open(qpath, "w") as f:
        json.dump(week.questions, f)
    out = str(tmp_path / "out")
    r1 = harness_run(store, qpath, budget=800, embedder=HashEmbedder(),
                     reasoner=ContextAnswerReasoner(), out_dir=out)
    r2 = harness_run(store, qpath, budget=800, embedder=HashEmbedder(),
                     reasoner=ContextAnswerReasoner())
    assert r1["verdict"] == r2["verdict"]                   # deterministic
    assert r1["verdict"] in {"SHED_SUFFICES", "CATHEDRAL_JUSTIFIED",
                             "FIX_UPSTREAM", "CAPTURE_BOUND"}
    assert 0.0 <= r1["factual_correct"] <= 1.0
    # 2 of the fixture's "emergent" questions have single-claim gold and are
    # auto-invalidated (rehearsal finding, 2026-07-19): 5 eligible -> 3.
    assert r1["n_gate_eligible"] == 3
    assert r1["invalid_emergent"] == ["q-morning", "q-routine"]
    with open(out + "/report.json") as f:
        assert json.load(f)["verdict"] == r1["verdict"]
    assert (tmp_path / "out" / "failures.md").exists()
