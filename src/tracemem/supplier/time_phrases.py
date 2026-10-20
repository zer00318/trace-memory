"""OWNS: relative time phrases → concrete ranges (map: Supplier /
Time-phrase parser, Range retrieval).

Table-driven and deliberately dumb. No model call: this runs on the hot
path of every ask, and a phrase like "yesterday" does not need a neural
network to become two integers. Milliseconds is the budget.

THE ONE IDEA THAT MAKES THIS DIFFERENT FROM `dateutil`:

    days end where the person slept, not where the clock rolled over.

"Last night" must include a 1am session. Calendar midnight would cut that
session away from the evening that produced it and file it under the next
day, which is wrong in the only way that matters — it answers "what did I
do last night" by hiding the last three hours of it. So every phrase here
resolves against `binder.event_segmenter.day_boundaries`, which finds the
long quiet gaps in actual observed activity.

THE FALLBACK IS THE DANGEROUS PART, and it is where this module is most
careful. With too little history there are no observed boundaries, and the
parser has to fall back to calendar days. That fallback is CORRECT but it
is not the same answer, so a resolved range carries `from_lived_rhythm`
saying which it was. A range that quietly changes meaning as history
accumulates, with nothing in the output to say so, is exactly the kind of
silent drift that makes an evaluation number impossible to interpret six
weeks later.

AN UNPARSEABLE PHRASE RETURNS None. It does not guess "probably today".
The supplier's job when it cannot pin a range is to search without one, not
to invent a window and then confidently report absence from inside it —
that would manufacture a false "never" out of a parsing failure.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Sequence

from tracemem.binder.event_segmenter import day_boundaries

HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS

# Where "morning"/"afternoon"/"evening"/"night" sit inside a lived day,
# as fractions of it. Fractions rather than clock times because a lived day
# is not 24 hours: it runs from wake to sleep, and someone whose day starts
# at 11am has a morning that is not 06:00-12:00.
#
# UNCALIBRATED (2026-07-22): these splits are declared, not derived from the
# founder's actual rhythm. The observed-boundary data to derive them will
# exist after real week #1; until then they are a reasonable guess and are
# labeled as one in `DayPart.calibrated`.
_DAY_PARTS = {
    "morning": (0.00, 0.33),
    "afternoon": (0.33, 0.66),
    "evening": (0.66, 1.00),
    "night": (0.75, 1.00),
}


@dataclass(frozen=True)
class Range:
    """A resolved window, plus how it was resolved.

    `from_lived_rhythm` is not decoration: the same phrase resolves to
    different milliseconds depending on whether observed sleep boundaries
    were available, and a consumer comparing two runs needs to know which
    it got.
    """
    t0_ms: int
    t1_ms: int
    phrase: str
    from_lived_rhythm: bool
    calibrated: bool = True

    @property
    def span_ms(self) -> int:
        return self.t1_ms - self.t0_ms

    def contains(self, t_ms: int) -> bool:
        return self.t0_ms <= t_ms <= self.t1_ms


def _calendar_day_start(t_ms: int) -> int:
    dt = datetime.fromtimestamp(t_ms / 1000.0)
    midnight = datetime(dt.year, dt.month, dt.day)
    return int(midnight.timestamp() * 1000)


def _lived_day_bounds(now_ms: int, activity: Sequence[int]):
    """(start, end) of the lived day containing now_ms, and whether it came
    from observed rhythm."""
    bounds = day_boundaries(activity) if activity else []
    starts = [b for b in bounds if b <= now_ms]
    ends = [b for b in bounds if b > now_ms]
    if starts:
        return starts[-1], (ends[0] if ends else now_ms), True
    # No observed boundary before now: fall back to the calendar, and SAY SO.
    start = _calendar_day_start(now_ms)
    return start, start + DAY_MS, False


def _nth_previous_day(now_ms: int, n: int, activity: Sequence[int]):
    """(start, end, from_lived) of the day n before the current one."""
    bounds = sorted(day_boundaries(activity)) if activity else []
    if bounds:
        # Boundaries mark where days ENDED. The current day begins at the
        # last boundary at-or-before now; each step back is one boundary.
        edges = bounds + [now_ms]
        idx = len(bounds)                       # index of the current day
        target = idx - n
        if 0 <= target - 1 < len(edges) - 1 and target >= 1:
            return edges[target - 1], edges[target], True
        if target == 0 and edges:
            return edges[0] - DAY_MS, edges[0], True
    start = _calendar_day_start(now_ms) - n * DAY_MS
    return start, start + DAY_MS, False


_LAST_N = re.compile(r"^(?:in\s+)?the\s+last\s+(\d+)\s+(minute|hour|day|week)s?$")
_N_AGO = re.compile(r"^(\d+)\s+(minute|hour|day|week)s?\s+ago$")
_UNIT_MS = {"minute": 60_000, "hour": HOUR_MS, "day": DAY_MS,
            "week": 7 * DAY_MS}


def parse(phrase: str, now_ms: int,
          activity: Optional[Sequence[int]] = None) -> Optional[Range]:
    """Resolve a relative time phrase, or None if it is not one.

    `activity` is the observed timestamps used to find lived day
    boundaries. Passing none is legal and makes the parser fall back to
    calendar days, flagged in the result.
    """
    text = " ".join(phrase.lower().strip().split())
    text = text.rstrip("?.!,")
    activity = list(activity or [])

    def lived_day(n):
        start, end, lived = _nth_previous_day(now_ms, n, activity)
        return start, end, lived

    # -- whole days --------------------------------------------------------
    if text in ("today", "so far today"):
        start, end, lived = _lived_day_bounds(now_ms, activity)
        return Range(start, min(end, now_ms), text, lived)

    if text in ("yesterday", "the day before"):
        start, end, lived = lived_day(1)
        return Range(start, end, text, lived)

    if text in ("the day before yesterday", "two days ago"):
        start, end, lived = lived_day(2)
        return Range(start, end, text, lived)

    # -- parts of a day ----------------------------------------------------
    #
    # "last night" is THE case this module exists for. It is the previous
    # lived day's night portion, which — because the day ends at sleep, not
    # at midnight — naturally includes a 1am session.
    for part, (lo, hi) in _DAY_PARTS.items():
        if text in ("last " + part, part, "this " + part, "yesterday " + part):
            n = 1 if text.startswith(("last ", "yesterday ")) else 0
            if n == 0 and part == "night":
                n = 1               # "night" alone means the one just passed
            start, end, lived = lived_day(n) if n else \
                _lived_day_bounds(now_ms, activity)[:2] + (
                    _lived_day_bounds(now_ms, activity)[2],)
            span = end - start
            return Range(int(start + span * lo), int(start + span * hi),
                         text, lived, calibrated=False)

    # -- rolling windows ---------------------------------------------------
    m = _LAST_N.match(text)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return Range(now_ms - n * _UNIT_MS[unit], now_ms, text, False)

    m = _N_AGO.match(text)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if unit == "day":
            start, end, lived = lived_day(n)
            return Range(start, end, text, lived)
        point = now_ms - n * _UNIT_MS[unit]
        half = _UNIT_MS[unit] // 2
        return Range(point - half, point + half, text, False)

    if text in ("this week", "the past week", "last week"):
        n = 7
        return Range(now_ms - n * DAY_MS, now_ms, text, False)

    if text in ("just now", "a moment ago"):
        return Range(now_ms - 15 * 60_000, now_ms, text, False)

    # -- HOW PEOPLE ACTUALLY ASK (added 2026-08-20 from a routing census) ---
    #
    # Twelve natural phrasings were run through the parser and FIVE of them
    # resolved to nothing: "lately", "recently", "an hour ago", "the
    # weekend", "the last few days". Each one then fell through to the node
    # path, which holds no time and answered from cosine similarity against
    # node labels — the failure that returned ENTITY `Aug` for a question
    # about August. A phrase the table does not know is not a small gap; it
    # is a question routed to the wrong organ.
    #
    # These are all VAGUE on purpose and are resolved to a generous window,
    # because the honest reading of "lately" is a span, not a boundary. The
    # caveat line reports the coverage of whatever window is chosen, so a
    # wide one cannot silently overclaim.
    if text in ("lately", "recently", "these days", "of late"):
        return Range(now_ms - 7 * DAY_MS, now_ms, text, False)

    if text in ("the last few days", "the past few days", "a few days ago",
                "the past couple of days", "the last couple of days"):
        return Range(now_ms - 3 * DAY_MS, now_ms, text, False)

    if text in ("this month", "the past month", "last month"):
        return Range(now_ms - 30 * DAY_MS, now_ms, text, False)

    # "an hour ago" — the numeric form was handled ("1 hour ago"), the way a
    # person says it was not.
    _ARTICLE = {"an hour ago": HOUR_MS, "a hour ago": HOUR_MS,
                "an hour": HOUR_MS, "half an hour ago": HOUR_MS // 2,
                "a minute ago": 60_000, "a couple of hours ago": 2 * HOUR_MS,
                "a few hours ago": 3 * HOUR_MS, "a day ago": DAY_MS,
                "a week ago": 7 * DAY_MS}
    if text in _ARTICLE:
        span = _ARTICLE[text]
        point = now_ms - span
        half = max(15 * 60_000, span // 2)
        return Range(point - half, min(point + half, now_ms), text, False)

    # The weekend: back to the most recent Saturday 00:00, through Sunday.
    if text in ("the weekend", "last weekend", "this weekend",
                "over the weekend"):
        now = datetime.fromtimestamp(now_ms / 1000.0)
        back = (now.weekday() - 5) % 7          # 5 = Saturday
        sat = datetime(now.year, now.month, now.day) - timedelta(days=back)
        t0 = int(sat.timestamp() * 1000)
        return Range(t0, min(t0 + 2 * DAY_MS, now_ms), text, False)

    if text in ("earlier", "earlier today", "this morning so far"):
        start, end, lived = _lived_day_bounds(now_ms, activity)
        return Range(start, now_ms, text, lived)

    # -- the minute/hour scale (added 2026-10-07, see find_range) -----------
    if text in ("right now", "at the moment", "currently"):
        return Range(now_ms - 15 * 60_000, now_ms, text, False)
    if text in ("the last few minutes", "the past few minutes",
                "last few minutes", "past few minutes", "a few minutes ago"):
        return Range(now_ms - 15 * 60_000, now_ms, text, False)
    if text in ("the last hour", "the past hour", "last hour", "past hour"):
        return Range(now_ms - HOUR_MS, now_ms, text, False)
    if text in ("the last couple of hours", "the past couple of hours",
                "the last few hours", "the past few hours"):
        span = 2 * HOUR_MS if "couple" in text else 3 * HOUR_MS
        return Range(now_ms - span, now_ms, text, False)
    if text == "tonight":
        start, end, lived = _lived_day_bounds(now_ms, activity)
        lo, _hi = _DAY_PARTS["evening"]
        t0 = int(start + (end - start) * lo)
        return Range(min(t0, now_ms - 60_000), now_ms, text, lived,
                     calibrated=False)

    # UNPARSEABLE. Deliberately None rather than a guess — see the module
    # docstring: a manufactured window turns a parsing failure into a
    # confident false "never".
    return None


_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"])}
_MONTHS.update({m[:3]: i + 1 for m, i in
                zip(list(_MONTHS), range(12))})
_WEEKDAYS = {d: i for i, d in enumerate(
    ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
     "sunday"])}

_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTH_DAY_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+"
    r"(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b", re.I)
_DAY_MONTH_RE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?"
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
    r"(?:,?\s+(\d{4}))?\b", re.I)
_WEEKDAY_RE = re.compile(
    r"\b(?:on\s+|last\s+)?(monday|tuesday|wednesday|thursday|friday"
    r"|saturday|sunday)\b", re.I)


def _calendar_range(year: int, month: int, day: int, phrase: str):
    """A named calendar day → its local midnight-to-midnight window.

    Calendar, not lived-rhythm, and `from_lived_rhythm=False` says so. When
    a person names a date they mean the date; the lived-day machinery is for
    relative phrases like "yesterday", where the boundary genuinely is a
    question about when they went to sleep.
    """
    try:
        start = datetime(year, month, day)
    except ValueError:
        return None
    t0 = int(start.timestamp() * 1000)
    return Range(t0, t0 + DAY_MS, phrase, False)


def parse_absolute(text: str, now_ms: int) -> Optional[Range]:
    """A named date inside free text: "august 3", "3 Aug 2026", "2026-08-03".

    Added because `parse` handled only RELATIVE phrases, and the first
    question anyone asks a memory is about a specific day. Without this,
    "what was I doing on August 3?" resolved to no window at all, retrieval
    fell through to the node graph, and the answer came back `not in memory`
    about a day holding 29,250 claims.

    A year is optional and defaults to the year that puts the date in the
    PAST, because a memory is asked about what happened, not what will.
    """
    now_year = datetime.fromtimestamp(now_ms / 1000.0).year
    m = _ISO_RE.search(text)
    if m:
        return _calendar_range(int(m.group(1)), int(m.group(2)),
                               int(m.group(3)), m.group(0))
    for regex, order in ((_MONTH_DAY_RE, "md"), (_DAY_MONTH_RE, "dm")):
        m = regex.search(text)
        if not m:
            continue
        mon_s, day_s = (m.group(1), m.group(2)) if order == "md" else (m.group(2), m.group(1))
        month = _MONTHS.get(mon_s.lower()[:3])
        if month is None:
            continue
        year = int(m.group(3)) if m.group(3) else now_year
        rng = _calendar_range(year, month, int(day_s), m.group(0).strip())
        if rng is not None and m.group(3) is None and rng.t0_ms > now_ms:
            rng = _calendar_range(year - 1, month, int(day_s), m.group(0).strip())
        return rng
    m = _WEEKDAY_RE.search(text)
    if m:
        target = _WEEKDAYS[m.group(1).lower()]
        now = datetime.fromtimestamp(now_ms / 1000.0)
        back = (now.weekday() - target) % 7 or 7
        day = now - timedelta(days=back)
        return _calendar_range(day.year, day.month, day.day, m.group(0).strip())
    return None


_NUMBER_WORDS = {"one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4,
                 "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                 "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
                 "twenty": 20, "thirty": 30, "forty": 40, "forty-five": 45}
_COUNT_RE = r"(\d+|%s)" % "|".join(
    sorted((re.escape(w) for w in _NUMBER_WORDS if w not in ("a", "an")),
           key=len, reverse=True))
_LAST_N_IN = re.compile(r"\b(?:the\s+)?(?:last|past)\s+%s\s+(minute|hour|day|week)s?\b"
                        % _COUNT_RE)
_N_AGO_IN = re.compile(r"\b%s\s+(minute|hour|day|week)s?\s+ago\b" % _COUNT_RE)


def _count(token: str) -> int:
    if token.isdigit():
        return int(token)
    return _NUMBER_WORDS.get(token, 0)


def find_range(text: str, now_ms: int,
               activity: Optional[Sequence[int]] = None) -> Optional[Range]:
    """The time window a QUESTION is about, or None if it names no time.

    Relative phrases are matched longest-first so "yesterday afternoon" never
    loses to "yesterday" — the shorter match would silently widen the window
    by a factor of three and make the answer describe the wrong half of a day.
    """
    low = " ".join(text.lower().strip().split())
    # NUMERIC WINDOWS FIRST, searched INSIDE the question (2026-10-07). The
    # anchored forms in `parse` match only when the whole text is the phrase,
    # so "what did I read in the last 10 minutes" resolved to no window and a
    # live recency question answered `not in memory`. A count is also the
    # most specific thing a question can say about time, so it wins.
    m = _LAST_N_IN.search(low)
    if m:
        n = _count(m.group(1))
        if n:
            return Range(now_ms - n * _UNIT_MS[m.group(2)], now_ms,
                         m.group(0).strip(), False)
    m = _N_AGO_IN.search(low)
    if m:
        n = _count(m.group(1))
        if n:
            return parse("%d %s ago" % (n, m.group(2)), now_ms, activity)
    for phrase in sorted(phrases(), key=len, reverse=True):
        if phrase in low:
            rng = parse(phrase, now_ms, activity)
            if rng is not None:
                return rng
    return parse_absolute(low, now_ms)


def phrases() -> List[str]:
    """Everything the table understands, for the supplier's cue extractor
    to match against and for a test to enumerate."""
    out = ["today", "so far today", "yesterday", "the day before",
           "the day before yesterday", "two days ago", "this week",
           "the past week", "last week", "just now", "a moment ago",
           # the routing census of 2026-08-20 — see parse()
           "lately", "recently", "these days", "of late",
           "the last few days", "the past few days", "a few days ago",
           "the past couple of days", "the last couple of days",
           "this month", "the past month", "last month",
           "an hour ago", "a hour ago", "half an hour ago", "a minute ago",
           "a couple of hours ago", "a few hours ago", "a day ago",
           "a week ago", "the weekend", "last weekend", "this weekend",
           "over the weekend", "earlier today", "earlier",
           # the minute/hour scale — 2026-10-07, see find_range
           "right now", "at the moment", "currently",
           "the last few minutes", "the past few minutes",
           "last few minutes", "past few minutes", "a few minutes ago",
           "the last hour", "the past hour", "last hour", "past hour",
           "the last couple of hours", "the past couple of hours",
           "the last few hours", "the past few hours", "tonight"]
    for part in _DAY_PARTS:
        out += [part, "last " + part, "this " + part, "yesterday " + part]
    return sorted(set(out))
