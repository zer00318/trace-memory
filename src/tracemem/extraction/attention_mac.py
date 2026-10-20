"""OWNS: Mac native attention channel: frontmost + cursor (P1.2)

The focus sense — pure perception of WHERE YOU ARE on the machine:
frontmost app, its front window title, cursor position. No claims, no
routing knowledge — those live upstream. Imports AppKit/Quartz lazily so
non-mac test environments import this module fine.

Window titles come from the window server and are only visible when the
process has Screen Recording permission; the sense degrades HONESTLY to
an empty title rather than failing, and says so via `window_visible`.

SPEC: 01 s6 / 07 s1
ORACLE: a real read on this Mac emits app + pid + cursor in [0,1]
        (tests/oracles: test_attention_tap_reads_frontmost).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AttentionSpan:
    t_ms: int
    app: str                 # frontmost application name, e.g. "Safari"
    pid: int
    window: str              # front window title; "" when not visible to us
    window_visible: bool     # False = permission denied us titles (honest hole)
    cursor_x: float          # normalized [0,1], top-left origin, main display
    cursor_y: float


def attention_span(t_ms: Optional[int] = None) -> AttentionSpan:
    """One reading of the focus sense, taken NOW on this machine."""
    import time

    if t_ms is not None and not isinstance(t_ms, int):
        raise TypeError(f"attention_span() takes an optional int timestamp, "
                        f"got {type(t_ms).__name__}")

    from AppKit import NSWorkspace
    import Quartz

    front = NSWorkspace.sharedWorkspace().frontmostApplication()
    app = str(front.localizedName() or "") if front else ""
    pid = int(front.processIdentifier()) if front else -1

    window, visible = "", False
    infos = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly
        | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID) or []
    for info in infos:
        if int(info.get("kCGWindowOwnerPID", -2)) != pid:
            continue
        if int(info.get("kCGWindowLayer", 1)) != 0:      # real windows only
            continue
        name = info.get("kCGWindowName")
        if name is not None:
            visible = True                # the window server showed us titles
            if str(name):
                window = str(name)
                break

    # Cursor in global top-left coordinates, normalized by the main display.
    loc = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    bounds = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
    cx = min(1.0, max(0.0, float(loc.x) / float(bounds.size.width or 1.0)))
    cy = min(1.0, max(0.0, float(loc.y) / float(bounds.size.height or 1.0)))

    return AttentionSpan(
        t_ms=int(time.time() * 1000) if t_ms is None else t_ms,
        app=app, pid=pid, window=window, window_visible=visible,
        cursor_x=cx, cursor_y=cy)
