"""OWNS: the focus context dict — the scene identifier's view of "where you
are" (map: World / Focus sense; atoms: Fullscreen flag, Active Space, Title
reader).

Three atoms in one file because they are three fields of ONE reading, and
splitting them would mean three window-server samples where one suffices.

FULLSCREEN is a strong context signal precisely because it collapses the
scene: a fullscreen window means one activity, no peeking, no side-by-side.
Detected geometrically — a layer-0 window whose rect covers a whole display
AND a hidden menubar — rather than via the private CGS Space APIs, which
change between macOS releases and fail silently when they do.

ACTIVE SPACE is inferred, per the brief, from the window list rather than
read from a private API. The inference: each Space shows a distinct SET of
windows, so the sorted tuple of on-screen layer-0 window ids is a signature
of the Space. Signatures get stable sequential ids the first time they are
seen.
    Its honest limits, declared: (a) the id is stable within a session but
    NOT across a restart — window ids are reassigned, so Space 2 today is
    not Space 2 tomorrow, and the field must never be persisted as though
    it were; (b) opening a window changes the signature, so a signature is
    matched by OVERLAP, not equality, or every new window would look like
    a new Space. The overlap threshold below is the one tunable, and it is
    labeled ASSUMED because no probe has run.

TITLES are the degraded-mode atom. Window titles need Screen Recording (and
for some apps, Accessibility); without the grant the window server returns
no name. The rule: the stream CONTINUES app-only and SAYS SO —
`titles_available: False` travels in the context so scene identification
knows the hole is a permission hole, not an untitled window. A missing
title that looks like an empty title is exactly the silent degradation this
field exists to prevent.

SPEC: 01 §6
ORACLE: tests/unit/test_focus_context.py
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from tracemem.capture.window_stack import StackSnapshot, WindowInfo, read_stack

# Jaccard similarity above which two window sets are the same Space.
# ASSUMED — no probe has run.
#
# RE-AUDIT 2026-07-21 moved this from 0.6 to 0.3, because 0.6 over-split
# small Spaces badly: a two-window Space losing one window scores 1/3 =
# 0.33, so every churn minted a new id (measured: 12 ids for 2 Spaces, 4
# ids for a single 2-window Space). Small Spaces are the common case — a
# fullscreen app on its own Space is two windows or fewer.
# 0.3 keeps both properties that matter:
#   {1,2} -> {1,3}      = 0.33  same Space  (one window churned)
#   {1,2,3,4,5} -> {1}  = 0.20  different   (the subset bug stays fixed)
SPACE_MATCH_OVERLAP = 0.3

# Remembered Space signatures are capped. Without a cap `_spaces` grows
# without bound and observe() scans all of it, so a daemon polling every 2s
# degrades quadratically and leaks for the life of the process (re-audit
# 2026-07-21). macOS allows 16 Spaces per display; 32 holds every real
# Space with room to spare, and the oldest is the safest to forget.
MAX_REMEMBERED_SPACES = 32

# macOS puts the menubar at layer 24 and its own overlays above that. A
# fullscreen scene is one where no menubar window is on screen.
MENUBAR_LAYER = 24


@dataclass(frozen=True)
class FocusContext:
    app: str
    pid: int
    window: str
    titles_available: bool      # False = permission hole, NOT an untitled window
    fullscreen: bool
    space_id: Optional[int]     # session-scoped; never persist as an identity
    display_key: Optional[str] = None

    def as_reading(self) -> Dict[str, object]:
        """The ContextStream shape (edge-triggered): the cursor and other
        fast-moving fields are deliberately absent — this channel is for
        facts that change at the speed of a person switching context."""
        return {"app": self.app, "window": self.window,
                "fullscreen": self.fullscreen, "space_id": self.space_id,
                "titles_available": self.titles_available}


def titles_available(windows: Sequence[WindowInfo]) -> bool:
    """Did the window server give us ANY title at all?

    Whole-snapshot, not per-window, and that is the point: one untitled
    window proves nothing (plenty of real windows have no title), whereas a
    snapshot where NOTHING has a title is the signature of a missing grant.
    A machine with zero windows tells us nothing either way — we report
    True there rather than accusing the OS of withholding.
    """
    real = [w for w in windows if w.layer == 0]
    if not real:
        return True
    return any(w.title for w in real)


def is_fullscreen(window: Optional[WindowInfo], display_rects: Sequence,
                  windows: Sequence[WindowInfo]) -> bool:
    """Fullscreen = a layer-0 window covering a whole display, menubar gone.

    Both halves are needed. A maximized window covers the display too but
    keeps the menubar; a menubar can also auto-hide under a merely large
    window. Requiring both is what separates "big" from "fullscreen".
    """
    if window is None or window.layer != 0:
        return False
    if any(w.layer == MENUBAR_LAYER for w in windows):
        return False
    x0, y0, x1, y1 = window.rect
    for (dx0, dy0, dx1, dy1) in display_rects:
        if x0 <= dx0 + 1 and y0 <= dy0 + 1 and x1 >= dx1 - 1 and y1 >= dy1 - 1:
            return True
    return False


class SpaceTracker:
    """Infers a session-scoped Space id from window-set signatures.

    TWO BUGS FIXED (audit 2026-07-21), both of which collapsed genuinely
    distinct Spaces into one id — the exact opposite of what the atom is for:

      1. The signature was a MONOTONIC UNION (`known |= sig`), so a Space's
         remembered set only ever grew. After a few drift steps it held
         every window ever seen there, and unrelated sets kept matching it.
      2. The score divided by `min(len(known), len(sig))`, so any current
         set that was a SUBSET of a remembered set scored a perfect 1.0. A
         Space showing one sticky all-Desktops window matched the biggest
         Space every time.

    Now: similarity is symmetric (Jaccard — shared over TOTAL distinct), so
    a subset no longer scores perfectly, and drift REPLACES the signature
    rather than accreting into it, so a Space's fingerprint stays the size
    of a Space.
    """

    def __init__(self, overlap: float = SPACE_MATCH_OVERLAP) -> None:
        self.overlap = overlap
        self._spaces: List[set] = []
        self._seen: Dict[int, int] = {}      # space id -> last-seen tick (LRU)
        self._tick = 0

    @staticmethod
    def signature(windows: Sequence[WindowInfo]) -> set:
        return {w.window_id for w in windows if w.layer == 0}

    def observe(self, windows: Sequence[WindowInfo]) -> Optional[int]:
        sig = self.signature(windows)
        if not sig:
            return None                       # no windows: no evidence of a Space
        best_i, best_score = None, 0.0
        for i, known in enumerate(self._spaces):
            if not known:                     # an evicted slot never matches
                continue
            union = len(known | sig) or 1
            score = len(known & sig) / union   # Jaccard: symmetric, subset-safe
            if score > best_score:
                best_i, best_score = i, score
        if best_i is not None and best_score >= self.overlap:
            # Same Space, drifted: REPLACE the signature so it tracks the
            # Space as it is now instead of accumulating its whole history.
            self._spaces[best_i] = set(sig)
            self._seen[best_i] = self._tick
            self._tick += 1
            return best_i
        self._spaces.append(set(sig))
        idx = len(self._spaces) - 1
        self._seen[idx] = self._tick
        self._tick += 1
        self._evict()
        return idx

    def _evict(self) -> None:
        """Forget the least-recently-seen Space once we hold too many.

        Ids are NOT renumbered — a Space's id must stay stable for as long
        as the session refers to it, so an evicted slot is emptied rather
        than removed. An emptied slot can never match (its signature is
        empty, so Jaccard is 0), and a returning Space simply gets a new id,
        which is the honest outcome: we forgot it.
        """
        live = [i for i, s in enumerate(self._spaces) if s]
        if len(live) <= MAX_REMEMBERED_SPACES:
            return
        oldest = min(live, key=lambda i: self._seen.get(i, 0))
        self._spaces[oldest] = set()


class FocusSense:
    """One sample = one FocusContext. Holds the SpaceTracker's memory."""

    def __init__(self, tracker: Optional[SpaceTracker] = None) -> None:
        self.tracker = tracker or SpaceTracker()

    def read(self, snapshot: Optional[StackSnapshot] = None,
             display_rects: Optional[Sequence] = None,
             focus_pid: Optional[int] = None) -> FocusContext:
        """One reading. `focus_pid` is the OS's own answer to "which app has
        the keyboard"; when omitted on a real Mac it is READ, not guessed.

        AUDIT 2026-07-28: without it this sense reported whichever window was
        visually on top, which is a different question from "which window are
        you on" and provably disagreed with the older attention_mac tap on
        this machine. Wiring it here rather than only in the test is the
        point — a fix behind a default that nothing uses is not a fix.
        """
        snap = snapshot if snapshot is not None else read_stack()
        rects = display_rects if display_rects is not None else _display_rects()
        focus_app = None
        if snapshot is None:               # live read only; fixtures stay pure
            focus_app, live_pid = _focused_app()
            if focus_pid is None:
                focus_pid = live_pid
        front = snap.frontmost(rects, focus_pid=focus_pid)

        # THE OS OWNS THE ANSWER TO "WHICH APP" (audit 2026-07-28, third
        # failure of the same guard). Deriving the app from the topmost
        # WINDOW is wrong whenever the focused app has no ordinary window on
        # screen — minimised, all-windows-closed, a menubar agent — because
        # frontmost() then falls back to somebody else's window and the sense
        # confidently reports the wrong application. Every claim the eye
        # writes carries this app, so that is a silent, permanent
        # mis-attribution of the founder's memory.
        #
        # So: NSWorkspace decides the app, the stack only supplies the window
        # title, and a title is used ONLY when it belongs to the focused app.
        # A focused app with no visible window reports its name and an empty
        # window — true, and better than a confident borrowed one.
        same_app = front is not None and (focus_pid is None
                                          or front.pid == focus_pid)
        return FocusContext(
            app=focus_app or (front.owner if front else ""),
            pid=focus_pid if focus_pid is not None else (front.pid if front else -1),
            window=front.title if same_app else "",
            titles_available=titles_available(snap.windows),
            fullscreen=is_fullscreen(front, rects, snap.windows),
            space_id=self.tracker.observe(snap.windows))


def _focused_app():
    """(app name, pid) of whoever owns the keyboard — the OS's own answer.

    (None, None) when unknowable. None is honest: the caller then falls back
    to pure z-order, a worse answer but never a fabricated one.
    """
    try:
        from AppKit import NSWorkspace
        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        if front is None:
            return (None, None)
        return (str(front.localizedName() or ""),
                int(front.processIdentifier()))
    except Exception:
        return (None, None)


def _display_rects() -> List[Tuple[float, float, float, float]]:
    """Each panel's rect in GLOBAL desktop points — origin included.

    BUG FIXED (audit 2026-07-21): this built (0, 0, w, h) for every display,
    so a fullscreen window on a second monitor matched nothing.
    """
    from tracemem.capture.displays import read_displays
    return [d.rect for d in read_displays()]
