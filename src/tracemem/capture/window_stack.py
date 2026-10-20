"""OWNS: who covers whom (map: World / Screen stream / Occlusion truth;
atoms: Window z-order snapshot, Visible-region map).

WHY THE PERIPHERAL NEEDS THIS: the diff kernel reports "something changed
in region (7,3)". Turning that into "something changed in your calendar"
requires knowing which window actually OWNED those pixels at that instant
— and the frontmost app is NOT the answer, because a notification, a
floating player, or a half-covered editor all put someone else's pixels
where the frontmost window nominally sits. Attributing a change to the
wrong window is a silent mis-citation: the claim looks perfect and points
at the wrong thing forever.

TWO PARTS:
  1. Snapshot — sample CGWindowList (order + bounds) at a low rate, keyed
     by t_ms. Cheap, no pixels, no content.
  2. Visible-region map — pure geometry: a window's visible area is its
     rect MINUS every rect above it in z-order. Then a point lookup answers
     "who owns this pixel".

COORDINATES: CGWindowList reports bounds in POINTS already (it is the
window server's layout space), so nothing here converts — per the points
law (capture/coordinate_space.py), and that non-conversion is deliberate.

COST NOTE the brief asked for: this is the cheap path. ScreenCaptureKit
can hand back per-window metadata with its frames, which would be exact and
free of sampling skew, but it is only available inside a running SCStream
and only for windows in the content filter. Snapshot sampling covers every
window including ones capture excludes, at the cost of skew bounded by the
sample interval. Verdict recorded on the board: keep sampling as the
general answer; revisit if SCStream metadata ever becomes the only consumer.

SPEC: 01 §3
ORACLE: tests/unit/test_window_stack.py — synthetic window lists for the
        geometry, plus a live snapshot of this Mac's real window server.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

Rect = Tuple[float, float, float, float]        # x0, y0, x1, y1 in POINTS

# How often the stack is sampled. Window arrangement changes at human speed;
# 2s bounds attribution skew at 2s while costing one cheap window-server call.
# NOT probed — a declared choice. The Ephemerals atom (3-second toasts) is the
# one that will challenge it, and its live run owns moving this number.
SNAPSHOT_INTERVAL_S = 2.0


@dataclass(frozen=True)
class WindowInfo:
    window_id: int
    owner: str                  # application name
    pid: int
    title: str                  # "" when the window server withholds titles
    layer: int                  # 0 = normal; >0 = menubar/overlay/notification
    rect: Rect                  # in points
    z: int                      # 0 = frontmost. CGWindowList's own order.

    @property
    def area(self) -> float:
        x0, y0, x1, y1 = self.rect
        return max(0.0, x1 - x0) * max(0.0, y1 - y0)


@dataclass(frozen=True)
class StackSnapshot:
    t_ms: int
    windows: Tuple[WindowInfo, ...]

    def frontmost(self, display_rects: Optional[Sequence] = None,
                  focus_pid: Optional[int] = None) -> Optional[WindowInfo]:
        """The frontmost window a HUMAN is looking at.

        REGRESSION FIXED (audit 2026-07-21): this used to return the first
        layer-0 window in CGWindowList order, which on this Mac is an
        off-screen Brave helper at rect (0, -73, 1512, -32) carrying no
        title. That produced window="" alongside titles_available=True —
        exactly the "empty title that isn't a permission hole" confusion
        the focus sense exists to prevent, and it silently disagreed with
        the older attention_mac tap which reported the real title.

        RE-AUDIT 2026-07-21 found the first attempt INSUFFICIENT: filtering
        to on-screen windows only swapped one wrong window for another. Live
        on this Mac the layer-0 list is

            z=5  area   170,856  title ''      rect (0, 37, 1512, 150)
            z=6  area 1,428,840  title 'This video ... - YouTube'

        — z=5 is an on-screen, positive-area, untitled Brave chrome strip,
        so the geometry filter passed it straight through and the sense
        still reported window='' with titles_available=True.

        So the rule is now: prefer a TITLED window. That is what the older,
        live-verified attention_mac tap effectively does (it walks the
        frontmost app's windows for the first non-empty name), and aligning
        with it is the point — two senses on one machine disagreeing about
        which window is in front is the bug, whichever one is "right".

        Fallbacks, in order: titled on-screen → any on-screen → any
        layer-0. A wrong window beats no window for a sense whose job is
        "where are you", but only after the better rules find nothing.

        AUDIT 2026-07-28 — `focus_pid`, and why it exists. A live full-suite
        run produced a STABLE disagreement: this method said 'Brave Browser'
        while NSWorkspace said 'Microsoft Edge', all four bracketed reads
        agreeing. Neither was wrong. They answer DIFFERENT questions:

            NSWorkspace.frontmostApplication -> who owns the KEYBOARD
            CGWindowList z-order             -> what is visually ON TOP

        Those legitimately differ — an app can hold focus while another
        app's window sits above it. The focus sense's job (01_world > Focus
        sense: "knows which window you're on") is the FIRST question, so the
        OS's own answer decides the app, and z-order is used only to choose
        WHICH of that app's windows. That is what attention_mac has always
        done by filtering on pid; this brings the two senses into agreement
        by construction rather than by assertion.

        `focus_pid=None` keeps the pure z-order behaviour, so every existing
        caller and fixture is untouched.
        """
        candidates = [w for w in self.windows if w.layer == 0 and w.area > 0]
        if focus_pid is not None:
            owned = [w for w in candidates if w.pid == focus_pid]
            # An app that holds focus with no ordinary window of its own (a
            # menubar agent, a sheet) leaves `owned` empty; fall back rather
            # than report nothing, since something IS on screen.
            candidates = owned or candidates
        if display_rects:
            on_screen = [w for w in candidates
                         if any(_intersect(w.rect, d) for d in display_rects)]
            candidates = on_screen or candidates
        titled = [w for w in candidates if w.title]
        return (titled or candidates or [None])[0]


# -- pure geometry ---------------------------------------------------------
def _intersect(a: Rect, b: Rect) -> Optional[Rect]:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    return (x0, y0, x1, y1) if x0 < x1 and y0 < y1 else None


def subtract(base: Rect, cutter: Rect) -> List[Rect]:
    """base MINUS cutter, as a list of disjoint rectangles.

    Rectangle subtraction is not closed — an L-shape needs up to 4 rects —
    so this returns a list, and every caller must handle that. Splitting
    into bands (top, bottom, then left/right of the middle band) keeps the
    pieces disjoint and their union exactly equal to the true remainder.
    """
    hit = _intersect(base, cutter)
    if hit is None:
        return [base]
    bx0, by0, bx1, by1 = base
    hx0, hy0, hx1, hy1 = hit
    out: List[Rect] = []
    if by0 < hy0:
        out.append((bx0, by0, bx1, hy0))            # band above
    if hy1 < by1:
        out.append((bx0, hy1, bx1, by1))            # band below
    if bx0 < hx0:
        out.append((bx0, hy0, hx0, hy1))            # left of the middle band
    if hx1 < bx1:
        out.append((hx1, hy0, bx1, hy1))            # right of it
    return out


def visible_regions(windows: Sequence[WindowInfo]) -> dict:
    """window_id → the rectangles of it a human can actually see.

    Walks front to back, carving each window by everything already placed
    in front of it. A fully covered window maps to [] — which is the useful
    answer, not a missing key: "present but invisible" is exactly what the
    peripheral must not attribute a change to.
    """
    ordered = sorted(windows, key=lambda w: w.z)
    out = {}
    occluders: List[Rect] = []
    for w in ordered:
        pieces = [w.rect]
        for occ in occluders:
            nxt: List[Rect] = []
            for p in pieces:
                nxt.extend(subtract(p, occ))
            pieces = nxt
            if not pieces:
                break
        out[w.window_id] = pieces
        occluders.append(w.rect)
    return out


def window_at(point: Tuple[float, float], windows: Sequence[WindowInfo],
              regions: Optional[dict] = None) -> Optional[WindowInfo]:
    """Who owns this point — the peripheral's WHERE lookup. None = desktop."""
    regions = visible_regions(windows) if regions is None else regions
    px, py = point
    for w in sorted(windows, key=lambda w: w.z):
        for (x0, y0, x1, y1) in regions.get(w.window_id, ()):
            if x0 <= px < x1 and y0 <= py < y1:
                return w
    return None


def visible_area(window_id: int, regions: dict) -> float:
    return sum(max(0.0, x1 - x0) * max(0.0, y1 - y0)
               for (x0, y0, x1, y1) in regions.get(window_id, ()))


# -- the real sampler ------------------------------------------------------
def read_stack(t_ms: Optional[int] = None) -> StackSnapshot:
    """One live snapshot of the window server. No pixels, no content — just
    who is where. Returns an empty snapshot where Quartz is absent."""
    import time
    stamp = int(time.time() * 1000) if t_ms is None else t_ms
    try:
        import Quartz
    except Exception:
        return StackSnapshot(t_ms=stamp, windows=())

    infos = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly
        | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID) or []

    windows = []
    for z, info in enumerate(infos):        # CGWindowList is already front-to-back
        b = info.get("kCGWindowBounds") or {}
        x = float(b.get("X", 0.0))
        y = float(b.get("Y", 0.0))
        w = float(b.get("Width", 0.0))
        h = float(b.get("Height", 0.0))
        name = info.get("kCGWindowName")
        windows.append(WindowInfo(
            window_id=int(info.get("kCGWindowNumber", -1)),
            owner=str(info.get("kCGWindowOwnerName") or ""),
            pid=int(info.get("kCGWindowOwnerPID", -1)),
            title=str(name) if name else "",
            layer=int(info.get("kCGWindowLayer", 0)),
            rect=(x, y, x + w, y + h),
            z=z))
    return StackSnapshot(t_ms=stamp, windows=tuple(windows))
