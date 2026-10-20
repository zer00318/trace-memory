"""A day is a few dozen MOMENTS, not thirty thousand fragments.

Every case here is measured off the founder's real store (2026-08-01..03),
because the failure being fixed was invisible to synthetic data: on a fixture
where each claim is a tidy sentence, the old node path looks fine. It only
collapses on the real shape — median OCR claim length 15 characters, 87% of
AX object claims being window furniture, and a url that rewrites itself while
the page does not change.
"""
import time

import pytest

from tracemem.domain import (CaptureCoord, Claim, Confidence, CoordinateStack,
                             Modality, Provenance, SpanCoord, WorldCoord)
from tracemem.domain.grading import grade_for
from tracemem.supplier import timeline

T0 = 1_785_000_000_000


def claim(text, t_ms, app="Brave Browser", ax=None, role=None, perceiver="ocr",
          window=None):
    return Claim(
        text=text,
        coord=CoordinateStack(world=WorldCoord(place_key="system"),
                              capture=CaptureCoord(frame_id="f", device_id="mac-1",
                                                   app=app, window=window),
                              span=SpanCoord(bbox=(0, 0, 0, 0)), t_ms=t_ms),
        provenance=Provenance(perceiver=perceiver, modality=Modality.SCREEN),
        grade=grade_for(perceiver), confidence=Confidence(1.0),
        structured={k: v for k, v in (("ax", ax), ("role", role)) if v},
        bidder="b", bid_id="b1", bid_predicted_value=1.0, actual_cost=0.0,
        scope="frame")


def test_the_ax_reading_gives_the_ocr_confetti_something_to_belong_to():
    """"kiometres oway" means nothing alone. Inside a moment whose title the
    AX sense read, it is evidence for what the founder was reading."""
    cs = [claim("Morning Jazz - YouTube", T0, ax="window_title", perceiver="ax"),
          claim("youtube.com/watch?v=nQrr", T0 + 1, ax="url", perceiver="ax")]
    cs += [claim("kiometres oway and more text here", T0 + 2000 + i, )
           for i in range(30)]
    (m,) = timeline.build(cs)
    assert m.title == "Morning Jazz - YouTube"
    assert m.url == "youtube.com/watch?v=nQrr"
    assert any("kiometres" in s for s in m.samples)


def test_browser_self_report_does_not_split_one_video_into_six_moments():
    """MEASURED: Brave appends "- Audio playing - High memory usage - 1.5 GB -
    Brave (Private)" to every title, and the RAM figure changes while the
    founder sits still. Keyed naively, one video became many moments."""
    titles = ["Weather Update - YouTube - Audio playing - High memory usage - 1.5 GB - Brave (Private)",
              "Weather Update - YouTube - Audio playing - High memory usage - 1.8 GB - Brave (Private)",
              "Weather Update - YouTube - High memory usage - 2.0 GB - Brave (Private)"]
    cs = []
    for i, t in enumerate(titles):
        cs.append(claim(t, T0 + i * 60_000, ax="window_title", perceiver="ax"))
        cs += [claim("some readable sentence %d" % j, T0 + i * 60_000 + j + 1)
               for j in range(25)]
    moments = timeline.build(cs)
    assert len(moments) == 1, [m.label() for m in moments]
    assert moments[0].label() == "Weather Update - YouTube"


def test_a_real_switch_is_still_two_moments():
    """The merge must not manufacture continuity across a genuine change."""
    cs = [claim("Chess - YouTube", T0, ax="window_title", perceiver="ax")]
    cs += [claim("a readable line about chess", T0 + i + 1) for i in range(25)]
    cs += [claim("Reddit - r/AskReddit", T0 + 600_000, ax="window_title",
                 perceiver="ax")]
    cs += [claim("a readable line about reddit", T0 + 600_001 + i) for i in range(25)]
    assert [m.label() for m in timeline.build(cs)] == [
        "Chess - YouTube", "Reddit - r/AskReddit"]


def test_window_furniture_is_never_quoted_as_evidence():
    """AXButton labels are 64% of the AX volume and say nothing about the
    founder's life. They are RANKED OUT, not deleted — raw is immutable and
    every gate refuses to promote, never to record (DISPATCH)."""
    cs = [claim("Weather Update - YouTube", T0, ax="window_title", perceiver="ax")]
    cs += [claim("Bookmark this tab and more", T0 + i + 1, ax="object",
                 role="AXButton", perceiver="ax") for i in range(30)]
    (m,) = timeline.build(cs)
    assert m.samples == []


def test_a_witness_claim_is_not_a_moment():
    cs = [claim("[witness:organ-restart] eye", T0, perceiver="witness")]
    cs[0].structured["witness"] = "organ_restart"
    cs += [claim("real content line here", T0 + 1 + i) for i in range(25)]
    moments = timeline.build(cs)
    assert all("witness" not in m.label() for m in moments)


def test_the_moment_carries_a_grade_from_the_table_not_a_literal():
    """The moment's grade is its strongest witness's, taken from
    domain/grading.py.

    NOTE, and it is deliberate rather than an oversight: `ax` and `ocr` BOTH
    earn VERBATIM, so this cannot express "the AX read is better". The grade
    table says why in its own comment — VERBATIM is the ceiling and grade
    measures FIDELITY, not the absence of a failure mode, so where the two
    disagree the tie is broken by PRECEDENCE at birth. The timeline honours
    that precedence structurally instead: `title` and `url` are only ever
    taken from an AX reading, so `chess.com` can never arrive as `esa.com`
    in a moment's label however confident the OCR was.
    """
    cs = [claim("Real Title", T0, ax="window_title", perceiver="ax")]
    cs += [claim("mangled ocr fragmnt here", T0 + i + 1) for i in range(25)]
    (m,) = timeline.build(cs)
    assert m.grade == grade_for("ax")


def test_a_moment_label_never_comes_from_ocr():
    """PRECEDENCE, the thing the grade scale structurally cannot say. OCR of
    a tab strip yields `esa.com` for `chess.com` (measured: 13 occurrences vs
    2 in the real store). A label built from that is a durable lie, so labels
    come only from the AX read."""
    cs = [claim("esa.com is what OCR thought it saw", T0 + i) for i in range(30)]
    (m,) = timeline.build(cs)
    assert m.title is None and m.url is None
    assert m.label() == "Brave Browser"


def test_render_is_cited_and_time_ordered():
    cs = []
    for i, t in enumerate(["First - YouTube", "Second - YouTube"]):
        cs.append(claim(t, T0 + i * 600_000, ax="window_title", perceiver="ax"))
        cs += [claim("a readable line %d here" % j, T0 + i * 600_000 + j + 1)
               for j in range(25)]
    text = timeline.render(timeline.build(cs))
    assert text.index("First") < text.index("Second")
    assert text.count("[") >= 2   # every moment carries an id to cite


def test_two_visits_across_a_gap_are_two_moments_not_one_long_one():
    """A MOMENT MUST NOT SWALLOW BLINDNESS.

    Measured on 2026-08-04 — a day the digest reports as "watching 29% of
    this period, blind for 11 intervals" — the merge joined runs hours apart
    whenever nothing else was recorded in between, which is precisely what a
    capture gap looks like from the claim stream. Eight moments summed to 22
    hours and one claimed 392 continuous minutes on a single video. The
    memory would have reported a hole it KNEW about as attention, two lines
    above its own honest coverage number.
    """
    cs = [claim("Same Video - YouTube", T0, ax="window_title", perceiver="ax")]
    cs += [claim("a readable line here %d" % i, T0 + i + 1) for i in range(25)]
    # ...four hours of nothing, then the same page again.
    later = T0 + 4 * 3600_000
    cs += [claim("Same Video - YouTube", later, ax="window_title", perceiver="ax")]
    cs += [claim("another readable line %d" % i, later + i + 1) for i in range(25)]

    moments = timeline.build(cs)
    assert len(moments) == 2, [m.duration_ms for m in moments]
    assert all(m.duration_ms < 60_000 for m in moments), (
        "a moment absorbed the four-hour gap")


def test_geometry_attributed_ocr_keeps_the_window_ax_actually_read():
    """MEASURED over fifteen live minutes: 1,742 of 2,215 claims carried
    `app=Claude` from OCR while the AX sense reported the focused window as
    other apps (Finder, a chat app, a browser, Python). The answer that came
    out of that said "Claude showed Trash with…", which is false.

    An OCR claim's app is `attention_span()` — the FRONTMOST app when the
    pixels were grabbed. The pixels are a tile of the whole screen and may
    belong to a window that is not frontmost, so the stamp is a guess about
    ownership. The AX read is the OS naming the focused window, with no
    guessing step. Same precedence the site tier already uses.
    """
    cs = [claim("Downloads", T0, ax="window_title", perceiver="ax",
                app="Finder", window="window:7")]
    cs += [claim("a readable line of text %d" % i, T0 + i + 1,
                 app="Finder", window="window:7")
           for i in range(25)]
    (m,) = timeline.build(cs)
    assert m.app == "Finder"


def test_app_or_window_change_ends_old_url_context_before_next_ax_refresh():
    cs = [claim("mail.example/inbox", T0, ax="url", perceiver="ax",
                app="Browser", window="window:7")]
    cs += [claim("a readable line of text %d" % i, T0 + i + 1,
                 app="Editor", window="window:8") for i in range(25)]
    moments = timeline.build(cs)
    editor = [m for m in moments if m.app == "Editor"][0]
    assert editor.url is None and editor.title is None


def test_ocr_still_names_the_app_when_nothing_was_read():
    """The guess is not worthless — it is only outranked. With no AX reading
    at all it is the only thing there is."""
    cs = [claim("a readable line of text %d" % i, T0 + i, app="Preview")
          for i in range(25)]
    (m,) = timeline.build(cs)
    assert m.app == "Preview"
