"""The eye stores what is new and spends what it saves on detail.

Founder ruling 2026-10-20: no re-reads; capture new details instead.
Measured 2026-10-15: 52% of the eye's OCR claims repeated text it had
stored from the same app within ten minutes.
"""
from __future__ import annotations

from types import SimpleNamespace

from tests.helpers import make_claim
from tracemem.extraction.auction import Auction
from tracemem.extraction.diet import DietRegistry
from tracemem.extraction.seen_text import (PRESENCE_EVERY_MS,
                                           REREAD_HORIZON_MS, ReadEconomy,
                                           SeenText)
from tracemem.supplier import timeline

MIN = 60_000


def _c(text, t, app="Brave", window="window:1"):
    claim = make_claim(text, t_ms=t, frame="f-%d" % t, app=app,
                       bidder="peripheral:change")
    cap = claim.coord.capture
    object.__setattr__(cap, "window", window)
    return claim


def test_a_reread_of_the_same_window_is_not_stored_again():
    seen = SeenText()
    fresh, rep = seen.split([_c("Inbox (3)", 0), _c("Weekly report", 0)], 0)
    assert len(fresh) == 2 and not rep
    fresh, rep = seen.split([_c("inbox  (3)!", 2000), _c("New message", 2000)], 2000)
    assert [c.text for c in fresh] == ["New message"]
    assert [c.text for c in rep] == ["inbox  (3)!"]


def test_another_window_is_its_own_page():
    seen = SeenText()
    seen.split([_c("Inbox (3)", 0)], 0)
    fresh, _ = seen.split([_c("Inbox (3)", 1000, window="window:2")], 1000)
    assert len(fresh) == 1


def test_text_that_stays_on_screen_is_stored_once_however_long_it_stays():
    seen = SeenText()
    seen.split([_c("Chapter 4", 0)], 0)
    for k in range(1, 6):                    # re-sighted every 20 minutes
        t = k * 20 * MIN
        fresh, _ = seen.split([_c("Chapter 4", t)], t)
        assert not fresh, "a continuous sighting must not re-store"


def test_text_that_comes_back_after_the_horizon_is_a_new_sighting():
    seen = SeenText()
    seen.split([_c("Chapter 4", 0)], 0)
    t = REREAD_HORIZON_MS + 1
    fresh, _ = seen.split([_c("Chapter 4", t)], t)
    assert len(fresh) == 1


def test_detail_goes_where_the_eye_has_read_least():
    econ = ReadEconomy((3, 2))
    for cell in [(0, 0), (1, 0), (2, 0), (0, 1), (1, 1)]:
        econ.looked("Brave", cell, 1000)
    assert econ.next_detail("Brave", (0, 0)) == (2, 1)       # never read
    econ.looked("Brave", (2, 1), 2000)
    assert econ.next_detail("Brave", (0, 0)) == (1, 0)       # oldest, not self
    assert econ.next_detail("Code", (0, 0)) == (1, 0)        # per app


def _eye():
    from tracemem.app.eye_organ import EyeOrgan
    eye = EyeOrgan(submit=lambda c: True)
    eye._auction = Auction(DietRegistry(), prepare=lambda r: r)
    return eye


def test_a_look_that_found_nothing_new_says_presence_once_and_seeks_detail():
    eye = _eye()
    gaze = SimpleNamespace(region=(4, 4))
    rec = lambda t: SimpleNamespace(t_ms=t)                 # noqa: E731

    fresh, presence = eye._only_what_is_new([_c("Inbox (3)", 0)], gaze, "Brave", rec(0))
    assert len(fresh) == 1 and presence == []
    assert eye.detail_bids == 1, "detail is sought after every look, not only empty ones"

    fresh, presence = eye._only_what_is_new([_c("Inbox (3)", 2000)], gaze, "Brave", rec(2000))
    assert fresh == [] and presence == [], "presence is owed only after a minute of silence"
    assert eye.detail_bids == 1 and eye._auction.has_bid("eye:detail")

    t = PRESENCE_EVERY_MS + 1
    fresh, presence = eye._only_what_is_new([_c("Inbox (3)", t)], gaze, "Brave", rec(t))
    assert len(presence) == 1 and presence[0].structured["presence"] is True
    assert "Inbox" not in presence[0].text, "presence carries no screen text"
    assert eye.detail_bids == 1, "one outstanding detail bid at a time"
    assert eye.repeats_suppressed == 2


def test_presence_keeps_a_long_stretch_one_moment_without_becoming_a_sample():
    claims = [_c("Lecture 7 - Thermodynamics of mixing", 0)]
    t = 0
    while t < 30 * MIN:
        t += PRESENCE_EVERY_MS
        p = _c("Brave still in front", t)
        object.__setattr__(p, "structured", {"presence": True})
        claims.append(p)
    moments = timeline.build(claims)
    assert len(moments) == 1 and moments[0].duration_ms >= 29 * MIN
    assert all("still in front" not in s for s in moments[0].samples)


def _ax(text, t, kind, window="w1", app="Brave"):
    from tracemem.capture.ax_claims import claims_from_reading
    reading = {"app": app, "window_id": window}
    if kind == "title":
        reading["window_title"] = text
    elif kind == "url":
        reading["url"] = text
    else:
        reading["objects"] = [{"label": text, "role": "AXButton"}]
    return claims_from_reading(reading, t)


def test_window_titles_are_stored_on_change_not_on_every_read():
    """Measured 2026-10-20: 28,226 title-sense claims in 30 min, 99.1% repeats."""
    from tracemem.extraction.seen_text import CONTEXT_REASSERT_MS, AxChanges
    ax = AxChanges()
    fresh, _ = ax.split(_ax("Inbox - Mail", 0, "title") + _ax("Reply", 0, "obj"), 0)
    assert len(fresh) == 2
    for t in range(5000, 60000, 5000):                       # every read for a minute
        fresh, rep = ax.split(_ax("Inbox - Mail", t, "title") + _ax("Reply", t, "obj"), t)
        assert fresh == [] and len(rep) == 2
    fresh, _ = ax.split(_ax("Draft - Mail", 61000, "title"), 61000)
    assert len(fresh) == 1, "a changed title is news"
    t = 61000 + CONTEXT_REASSERT_MS
    fresh, _ = ax.split(_ax("Draft - Mail", t, "title"), t)
    assert len(fresh) == 1, "context is re-asserted so any question's range holds it"



def test_a_fully_read_screen_stops_seeking_detail():
    econ = ReadEconomy((2, 1))
    econ.looked("Brave", (0, 0), 1000)
    econ.looked("Brave", (1, 0), 1000)
    assert econ.stale_cell("Brave", (0, 0), 2000) is None
    assert econ.stale_cell("Brave", (0, 0), 1000 + REREAD_HORIZON_MS) == (1, 0)
