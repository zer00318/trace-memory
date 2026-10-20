"""Design 0 enters the arena — the incumbent under the same contract."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from tests.fakes import HashEmbedder
from tests.helpers import make_claim
from tracemem.contenders.design_zero import DesignZeroAdapter

spec = importlib.util.spec_from_file_location(
    "arena", Path(__file__).resolve().parents[2] / "tools" / "arena.py")
arena = importlib.util.module_from_spec(spec)
spec.loader.exec_module(arena)


def test_design_zero_runs_the_dry_arena():
    claims, questions, plant = arena.build_week()
    cards = arena.run([DesignZeroAdapter(HashEmbedder())], claims, questions, plant)
    c = cards["design_0"]
    assert c["citation_validity"] == 1.0, "the incumbent fabricated a receipt"
    assert c["objects"] > 0, "birth never ran — no nodes exist"


def test_design_zero_mouth_is_unreachable():
    m = DesignZeroAdapter(HashEmbedder())
    m.ingest([make_claim(text="Sam sent the Berlin draft", t_ms=1000, frame="f1")])
    m.marinate(10)
    m.supply("Sam Berlin?")            # must not touch the reasoner
    try:
        m._hub.w.reasoner.anything
        assert False, "sentinel failed"
    except AssertionError as e:
        assert "forbidden" in str(e) or "sentinel failed" not in str(e)


def test_design_zero_drops_unresolvable_sources_instead_of_lying():
    m = DesignZeroAdapter(HashEmbedder())
    m.ingest([make_claim(text=f"Sam note {i}", t_ms=1000 + i, frame=f"f{i}")
              for i in range(5)])
    m.marinate(10)
    pkg = m.supply("Sam?")
    ingested = set(m._claims)
    assert all(set(b.claim_ids) <= ingested for b in pkg.blocks)
