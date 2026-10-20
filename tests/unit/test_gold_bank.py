"""Eval-only frames: banked at a cadence, owner-only, capped (2026-10-20)."""
from __future__ import annotations

import json
import stat
from types import SimpleNamespace

from tracemem.extraction.gold_bank import GoldBank

MIN = 60_000


def _frame(w=64, h=40):
    return SimpleNamespace(width=w, height=h, bytes_per_row=w * 4,
                           data=bytes([200, 120, 40, 255]) * (w * h))


def test_frames_are_banked_at_the_cadence_and_kept_private(tmp_path):
    bank = GoldBank(tmp_path / "frames", every_ms=4 * MIN, max_frames=10)
    assert bank.maybe_bank(_frame(), 0, "Mail") is not None
    assert bank.maybe_bank(_frame(), 2 * MIN, "Mail") is None, "too soon"
    image = bank.maybe_bank(_frame(), 4 * MIN, "Code", window="window:7")
    assert image is not None and image.read_bytes()[:2] == b"\xff\xd8"   # JPEG
    meta = json.loads(image.with_suffix(".json").read_text())
    assert meta == {"id": image.stem, "class": "live", "t_ms": 4 * MIN,
                    "app": "Code", "window": "window:7",
                    "coord_space": "pixels_topleft"}
    assert stat.S_IMODE(image.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "frames").stat().st_mode) == 0o700


def test_the_bank_keeps_only_the_newest(tmp_path):
    bank = GoldBank(tmp_path / "frames", every_ms=1, max_frames=3)
    for t in range(1, 7):
        bank.maybe_bank(_frame(), t * 1000, "Mail")
    kept = sorted(p.stem for p in (tmp_path / "frames").glob("*.jpg"))
    assert kept == ["live_4000", "live_5000", "live_6000"]
    assert len(list((tmp_path / "frames").glob("*.json"))) == 3


def test_a_broken_encoder_never_costs_the_eye_a_beat(tmp_path):
    bank = GoldBank(tmp_path / "frames")
    assert bank.maybe_bank(SimpleNamespace(width=0, height=0, bytes_per_row=0,
                                           data=b""), 0, "Mail") is None
    assert bank.errors == 1
