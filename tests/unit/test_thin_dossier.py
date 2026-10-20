"""One fact does not need summarising, and pretending otherwise apologises.

Everything memory held about Hana on 2026-08-24:

    [fc75e59c] Hana — a person, seen in 34 moment(s) on 2026-08-04
            on screen: Gardening tips with Hana

There is no summary of that which is not either those two lines again or an
invention. The product spent two model calls trying anyway, the guard threw
the result away — correctly — and the founder was shown:

    I could not summarise this without risking a claim the evidence does not
    support, so here is the evidence:

which reads as a malfunction. Nothing had malfunctioned. That is all anyone
knows about Hana, and saying so plainly is the best available answer.

The rule: synthesis earns its place by compressing or connecting several
facts. Below that it can only restate or embellish, and embellishing is what
the guard exists to catch.
"""
from __future__ import annotations

import pytest

from tracemem.app.hub import Hub


class Block:
    def __init__(self, text, source_ids=("abc12345",)):
        self.text = text
        self.source_ids = source_ids


class Context:
    def __init__(self, *blocks):
        self.blocks = list(blocks)


def _hub():
    return Hub.__new__(Hub)          # only the pure helpers are exercised


THIN = Context(Block("[abc12345] Hana — a person, seen in 34 moment(s)\n"
                     "        on screen: Gardening tips with Hana"))
FAT = Context(Block("\n".join("[abc12345] line %d" % i for i in range(9))))


def test_lines_are_counted_not_blocks():
    assert _hub()._evidence_lines(THIN) == 2
    assert _hub()._evidence_lines(FAT) == 9


def test_blank_lines_are_not_facts():
    padded = Context(Block("[abc12345] Hana — a person\n\n   \n"
                           "        on screen: Gardening tips"))
    assert _hub()._evidence_lines(padded) == 2


def test_a_thin_subject_is_not_an_apology():
    a = _hub()._evidence_only(THIN, refused=False)
    assert "could not summarise" not in a.text
    assert "not enough here to summarise" in a.text
    # The evidence itself is still all there — the framing changed, not the
    # content. This answer must never be thinner than the refusal fallback.
    assert "Gardening tips with Hana" in a.text
    assert a.citations == ("abc12345",)
    assert not a.abstained


def test_a_real_refusal_still_says_so():
    """The two must stay distinguishable. A guard refusal means something
    went wrong upstream and is worth investigating; a thin subject does not.
    Collapsing them would hide every future refusal inside a normal-looking
    answer."""
    a = _hub()._evidence_only(FAT, refused=True)
    assert "could not summarise" in a.text
    assert "not enough here" not in a.text


def test_refusal_is_still_the_default():
    """Every existing caller passes no flag and means 'the guard refused'."""
    assert "could not summarise" in _hub()._evidence_only(FAT).text


@pytest.mark.parametrize("n,short_circuits", [(1, True), (2, True),
                                              (3, False), (9, False)])
def test_where_the_line_sits(n, short_circuits):
    ctx = Context(Block("\n".join("[abc12345] fact %d" % i for i in range(n))))
    assert (_hub()._evidence_lines(ctx) <= Hub.THIN_DOSSIER_LINES) is \
        short_circuits


@pytest.mark.parametrize("mode,expected", [
    ("synthesis", True),
    ("needle", False),
])
def test_only_summaries_short_circuit(mode, expected):
    """A needle question over two lines is an ordinary needle question.

    Its answer is a sentence pulled OUT of those lines — "the kiln firing
    moved to Thursday" — and handing back the lines instead would be a
    regression on the path that already works best. This rule is about
    summaries, which need something to summarise.
    """
    assert Hub._show_directly(THIN, mode) is expected


def test_a_fat_dossier_always_goes_to_the_model():
    assert Hub._show_directly(FAT, "synthesis") is False
