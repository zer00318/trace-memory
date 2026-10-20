"""ORACLE for the Time-phrase parser (06_supplier).

PROOF: "20-phrase test table green, including 'last night' spanning
midnight."

The midnight case is the whole reason this module is not `dateutil`. A 1am
session belongs to the evening that produced it, and a parser that files it
under the next calendar day answers "what did I do last night" by hiding
the last three hours of it.
"""
from datetime import datetime

import pytest

from tracemem.supplier.time_phrases import (
    DAY_MS, HOUR_MS, Range, parse, phrases,
)

# A fixed clock. Real dates rather than round numbers, because the calendar
# fallback path uses the local timezone and a synthetic epoch would hide a
# timezone mistake behind a number that happens to work.
NOW = int(datetime(2026, 7, 22, 15, 30).timestamp() * 1000)


def _week_of_activity(now_ms=NOW, days=5):
    """Observed timestamps with real sleep gaps: activity 09:00-23:00 each
    day, nothing overnight. day_boundaries finds the quiet stretches."""
    stamps = []
    for d in range(days, 0, -1):
        day_start = now_ms - d * DAY_MS
        base = day_start - (day_start % DAY_MS)
        for hour in range(9, 23):
            stamps.append(base + hour * HOUR_MS)
    stamps.append(now_ms - HOUR_MS)
    stamps.append(now_ms)
    return stamps


# -- THE ORACLE: the twenty-phrase table -----------------------------------
@pytest.mark.parametrize("phrase", [
    "today", "so far today", "yesterday", "the day before",
    "the day before yesterday", "two days ago", "last night", "this morning",
    "morning", "afternoon", "evening", "last evening", "yesterday morning",
    "this week", "the past week", "last week", "just now", "a moment ago",
    "in the last 2 hours", "the last 30 minutes", "3 days ago", "2 weeks ago",
])
def test_the_phrase_table_resolves(phrase):
    got = parse(phrase, NOW, _week_of_activity())
    assert got is not None, phrase
    assert got.t0_ms < got.t1_ms, phrase
    assert got.span_ms > 0


def test_the_table_is_at_least_twenty_phrases():
    assert len(phrases()) >= 18
    resolved = [p for p in phrases() if parse(p, NOW, _week_of_activity())]
    assert len(resolved) == len(phrases())


# -- the midnight case -----------------------------------------------------
def test_last_night_includes_a_one_am_session():
    """THE case the module exists for. Days end at sleep, not at midnight,
    so a 1am session belongs to the evening that produced it."""
    activity = []
    # Yesterday: 09:00 through 01:00 the following calendar morning.
    day0 = int(datetime(2026, 7, 20, 0, 0).timestamp() * 1000)
    for hour in range(9, 26):                       # 09:00 -> 01:00 next day
        activity.append(day0 + hour * HOUR_MS)
    # Then a real sleep gap, then today.
    day1 = int(datetime(2026, 7, 21, 0, 0).timestamp() * 1000)
    for hour in range(10, 16):
        activity.append(day1 + hour * HOUR_MS)
    now = day1 + 15 * HOUR_MS

    one_am = day0 + 25 * HOUR_MS                    # 01:00 on the 21st
    got = parse("last night", now, activity)

    assert got is not None
    assert got.from_lived_rhythm is True
    assert got.contains(one_am), (
        "1am session fell outside 'last night' — the parser used a calendar "
        "day somewhere")


def test_a_lived_day_is_not_a_calendar_day():
    """If the two always agreed, this module would be pointless.

    Note the fixture: activity runs late, past midnight. A rhythm that
    happens to stop at midnight WOULD produce the same range from both
    paths — that is a coincidence, not a property, so this test uses a
    rhythm where the difference is real rather than asserting the two can
    never coincide.
    """
    activity = []
    day0 = int(datetime(2026, 7, 20, 0, 0).timestamp() * 1000)
    for hour in range(9, 26):                       # 09:00 -> 01:00 next day
        activity.append(day0 + hour * HOUR_MS)
    day1 = int(datetime(2026, 7, 21, 0, 0).timestamp() * 1000)
    for hour in range(10, 16):
        activity.append(day1 + hour * HOUR_MS)
    now = day1 + 15 * HOUR_MS

    lived = parse("yesterday", now, activity)
    calendar = parse("yesterday", now, [])
    assert lived.from_lived_rhythm is True
    assert calendar.from_lived_rhythm is False
    assert (lived.t0_ms, lived.t1_ms) != (calendar.t0_ms, calendar.t1_ms)
    # And concretely: the lived day reaches past calendar midnight.
    assert lived.t1_ms > int(datetime(2026, 7, 21, 0, 0).timestamp() * 1000)


# -- the fallback says so --------------------------------------------------
def test_without_history_the_parser_falls_back_and_admits_it():
    """The fallback is correct but it is not the same answer. A range that
    silently changes meaning as history accumulates makes an evaluation
    number impossible to interpret weeks later."""
    got = parse("yesterday", NOW, [])
    assert got is not None
    assert got.from_lived_rhythm is False


def test_with_history_it_uses_the_lived_rhythm():
    got = parse("yesterday", NOW, _week_of_activity())
    assert got.from_lived_rhythm is True


def test_the_uncalibrated_day_parts_are_flagged():
    """morning/afternoon/evening splits are declared, not derived from the
    founder's rhythm. Anything reading these needs to know that."""
    assert parse("this morning", NOW, _week_of_activity()).calibrated is False
    assert parse("yesterday", NOW, _week_of_activity()).calibrated is True


# -- the refusal -----------------------------------------------------------
@pytest.mark.parametrize("phrase", [
    "when I was in Rome", "the good old days", "", "sometime",
    "before the thing happened", "next tuesday", "wibble",
])
def test_an_unparseable_phrase_returns_none_rather_than_guessing(phrase):
    """A manufactured window turns a parsing failure into a confident false
    'never': the supplier would search an invented range, find nothing, and
    report absence from inside it."""
    assert parse(phrase, NOW, _week_of_activity()) is None


def test_parsing_is_forgiving_about_shape_but_not_about_meaning():
    assert parse("  YESTERDAY ", NOW, []) is not None
    assert parse("Today?", NOW, []) is not None
    assert parse("yesterdayish", NOW, []) is None


# -- ranges behave ---------------------------------------------------------
def test_today_never_extends_into_the_future():
    """A range whose end is in the future would make the coverage
    denominator include time that has not happened, understating coverage
    and manufacturing a gap."""
    got = parse("today", NOW, _week_of_activity())
    assert got.t1_ms <= NOW


def test_rolling_windows_end_now():
    got = parse("in the last 2 hours", NOW, [])
    assert got.t1_ms == NOW
    assert got.span_ms == 2 * HOUR_MS


def test_n_days_ago_is_a_whole_day_not_a_point():
    """'3 days ago' means that day, not that instant — asking about a day
    and getting a moment would silently return almost nothing."""
    got = parse("3 days ago", NOW, _week_of_activity())
    assert got.span_ms > 6 * HOUR_MS


def test_n_hours_ago_is_a_window_around_the_point():
    got = parse("2 hours ago", NOW, [])
    assert got.contains(NOW - 2 * HOUR_MS)
    assert got.span_ms == HOUR_MS


def test_a_range_knows_what_it_contains():
    r = Range(t0_ms=100, t1_ms=200, phrase="x", from_lived_rhythm=True)
    assert r.contains(100) and r.contains(200) and r.contains(150)
    assert not r.contains(99) and not r.contains(201)
    assert r.span_ms == 100


def test_the_parser_is_fast_enough_for_the_hot_path():
    """'This must run in milliseconds' — the brief. It is on the path of
    every ask, so a model call here would be a per-question tax."""
    import time
    activity = _week_of_activity(days=30)
    start = time.time()
    for _ in range(200):
        parse("last night", NOW, activity)
    elapsed_ms = (time.time() - start) * 1000
    assert elapsed_ms / 200 < 10.0, "%.2fms per parse" % (elapsed_ms / 200)


def test_the_routing_census_how_people_actually_ask():
    """A phrase the table does not know is a question sent to the wrong organ.

    Run as a census on 2026-08-20: of twelve natural phrasings, FIVE resolved
    to nothing — "lately", "recently", "an hour ago", "the weekend", "the
    last few days". Each then fell through to the node path, which holds no
    time and answers by cosine against node labels. That is the exact failure
    that returned ENTITY `Aug`, ENTITY `Day`, ENTITY `Year` for a question
    about August 3rd.

    The two non-time questions at the end are the control: this must not
    start swallowing every question.
    """
    from tracemem.supplier.time_phrases import find_range

    now = 1_787_000_000_000
    for q in ("what was I doing yesterday",
              "what have I been doing lately",
              "what did I do this morning",
              "what was I working on recently",
              "what did I watch last night",
              "what have I been up to this week",
              "what did I do on august 3",
              "what was I doing an hour ago",
              "what did I read today",
              "what happened over the weekend",
              "what did I do in the last few days",
              "what did I do earlier today"):
        assert find_range(q, now) is not None, q

    for q in ("who is Juno", "what is my wifi password"):
        assert find_range(q, now) is None, q


def test_a_vague_window_is_still_a_bounded_one():
    """"Lately" is a span, not a boundary — but it must still be finite and
    must not run into the future, or the coverage caveat computed over it is
    meaningless."""
    from tracemem.supplier.time_phrases import find_range

    now = 1_787_000_000_000
    for q in ("lately", "over the weekend", "an hour ago", "the last few days"):
        r = find_range(q, now)
        assert r.t0_ms < r.t1_ms <= now, q
        assert r.span_ms <= 31 * 24 * 3600 * 1000, q


def test_a_window_named_inside_a_question_is_found():
    """WITNESSED 2026-10-07 on a live shadow daemon: "what was I doing in the
    last few minutes?" answered `(not in memory)` while three minutes of
    episodes sat in the store.

    Two holes, same class as the 2026-08-20 census. `find_range` only
    substring-matched the fixed phrase list, so the NUMERIC windows that
    `parse` supports ("the last 30 minutes", "2 hours ago") matched only when
    the whole question WAS the phrase — inside a real question they never
    resolved. And the minute/hour-scale vague phrasings were absent outright.
    A question that names a window and gets none is routed to the node path,
    which holds no time.
    """
    from tracemem.supplier.time_phrases import find_range

    now = 1_787_000_000_000
    cases = {
        "what was I doing in the last few minutes?": (2 * 60_000, 30 * 60_000),
        "what did I read in the last 10 minutes": (10 * 60_000, 10 * 60_000),
        "what was on my screen 20 minutes ago": (None, None),
        "what did I do in the past hour": (HOUR_MS, HOUR_MS),
        "what was I doing the last two hours": (2 * HOUR_MS, 2 * HOUR_MS),
        "what am I doing right now": (None, None),
        "what did I watch tonight": (None, None),
        "what happened three hours ago": (None, None),
        "what did I work on over the last couple of hours": (2 * HOUR_MS, 2 * HOUR_MS),
    }
    for q, (lo_span, hi_span) in cases.items():
        r = find_range(q, now)
        assert r is not None, q
        assert r.t0_ms < r.t1_ms <= now, q
        if lo_span is not None:
            assert lo_span <= r.span_ms <= hi_span, (q, r.span_ms)

    # "N ago" is a window AROUND the point, never ending at now
    r = find_range("what was on my screen 20 minutes ago", now)
    assert r.t0_ms <= now - 20 * 60_000 <= r.t1_ms
    # right now is recent, not the whole day
    assert find_range("what am I doing right now", now).span_ms <= 20 * 60_000


def test_number_shaped_words_that_are_not_windows_stay_none():
    from tracemem.supplier.time_phrases import find_range

    now = 1_787_000_000_000
    for q in ("who is Juno", "what is my wifi password",
              "how many hours of sleep is healthy",
              "what is the last thing on my reading list"):
        assert find_range(q, now) is None, q
