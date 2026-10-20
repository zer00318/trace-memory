"""Answering about a STRETCH OF TIME, and refusing honestly when blind.

The bug this path exists to fix, measured on the founder's live daemon
2026-08-20: "What was I doing on August 3?" returned `not in memory` after
101 seconds, about a day holding 29,250 claims. Retrieval had entered through
the node graph, which holds no time.
"""
import pytest

from tracemem.app.witness import WitnessKind
from tracemem.domain import (CaptureCoord, Claim, Confidence, CoordinateStack,
                             Modality, Provenance, SpanCoord, WorldCoord)
from tracemem.domain.grading import grade_for
from tracemem.llm.answer import parse_answer
from tracemem.supplier import timeline
from tracemem.supplier.time_phrases import Range
from tracemem.supplier.window import supply_window

T0 = 1_785_000_000_000
DAY = 86_400_000


def _claim(text, t_ms, ax=None, perceiver="ocr", structured=None):
    st = dict(structured or {})
    if ax:
        st["ax"] = ax
    return Claim(
        text=text,
        coord=CoordinateStack(world=WorldCoord(place_key="system"),
                              capture=CaptureCoord(frame_id="f",
                                                   device_id="mac-1",
                                                   app="Brave Browser"),
                              span=SpanCoord(bbox=(0, 0, 0, 0)), t_ms=t_ms),
        provenance=Provenance(perceiver=perceiver, modality=Modality.SCREEN),
        grade=grade_for(perceiver), confidence=Confidence(1.0), structured=st,
        bidder="b", bid_id="b1", bid_predicted_value=1.0, actual_cost=0.0,
        scope="frame")


class FakeLog:
    def __init__(self, claims):
        self._claims = sorted(claims, key=lambda c: c.coord.t_ms)

    def read_range(self, t0, t1, place_key=None):
        return [c for c in self._claims if t0 <= c.coord.t_ms <= t1]

    def ids_in_range(self, t0, t1, place_key=None):
        return [c.id for c in self.read_range(t0, t1)]


def _day(n=30):
    cs = [_claim("Weather Update - YouTube", T0, ax="window_title", perceiver="ax"),
          _claim("youtube.com/watch?v=38l5", T0 + 1, ax="url", perceiver="ax")]
    cs += [_claim("a readable sentence number %d" % i, T0 + 1000 + i * 1000)
           for i in range(n)]
    return cs


def test_a_day_of_claims_becomes_a_cited_timeline():
    ctx = supply_window(FakeLog(_day()), Range(T0 - 1, T0 + DAY, "august 3", False), 6000)
    assert ctx.blocks
    body = "\n".join(b.text for b in ctx.blocks)
    assert "Weather Update - YouTube" in body


def test_every_rendered_id_is_a_citation_the_guard_can_check():
    """THE PREFIX TRAP. The render puts [375ebf95] in the text; if source_ids
    held full UUIDs the model's correct citation would match nothing,
    `citations` would come back empty, and the never-lie guard — which only
    verifies answers that HAVE citations — would wave it through."""
    ctx = supply_window(FakeLog(_day()), Range(T0 - 1, T0 + DAY, "august 3", False), 6000)
    rendered = [b for b in ctx.blocks if b.kind == "moment"]
    assert rendered
    for b in rendered:
        for sid in b.source_ids:
            assert len(sid) == timeline.CITE_LEN
        assert "[%s]" % b.source_ids[0] in b.text
    answer = parse_answer("I watched a video [%s]." % rendered[0].source_ids[0], ctx)
    assert answer.citations == (rendered[0].source_ids[0],)
    assert answer.invented == ()


def test_an_invented_citation_is_shown_as_unsupported_not_quietly_dropped():
    ctx = supply_window(FakeLog(_day()), Range(T0 - 1, T0 + DAY, "august 3", False), 6000)
    a = parse_answer("You watched the surgical strike [start_ofc].", ctx)
    assert a.invented == ("start_ofc",)
    assert "[UNSUPPORTED]" in a.text
    assert "start_ofc" not in a.text


def test_an_unwatched_window_says_so_instead_of_saying_nothing_happened():
    """"nothing happened" and "I wasn't watching" are different answers and
    only one of them is ever true."""
    ctx = supply_window(FakeLog([]), Range(T0, T0 + DAY, "august 3", False), 6000)
    body = " ".join(b.text for b in ctx.blocks).lower()
    assert "no perception" in body
    assert all(b.kind == "coverage" for b in ctx.blocks)


def test_a_partly_blind_window_carries_its_coverage_caveat():
    gap = _claim("[witness:gap]", T0 + 1000, perceiver="witness",
                 structured={"witness": WitnessKind.GAP.value,
                             "t2_ms": T0 + DAY // 2, "cause": "daemon killed"})
    ctx = supply_window(FakeLog(_day() + [gap]),
                        Range(T0, T0 + DAY, "august 3", False), 6000)
    body = " ".join(b.text for b in ctx.blocks)
    assert "COVERAGE" in body and "watching" in body


def test_dropped_moments_are_declared_never_silently_truncated():
    """A timeline that quietly omits half a day reads as a whole day, and the
    model will summarise it as one.

    Moments are capped by COUNT as well as by budget since 2026-08-20:
    twenty short moments fit the token budget easily and still ruined the
    answer, because the model walked all twenty and produced a transcript.
    Either kind of truncation must be declared.
    """
    cs = []
    for i in range(60):
        cs.append(_claim("Video %d - YouTube" % i, T0 + i * 600_000,
                         ax="window_title", perceiver="ax"))
        cs += [_claim("a readable sentence here %d" % j,
                      T0 + i * 600_000 + j + 1) for j in range(25)]
    ctx = supply_window(FakeLog(cs), Range(T0, T0 + DAY, "today", False), 400)
    body = " ".join(b.text for b in ctx.blocks)
    assert "are not shown" in body
