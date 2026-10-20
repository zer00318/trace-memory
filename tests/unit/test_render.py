"""Spec 02 §3 oracles — deterministic rendering."""
import pytest

from tracemem.text.render import render


def test_fen_renders_deterministically():
    s = {"type": "chess.fen", "fen": "8/8/8/8/8/8/8/8 w - -", "to_move": "black"}
    assert render(s) == render(s)
    assert "FEN" in render(s) and "black" in render(s)

def test_unknown_type_raises():
    with pytest.raises(KeyError):
        render({"type": "interpretive-dance"})
