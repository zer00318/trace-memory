"""The arena harness itself under test — the referee must be trustworthy
before any verdict it produces is."""
from __future__ import annotations

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "arena", Path(__file__).resolve().parents[2] / "tools" / "arena.py")
arena = importlib.util.module_from_spec(spec)
spec.loader.exec_module(arena)

from tracemem.contenders import FutureAnswerMemory, UnderstandingMemory, WiringMemory


def test_dry_run_emits_complete_scorecards():
    claims, questions, plant = arena.build_week()
    cards = arena.run([UnderstandingMemory(), FutureAnswerMemory(), WiringMemory()],
                      claims, questions, plant)
    assert set(cards) == {"design_a", "design_b", "design_c"}
    for c in cards.values():
        for key in ("ingest_s", "marinate_s", "bytes", "objects",
                    "citation_validity", "recall_at_pack", "questions"):
            assert key in c
        assert c["citation_validity"] == 1.0, "referee caught an invalid citation"
        assert len(c["questions"]) == 4


def test_dry_run_planted_facts_are_findable():
    """At least the associative engines must hit the planted facts on the
    easy dry-run week — an engine below 0.5 here would be arena-dead anyway."""
    claims, questions, plant = arena.build_week()
    cards = arena.run([UnderstandingMemory(), FutureAnswerMemory(), WiringMemory()],
                      claims, questions, plant)
    assert any(c["recall_at_pack"] >= 0.75 for c in cards.values())
    for c in cards.values():
        assert c["recall_at_pack"] >= 0.5, f"an engine missed most planted facts: {c}"


def test_aggregate_question_is_never_guessed_by_c():
    claims, questions, plant = arena.build_week()
    cards = arena.run([WiringMemory()], claims, questions, plant)
    agg = [q for q in cards["design_c"]["questions"] if q["class"] == "aggregate"][0]
    assert agg["routed"], "design C must route counting questions, not guess"
