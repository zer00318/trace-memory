"""ORACLE for the context-gap metric core (tools/context_gap.py).

The full run needs the embedder and gold frames, so it is not in the suite.
What is here is the matching logic — the part that, if wrong, would make the
headline number lie. It is tested with a DETERMINISTIC stub embedder so the
recall arithmetic is checked without a model.

The point of this file is that the gap number cannot silently drift. It is
the product's north-star metric; a metric nobody can test is a vibe.
"""
import math

import pytest

from tools.context_gap import TIERS, _cos, frame_recall


def test_cosine_is_one_for_identical_and_zero_for_orthogonal():
    assert _cos([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)
    assert _cos([1, 0, 0], [0, 1, 0]) == pytest.approx(0.0)
    assert _cos([1, 1], [1, 1]) == pytest.approx(1.0)


def test_cosine_handles_a_zero_vector_without_dividing_by_zero():
    assert _cos([0, 0], [1, 1]) == 0.0


class OneHot:
    """A stub embedder: each distinct word gets its own axis, so two texts
    are 'similar' exactly when they share words. Deterministic, no model."""

    def __init__(self):
        self.axes = {}

    def __call__(self, text):
        words = text.lower().split()
        vec = [0.0] * 64
        for w in words:
            self.axes.setdefault(w, len(self.axes))
            vec[self.axes[w] % 64] += 1.0
        return vec


def test_a_claim_present_verbatim_is_recalled():
    embed = OneHot()
    claims = [{"kind": "verbatim", "text": "the kiln fires thursday"}]
    frags = ["the kiln fires thursday", "unrelated words here"]
    our = [embed(f) for f in frags]
    whole = embed(" ".join(frags))
    got = frame_recall(claims, our, whole, embed, (0.55,))
    assert got[0.55]["verbatim"] == [1, 1]


def test_a_claim_absent_from_our_context_is_not_recalled():
    embed = OneHot()
    claims = [{"kind": "inference", "text": "the user is asleep"}]
    frags = ["a spreadsheet of quarterly revenue"]
    our = [embed(f) for f in frags]
    whole = embed(" ".join(frags))
    got = frame_recall(claims, our, whole, embed, (0.55,))
    assert got[0.55]["inference"] == [0, 1]


def test_recall_is_counted_per_tier():
    embed = OneHot()
    claims = [
        {"kind": "verbatim", "text": "hello world"},       # present
        {"kind": "object", "text": "a red button"},        # absent
        {"kind": "activity", "text": "editing code now"},  # present
    ]
    frags = ["hello world", "editing code now"]
    our = [embed(f) for f in frags]
    whole = embed(" ".join(frags))
    got = frame_recall(claims, our, whole, embed, (0.55,))
    assert got[0.55]["verbatim"] == [1, 1]
    assert got[0.55]["object"] == [0, 1]
    assert got[0.55]["activity"] == [1, 1]


def test_the_whole_frame_text_can_rescue_a_match():
    """The generous bound: a claim that no single fragment matches can still
    be recalled against the concatenated frame text. This is deliberate and
    must actually work, or the 'generous' label is false."""
    embed = OneHot()
    claims = [{"kind": "inference", "text": "alpha beta gamma delta"}]
    # No single fragment has all four words; together they do.
    frags = ["alpha beta", "gamma delta"]
    our = [embed(f) for f in frags]
    whole = embed(" ".join(frags))
    per_frag_best = max(_cos(embed(claims[0]["text"]), v) for v in our)
    assert per_frag_best < 1.0                         # no fragment is a full match
    got = frame_recall(claims, our, whole, embed, (0.55,))
    assert got[0.55]["inference"][0] == 1              # the whole rescued it


def test_unknown_kinds_are_ignored():
    embed = OneHot()
    claims = [{"kind": "witness", "text": "system started"}]
    got = frame_recall(claims, [embed("x")], embed("x"), embed, (0.55,))
    assert all(got[0.55][t] == [0, 0] for t in TIERS)


def test_every_tier_is_represented_in_the_result():
    embed = OneHot()
    got = frame_recall([], [embed("x")], embed("x"), embed, (0.55, 0.70))
    for bar in (0.55, 0.70):
        assert set(got[bar]) == set(TIERS)
