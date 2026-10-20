"""The witness vocabulary — one legal shape, machine-consumable, complete."""
from __future__ import annotations

import subprocess
from pathlib import Path

from tracemem.app.witness import WitnessKind, witness
from tracemem.domain import Grade


def test_witness_is_a_wellformed_memory_claim():
    w = witness(WitnessKind.GAP, t_ms=1000, detail="last claim 9h ago",
                span_t2_ms=33_400_000)
    assert w.scope == "memory"                  # A4: never a frame read
    assert w.grade == Grade.VERBATIM
    assert w.structured["witness"] == "gap"
    assert w.structured["t2_ms"] == 33_400_000
    assert w.text.startswith("[witness:gap]")
    assert w.bidder == "system:witness" and w.id


def test_interval_and_point_witnesses():
    p = witness(WitnessKind.START, t_ms=5)
    assert "t2_ms" not in p.structured
    i = witness(WitnessKind.OVERWHELM, t_ms=5, span_t2_ms=9,
                structured_extra={"dropped": 42})
    assert i.structured["dropped"] == 42 and i.structured["t2_ms"] == 9


def test_no_ad_hoc_witness_strings_outside_the_vocabulary():
    """The grep oracle, as a test: '[witness:' literals may exist ONLY in
    this module (and tests). An organ hand-rolling one is a silent hole."""
    root = Path(__file__).resolve().parents[2] / "src"
    hits = subprocess.run(
        ["grep", "-rl", "[witness:", str(root)],
        capture_output=True, text=True).stdout.splitlines()
    allowed = {str(root / "tracemem" / "app" / "witness.py")}
    assert set(hits) <= allowed, f"ad-hoc witness strings in: {set(hits) - allowed}"


def test_every_kind_builds():
    for k in WitnessKind:
        assert witness(k, t_ms=1).structured["witness"] == k.value
