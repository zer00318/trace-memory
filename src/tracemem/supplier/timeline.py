"""MOMENTS — what a day looks like when you stop reading it one fragment at a time.

THE PROBLEM THIS SOLVES, measured on the founder's own store (2026-08-03,
29,250 claims in one day):

    perceiver ocr : 14,739 claims, median length 15 characters
                    "Photos" · "File" · "Edit" · "+" · "kiometres oway"
    perceiver ax  : 14,502 claims, of which 76,480/119,000 across Aug 1-3
                    are AXButton — "Back", "Reload", "Brave Shields"

Ask "what was I doing on August 3" and the node path answered `not in memory`
in 101 seconds. It was right to: the context it packed held ENTITY `Aug`,
ENTITY `Day`, ENTITY `Year` and a PATTERN about `Trash`. Meanwhile two claims
that same minute read (the video is replaced by a stand-in here):

    youtube.com/watch?v=aBcDeFgHiJk
    Morning Jazz | Live Session | ... - YouTube

The answer was in the store the whole time. Nothing looked at it, because
retrieval entered through the node graph and the node graph is built from
regex over the OCR confetti.

THE SPINE. A day is not 29,250 facts, it is a few dozen MOMENTS — a stretch
of time on one thing. The AX sense already reports what that thing is (url,
window_title) every 5 seconds. So a moment is a contiguous run of claims
sharing (app, url-or-title), and a day is the sequence of them. That is small
enough to hand to a model whole, and it is the shape a person's question
actually has.

WHY CHROME IS RANKED, NOT DROPPED. `AXButton` labels are 64% of the AX volume
and carry nothing — but DISPATCH's standing rule is "every gate refuses to
PROMOTE, never to RECORD", and raw is immutable. So nothing here deletes: the
chrome roles are simply never chosen as evidence. A label that turns out to
matter later is still in raw and returns the moment something asks for it.

WHY THE OCR SAMPLE IS SHORTEST-FIRST-EXCLUDED. Median OCR claim length is 15
characters, which is menu chrome. Longer runs are prose the founder was
actually reading. Sorting by length is a crude proxy for informativeness and
is labelled as such — it is a ranking, not a truth claim.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

# AX object roles that are furniture rather than content. Measured on Aug 1-3
# of the founder's store: AXButton 76,480 claims, AXPopUpButton 27,481 — 87%
# of all AX object claims, and not one of them says what he was doing.
CHROME_ROLES = frozenset({
    "AXButton", "AXPopUpButton", "AXMenuButton", "AXMenuItem", "AXMenu",
    "AXToolbar", "AXScrollBar", "AXSplitter", "AXImage", "AXCheckBox",
})

# A moment shorter than this is a flicker — an app that had focus for two
# seconds while the founder passed through it. Same constant family as the
# binder's flicker collapse; stated here rather than imported so this module
# stays usable without the binder.
MIN_MOMENT_MS = 20_000

# TWO VISITS TO THE SAME PAGE ARE TWO MOMENTS IF THE MACHINE WAS AWAY IN
# BETWEEN. Without this bound, `_merge_adjacent` joined runs that were hours
# apart whenever nothing else happened to be recorded between them — which is
# exactly what a capture GAP looks like from the claim stream. Measured on
# 2026-08-04, a day the digest itself reports as "watching 29% of this period,
# blind for 11 intervals": eight moments summed to 22 hours, one of them
# claiming 392 continuous minutes on a single video. The moment had swallowed
# the blindness and presented it as attention.
#
# That is the failure this product may never have: a memory must not fill a
# hole it knows about. Coverage is reported honestly two lines further down
# the same digest, so a merge that contradicts it is the system disagreeing
# with itself in one screen.
MERGE_GAP_MS = 5 * 60_000

# HOW MANY MOMENTS AN ANSWER SHOULD EVER SEE. A busy three minutes of window
# switching produced twenty moments, and the model dutifully walked all
# twenty — turning "what have I been doing this morning" into a transcript.
# The moments that matter for a question are the long ones; the rest are
# passing through. Whatever is dropped is COUNTED and said, never silently.
MAX_RENDERED_MOMENTS = 15

# THE BROWSER'S OWN NOISE, appended to every window title it reports:
#   "… - YouTube - Audio playing - High memory usage - 1.5 GB - Brave (Private)"
# Four of those five segments describe the BROWSER's state, not the founder's
# life, and they change while he sits still — so leaving them in splits one
# twenty-minute video into six "moments" that differ only by how much RAM
# Brave had allocated. Stripped for identity and display; the raw claim keeps
# every character, as raw always does.
_CHROME_SUFFIX = re.compile(
    r"\s*[-–|]\s*(?:Audio playing|Video playing|Camera .*?|Microphone .*?"
    r"|High memory usage|[\d.]+\s*[KMG]B|Brave(?:\s*\(Private\))?"
    r"|Google Chrome|Safari|Firefox|Private browsing)\s*$", re.I)


def clean_title(title: Optional[str]) -> Optional[str]:
    """A window title with the browser's self-report peeled off the end."""
    if not title:
        return title
    prev = None
    out = title.strip()
    while out and out != prev:
        prev = out
        out = _CHROME_SUFFIX.sub("", out).strip()
    return out or title.strip()


# How many OCR lines are quoted as evidence per moment. Enough to recognise
# the content, small enough that a day of moments still fits a context window.
SAMPLE_PER_MOMENT = 6
MIN_SAMPLE_CHARS = 18

# HOW LONG A CITATION IS. The rendered [id] and the block's source_ids must be
# the SAME string or citation checking silently fails open: the model dutifully
# writes [375ebf95], `parse_answer` looks for it among full UUIDs, finds
# nothing, and reports an answer with zero citations — which the never-lie
# guard then waves through because it only verifies answers that HAVE
# citations. A prefix mismatch is therefore not a cosmetic bug; it disables
# the guard.
CITE_LEN = 8


def cite_id(claim_id: str) -> str:
    return (claim_id or "")[:CITE_LEN]


@dataclass
class Moment:
    """A stretch of time spent on one thing."""
    t0_ms: int
    t1_ms: int
    app: str
    title: Optional[str] = None
    url: Optional[str] = None
    n_claims: int = 0
    samples: List[str] = field(default_factory=list)
    claim_ids: List[str] = field(default_factory=list)
    # The strongest witness that went into this moment, carried rather than
    # invented: an AX-read title and a guessed OCR fragment are not equally
    # good evidence, and the packer must not flatten that difference.
    grade: Optional[object] = None

    @property
    def duration_ms(self) -> int:
        return max(0, self.t1_ms - self.t0_ms)

    def label(self) -> str:
        """What this moment WAS, in one string, best signal first.

        Title beats url beats app: a page title is what a person would call
        the thing ("Morning Jazz … - YouTube"), a url is what the machine
        would call it, and the app alone is what is left when neither was
        read.
        """
        return self.title or self.url or self.app


def _ax_kind(claim) -> Optional[str]:
    return (claim.structured or {}).get("ax")


def _role(claim) -> str:
    return str((claim.structured or {}).get("role") or "")


def is_chrome(claim) -> bool:
    """UI furniture: an AX object whose role is a control, not content."""
    return _ax_kind(claim) == "object" and _role(claim) in CHROME_ROLES


def _key(app: str, title: Optional[str], url: Optional[str]) -> Tuple:
    """What makes this moment THIS moment.

    The url is deliberately compared whole, query string included: two
    YouTube watch pages are two different moments even though both are
    `youtube.com`, and that distinction is the entire content of the answer.
    """
    return (app, title, url)


def build(claims: Sequence, min_moment_ms: int = MIN_MOMENT_MS) -> List[Moment]:
    """Claims in time order → the moments they spent themselves on.

    The AX readings act as a running CONTEXT rather than as events: a url
    claim at 20:36 describes every OCR fragment after it until the next url
    claim. That is what makes the confetti attributable at all — an OCR run
    saying "kiometres oway" is meaningless alone and is evidence for
    "reading a story on Reddit" once it sits inside a moment.
    """
    fold = TimelineFold()
    fold.feed(claims)
    return fold.result(min_moment_ms)


class TimelineFold:
    """`build` as a left-to-right fold that can be fed more claims later.

    WHY (2026-10-15): "which apps did I use most today?" took 66s, 61s of it
    decrypting all ~185k of today's claims, on every ask. The timeline is a
    fold over claims in time order, so its state at time T plus the claims
    after T is the same timeline as a rebuild. `supply_window` keeps one fold
    and feeds it only what is new; `build` is a fold fed once, so the two
    paths cannot drift (test_timeline_fold.py).
    """

    def __init__(self) -> None:
        self.moments: List[Moment] = []
        self.cur: Optional[Moment] = None
        self.app, self.title, self.url = "unknown", None, None
        self.contexts: Dict[Tuple[str, Optional[str]], Dict[str, Optional[str]]] = {}
        self.current_key: Optional[Tuple[str, Optional[str]]] = None
        self.last_t_ms: Optional[int] = None

    def feed(self, claims: Sequence) -> None:
        """Claims strictly after everything fed so far, any order."""
        cur, current_key = self.cur, self.current_key
        app, title, url = self.app, self.title, self.url
        contexts = self.contexts
        for c in sorted(claims, key=lambda c: c.coord.t_ms):
            if (c.structured or {}).get("witness"):
                continue
            kind = _ax_kind(c)
            claim_app = c.coord.capture.app or "unknown"
            claim_window = c.coord.capture.window
            key = (claim_app, claim_window)
            if kind is not None:
                state = contexts.setdefault(key, {"title": None, "url": None})
                if kind == "url":
                    state["url"] = c.text
                elif kind == "window_title":
                    state["title"] = clean_title(c.text)
                current_key = key
                app, title, url = claim_app, state["title"], state["url"]
            elif claim_window is not None:
                # Geometry-attributed OCR: the pixel owner is stronger than a
                # stale focused-window reading. Context is reusable only for this
                # exact window id.
                state = contexts.get(key, {"title": None, "url": None})
                current_key = key
                app, title, url = claim_app, state["title"], state["url"]
            elif current_key is not None and current_key[0] == claim_app:
                # Legacy rows predate window identity. They may inherit only
                # while the application is unchanged.
                state = contexts.get(current_key, {"title": None, "url": None})
                app, title, url = claim_app, state["title"], state["url"]
            else:
                # Attention changed before the next AX refresh. End the prior
                # context explicitly; never file this app under the old URL.
                current_key = (claim_app, None)
                app, title, url = claim_app, None, None

            key = _key(app, title, url)
            # A SILENCE ENDS A MOMENT, even when nothing about the page changed.
            # The key alone is not enough: if the founder leaves a video open and
            # the machine stops seeing for four hours, no key changes and the run
            # continues — so the moment absorbs the blindness and reports it as
            # attention. See MERGE_GAP_MS.
            gap = cur is not None and c.coord.t_ms - cur.t1_ms > MERGE_GAP_MS
            if cur is None or gap or _key(cur.app, cur.title, cur.url) != key:
                cur = Moment(t0_ms=c.coord.t_ms, t1_ms=c.coord.t_ms,
                             app=app, title=title, url=url)
                self.moments.append(cur)
            cur.t1_ms = max(cur.t1_ms, c.coord.t_ms)
            cur.n_claims += 1
            if cur.grade is None or c.grade > cur.grade:
                cur.grade = c.grade
            if len(cur.claim_ids) < 40:
                cur.claim_ids.append(c.id)
            if (kind is None and not is_chrome(c)
                    and not (c.structured or {}).get("presence")
                    and len(c.text) >= MIN_SAMPLE_CHARS):
                cur.samples.append(c.text)

            self.last_t_ms = c.coord.t_ms
        self.cur, self.current_key = cur, current_key
        self.app, self.title, self.url = app, title, url

    def result(self, min_moment_ms: int = MIN_MOMENT_MS) -> List[Moment]:
        """The moments so far. Copies: merging must not disturb the fold."""
        moments = [Moment(t0_ms=m.t0_ms, t1_ms=m.t1_ms, app=m.app,
                          title=m.title, url=m.url, n_claims=m.n_claims,
                          samples=list(m.samples), claim_ids=list(m.claim_ids),
                          grade=m.grade) for m in self.moments]
        merged = _merge_adjacent(moments)
        kept = [m for m in merged
                if m.duration_ms >= min_moment_ms or m.n_claims >= 20]
        # A day that is ALL flicker must not come back empty — an honest few
        # seconds beats a confident nothing.
        return kept or merged


def _merge_adjacent(moments: List[Moment]) -> List[Moment]:
    """Fold neighbouring moments that are the same thing.

    Two causes, both measured on the real store. A url can churn while the
    page does not (`youtube.com` → `youtube.com/watch?v=X` → `youtube.com`
    as the SPA rewrites its own address bar), and a title can arrive a beat
    before or after the url it belongs to. Keying strictly on (app, title,
    url) turned one seven-minute video into nine moments. Merging on the
    LABEL — what a person would call it — repairs that without inventing
    continuity across a real switch.
    """
    out: List[Moment] = []
    for m in moments:
        prev = out[-1] if out else None
        if (prev is not None and prev.app == m.app
                and prev.label() == m.label()
                and m.t0_ms - prev.t1_ms <= MERGE_GAP_MS):
            prev.t1_ms = max(prev.t1_ms, m.t1_ms)
            prev.n_claims += m.n_claims
            prev.samples.extend(m.samples)
            prev.url = prev.url or m.url
            if m.grade is not None and (prev.grade is None or m.grade > prev.grade):
                prev.grade = m.grade
            prev.title = prev.title or m.title
            for cid in m.claim_ids:
                if len(prev.claim_ids) < 40:
                    prev.claim_ids.append(cid)
            continue
        out.append(m)
    return out


def _rank_samples(samples: List[str], n: int = SAMPLE_PER_MOMENT) -> List[str]:
    seen, out = set(), []
    for s in sorted(samples, key=len, reverse=True):
        k = s.strip().lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(s.strip())
        if len(out) >= n:
            break
    return out


def render(moments: Sequence[Moment], clock_fn=None, max_moments: int = 40) -> str:
    """The timeline as the model sees it.

    Times are rendered as wall-clock because the question is asked in wall
    clock ("what was I doing on August 3"), and an epoch millisecond in the
    context is a number the model will happily invent a meaning for.
    """
    import time as _t
    clock_fn = clock_fn or (lambda ms: _t.strftime("%H:%M", _t.localtime(ms / 1000.0)))
    ranked = sorted(moments, key=lambda m: m.duration_ms,
                    reverse=True)[:min(max_moments, MAX_RENDERED_MOMENTS)]
    ranked.sort(key=lambda m: m.t0_ms)
    lines = []
    for m in ranked:
        mins = m.duration_ms / 60000.0
        head = "%s-%s (%.0f min) %s | %s" % (
            clock_fn(m.t0_ms), clock_fn(m.t1_ms), mins, m.app, m.label())
        if m.url and m.url != m.label():
            head += " | %s" % m.url
        lines.append("[%s] %s" % (cite_id(m.claim_ids[0]) if m.claim_ids
                                  else "-" * CITE_LEN, head))
        for s in _rank_samples(m.samples):
            lines.append("        seen: %s" % s[:120])
    return "\n".join(lines)


def summarize(moments: Sequence[Moment]) -> Dict[str, object]:
    """Numbers a person can check against their own memory of the day."""
    total = sum(m.duration_ms for m in moments)
    by_app: Dict[str, int] = {}
    for m in moments:
        by_app[m.app] = by_app.get(m.app, 0) + m.duration_ms
    return {
        "moments": len(moments),
        "watched_ms": total,
        "by_app_ms": dict(sorted(by_app.items(), key=lambda kv: -kv[1])),
    }
