"""Boot-time gaps: every silence explained, no ceremony for blips."""
from __future__ import annotations

from tracemem.app.gap_detector import detect_gap
from tracemem.app.witness import WitnessKind

H = 3_600_000


def test_empty_store_is_day_one_not_a_gap():
    assert detect_gap(None, None, now_ms=10 * H) is None


def test_short_blip_needs_no_ceremony():
    assert detect_gap(100_000, WitnessKind.CLEAN_STOP, now_ms=160_000) is None


def test_overnight_after_clean_stop_is_an_attributed_gap():
    w = detect_gap(0, WitnessKind.CLEAN_STOP, now_ms=9 * H)
    assert w.structured["witness"] == "gap"
    assert w.structured["cause"] == "clean-stop"
    assert w.structured["t2_ms"] == 9 * H


def test_nine_hours_with_no_goodbye_is_a_kill():
    w = detect_gap(0, WitnessKind.START, now_ms=9 * H)
    assert w.structured["witness"] == "kill-detected"
    assert w.structured["cause"] == "no-clean-stop"
    assert "9.0h" in w.text


def test_three_day_silence_even_after_clean_stop_is_flagged_overlong():
    w = detect_gap(0, WitnessKind.CLEAN_STOP, now_ms=72 * H)
    assert w.structured["cause"] == "overlong-clean-stop"
