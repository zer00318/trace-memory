"""OWNS: the Accessibility tree as a SENSE — the screen read, never guessed.

WHY THIS IS THE HIGHEST-VALUE PERCEPTION WORK AVAILABLE. Two separately
measured deficits turn out to have one cause and one fix:

  · **object tier 4.3%** (`eval_runs/context_gap.json`) — the worst number in
    the product. The ceiling's "objects" are exactly what an accessibility
    tree enumerates, and it enumerates them with no GPU and no model.
  · **the site tier is garbage, and provably unfixable by counting.** Measured
    2026-07-29 on the founder's real claims: `esa.com` appears 13 times and
    `chess.com` twice; `youtube.co`, `youluve.com`, `utube.co`,
    `thyoutube.com` all sit beside `youtube.com`. OCR misreads of a domain are
    SYSTEMATIC — the same wrong string read off the same screen again and
    again — so no frequency, corroboration or edit-distance rule can separate
    the misread from the truth without discarding real sites. The address bar
    holds the URL as a STRING. Reading it is not a better guess; it is the end
    of guessing.

WHAT THIS ACTUALLY DELIVERS, measured on this Mac 2026-07-29 against a live
Chromium window (Microsoft Edge, 66 AX nodes walked):

    AXWindow      title  'TRACE cockpit - Microsoft Edge'
    AXTextField   value  '127.0.0.1:4242'      desc 'Address and search bar'
    AXRadioButton              x9, one per open tab, each with its real title
                                 and AXValue=True on the selected one

The URL is exact. The tab titles are exact. Neither passed through a pixel.

WHAT IT DOES **NOT** DELIVER, stated because the gap matters more than the
win. **Page content is not available.** Chromium exposes its renderer's
accessibility tree only when asked, and the documented request —
`AXManualAccessibility` — returned `-25205` (attribute unsupported) on this
build. So there is no `AXWebArea` and no in-page text: this sense sees the
BROWSER, not the PAGE. OCR remains the only reader of page content, and the
two are complements rather than replacements. Electron apps are worse still:
the Claude desktop app exposed exactly 2 labelled nodes for its whole window.

NO NEW DEPENDENCY. `pyobjc-framework-ApplicationServices` is not installed and
is not being added: `objc.loadBundleFunctions` binds the four C entry points
directly out of the system framework, the same trick `app/permissions.py`
already uses for `AXIsProcessTrusted` (there via ctypes, because a function
returning a Boolean needs no bridge; these return CFTypeRefs and do).

OCCLUSION IS CHECKED, NOT ASSUMED. The AX tree will happily describe a window
that is behind three others, and a memory that holds something the founder
never actually saw is a false memory — the exact failure this product exists
to refuse. So a reading is only returned for the FOCUSED window of the
FRONTMOST application, and it is cross-checked against the window server's own
on-screen list before it is believed.

UNKNOWN IS A HOLE. Every failure path here returns None, never a partial or a
guess: accessibility not granted, framework unavailable, the app exposing no
windows (measured: Brave with every window minimised answers zero windows with
a SUCCESS code, and the window server agrees — that is a true "nothing to
see", not an error). A sense that fabricates on a failed read is worse than no
sense at all.

SPEC: 01 §2 (world/screen), priority MASTER #2.
"""
from __future__ import annotations

from typing import Dict, List, Optional

# How deep to walk. The browser chrome that carries the URL and the tab
# titles sits at depth 8 in the measured tree; 12 leaves headroom without
# letting a pathological app turn one frame into a tree walk.
MAX_DEPTH = 12
MAX_NODES = 4000

# Roles worth recording as OBJECTS. Deliberately narrow: an AX tree is full of
# layout scaffolding (AXGroup, AXSplitGroup) that names nothing a person would
# call an object, and recording those would inflate the object tier with
# structure rather than content — scoring better without seeing more.
_OBJECT_ROLES = {
    "AXButton", "AXRadioButton", "AXCheckBox", "AXMenuItem", "AXTextField",
    "AXTextArea", "AXLink", "AXImage", "AXPopUpButton", "AXTabGroup",
    "AXStaticText", "AXHeading", "AXCell", "AXRow", "AXDisclosureTriangle",
}

# Roles whose CONTENT is their value, not their name. For everything else the
# value is state (a checkbox's True) and the name is the title/description.
_TEXT_ROLES = {"AXTextField", "AXTextArea", "AXStaticText", "AXHeading"}

# The address bar identifies itself by DESCRIPTION, not by role — every
# browser has text fields, and only one of them is the URL. Matched
# case-insensitively on a substring because the exact string is localised
# ("Address and search bar" on this Mac).
_ADDRESS_BAR_HINTS = ("address and search", "address bar", "location",
                      "adresse", "url")

# Chromium decorates a tab's accessibility description with live STATE, not
# just its title. Measured on this Mac 2026-07-29:
#
#   'Fusion CDT - Sleeping - Memory usage - 90.0 MB'
#
# "Sleeping" and "90.0 MB" are facts about the browser's memory manager at one
# instant, not about what the founder was reading. Left in, they would enter
# the graph as part of the thing's NAME, so the same tab would be a different
# entity every time its memory figure changed — and a node whose identity
# drifts with an irrelevant number can never accumulate evidence.
#
# The stripping is deliberately narrow: only these exact trailing annotations,
# only at the end. A real title containing " - " is untouched.
_TAB_STATE_SUFFIXES = ("sleeping", "audio playing", "audio muted",
                       "memory usage", "high memory usage")

_FUNCS = [
    ("AXUIElementCreateApplication", b"@i"),
    ("AXUIElementCopyAttributeValue", b"i@@o^@"),
    ("AXIsProcessTrusted", b"Z"),
]

_ns: Optional[dict] = None


def _bind() -> Optional[dict]:
    """Bind the AX C functions once, or answer None forever after."""
    global _ns
    if _ns is not None:
        return _ns or None
    try:
        import objc
        from Foundation import NSBundle
        bundle = NSBundle.bundleWithPath_(
            "/System/Library/Frameworks/ApplicationServices.framework")
        ns: dict = {}
        objc.loadBundleFunctions(bundle, ns, _FUNCS)
        if "AXUIElementCopyAttributeValue" not in ns:
            _ns = {}
            return None
        _ns = ns
        return ns
    except Exception:
        _ns = {}
        return None


def clean_tab_title(description: str) -> str:
    """Strip Chromium's live-state annotations from a tab or window title.

    'Fusion CDT - Sleeping - Memory usage - 90.0 MB'  -> 'Fusion CDT'
    '... - YouTube - Audio playing - High memory usage - 1.3 GB - Brave (Private)'
        -> '... - YouTube - Brave (Private)'

    SEGMENTS ARE FILTERED, NOT POPPED FROM THE TAIL, and a real window title is
    why. The first version only stripped trailing annotations, which is what a
    TAB looks like — but a WINDOW title puts the browser's own name last, so
    the state sits in the MIDDLE and nothing was removed at all. Measured on
    the founder's screen, verbatim.

    Narrow on purpose: a segment is dropped only when the whole segment is a
    known annotation or a bare size. A real title containing " - " is
    untouched, which is most of the web.
    """
    parts = [p.strip() for p in str(description).split(" - ")]
    kept = []
    for seg in parts:
        low = seg.lower()
        if low in _TAB_STATE_SUFFIXES:
            continue
        if _is_bare_size(low):
            continue
        kept.append(seg)
    return " - ".join(kept).strip() or str(description).strip()


def _is_bare_size(text: str) -> bool:
    """'90.0 MB', '1.3 GB' — a memory figure, never part of a name."""
    for unit in ("mb", "kb", "gb"):
        if text.endswith(unit):
            head = text[: -len(unit)].strip()
            if head.replace(".", "", 1).isdigit():
                return True
    return False


def is_trusted() -> Optional[bool]:
    """Has the user granted Accessibility? None if we cannot even ask."""
    ns = _bind()
    if ns is None:
        return None
    try:
        return bool(ns["AXIsProcessTrusted"]())
    except Exception:
        return None


def _attr(copy, el, name):
    try:
        err, value = copy(el, name, None)
    except Exception:
        return None
    return value if err == 0 else None


def _window_or_none(copy, el):
    """`el` if it really is a window. See the call site for what this cost."""
    if el is None:
        return None
    role = _attr(copy, el, "AXRole")
    return el if role is not None and str(role) == "AXWindow" else None


def _app_named(pid: int) -> str:
    """The app's own name for a pid, so a multi-window reading is attributable."""
    try:
        from AppKit import NSRunningApplication
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        return str(app.localizedName() or "") if app else ""
    except Exception:                                    # noqa: BLE001
        return ""


def _frontmost():
    """(name, pid) of the frontmost app, or None."""
    try:
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return None
        return str(app.localizedName() or ""), int(app.processIdentifier())
    except Exception:
        return None


def _on_screen_pids() -> Optional[set]:
    """PIDs the WINDOW SERVER says own a normal on-screen window.

    This is the occlusion cross-check's first half and it is not optional:
    the AX tree describes windows the founder may not be able to see, and a
    memory that holds something he never saw is a false memory.
    """
    try:
        import Quartz
        info = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly
            | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID)
    except Exception:
        return None
    pids = set()
    for w in (info or []):
        # Layer 0 is the ordinary window layer. Menus, the Dock and overlays
        # live above it and are not what a person means by "what I was
        # looking at".
        if w.get("kCGWindowLayer") == 0 and w.get("kCGWindowOwnerPID"):
            pids.add(int(w["kCGWindowOwnerPID"]))
    return pids


# What fraction of a window must actually be showing before its contents
# count as SEEN. A window with a two-pixel sliver poking out from behind
# another was not read by a person, and recording it would put something in
# memory the founder never looked at — the one thing this sense exists to
# never do. UNCALIBRATED.
VISIBLE_FRACTION = 0.05

# How many windows one reading may cover. A person with thirty windows open
# was not reading thirty windows; and each one is a synchronous cross-process
# tree walk on the thread that also captures.
MAX_WINDOWS = 6


def read_visible_contexts(limit: int = MAX_WINDOWS) -> List[Dict[str, object]]:
    """Every window the founder can ACTUALLY SEE, not just the focused one.

    WHY THIS EXISTS, and it is a measured cost rather than a nice-to-have. The
    focused-window-only read made `_read_beats_guess` suppress OCR sites in
    any episode where an address bar had been read — and on 5,973 of the
    founder's real claims that discarded two shop sites (names
    withheld), which may well be sites the owner really visited in a
    window the read did not cover. The rule was right; its evidence was too
    narrow. Read every visible window and the rule stops guessing, because
    everything true has been read.

    OCCLUSION IS THE WHOLE DIFFICULTY. "Open" is not "seen": a window fully
    behind another is present to the accessibility API and invisible to a
    person, and a memory that holds what he could not see is a false memory.
    `capture/window_stack.visible_regions` already computes the geometry —
    built, tested, and until now unwired — so this asks it rather than
    inventing a second answer.
    """
    ns = _bind()
    if ns is None or not is_trusted():
        return []
    try:
        from tracemem.capture.window_stack import (read_stack, visible_area,
                                                   visible_regions)
    except Exception:                                    # noqa: BLE001
        return []
    try:
        snap = read_stack()
        windows = [w for w in snap.windows if w.layer == 0 and w.area > 0]
        regions = visible_regions(windows)
    except Exception:                                    # noqa: BLE001
        return []

    # Front to back, so the window a person is most likely reading is first
    # and the budget is spent on what is nearest the top of the stack.
    visible = []
    for w in sorted(windows, key=lambda w: w.z):
        try:
            frac = visible_area(w.window_id, regions) / w.area
        except Exception:                                # noqa: BLE001
            continue
        if frac < VISIBLE_FRACTION:
            continue                    # present, not seen
        visible.append((w, frac))
        if len(visible) >= limit:
            break

    out: List[Dict[str, object]] = []
    for window_info, frac in visible:
        kwargs = {}
        if getattr(window_info, "title", ""):
            kwargs["_window_title"] = window_info.title
        reading = read_focused_context(
            _pid=window_info.pid, _app_name="",
            _skip_onscreen_check=True, **kwargs)
        if reading:
            reading = dict(reading)
            reading["source_pid"] = window_info.pid
            reading["window_id"] = str(window_info.window_id)
            reading["window_rect_points"] = tuple(
                getattr(window_info, "rect", ())) or None
            reading["visible_fraction"] = float(frac)
            out.append(reading)
    return out


def read_focused_context(_pid: Optional[int] = None,
                         _app_name: str = "",
                         _skip_onscreen_check: bool = False,
                         _window_title: str = ""
                         ) -> Optional[Dict[str, object]]:
    """What the front window IS, read from the OS. None when unknown.

    Returns:
        {"app": str, "window_title": str|None, "url": str|None,
         "tabs": [str], "objects": [{"role": str, "label": str}]}

    `_pid` targets a specific application instead of the frontmost one. It is
    UNDERSCORED BECAUSE PRODUCTION MUST NEVER PASS IT: a window that is not
    frontmost is a window the founder is not looking at, and recording it
    would put something in memory he never saw. It exists so the URL and tab
    extraction can be verified against a real browser without stealing the
    focus of whoever is at the machine — which would itself have corrupted
    the capture it was meant to check.
    """
    ns = _bind()
    if ns is None or not is_trusted():
        return None
    if _pid is not None:
        app_name, pid = (_app_name or _app_named(int(_pid))
                         or "(targeted)"), int(_pid)
    else:
        front = _frontmost()
        if front is None:
            return None
        app_name, pid = front

    # `read_visible_contexts` has already proven visibility with real
    # geometry, which is strictly stronger than "owns something on screen".
    on_screen = None if _skip_onscreen_check else _on_screen_pids()
    if on_screen is not None and pid not in on_screen:
        # The frontmost app owns nothing visible (every window minimised).
        # Measured on Brave 2026-07-29: AX answers zero windows with a
        # SUCCESS code and the window server agrees. Nothing was seen, so
        # nothing is reported — that is a true absence, not a failure.
        return None

    create = ns["AXUIElementCreateApplication"]
    copy = ns["AXUIElementCopyAttributeValue"]
    try:
        app_el = create(pid)
    except Exception:
        return None

    # The FOCUSED window specifically. AXWindows would hand back everything
    # the app owns, including windows behind other apps.
    #
    # AND ITS ROLE IS CHECKED, because AXFocusedWindow does not always return
    # a window. Measured on this Mac 2026-07-29: asking a non-frontmost
    # Microsoft Edge answered err=0 with an **AXApplication** element
    # (a different pid — a helper process). Walking it produced 2,250
    # "objects", every one of them a menu item from the Apple menu: "About
    # This Mac", "Force Quit"... A memory that recorded those as what the
    # founder was looking at would be confidently, unfalsifiably wrong, and
    # the object-tier score would have gone UP while the sense got worse.
    window = None
    if _window_title:
        wanted = clean_tab_title(str(_window_title))
        for candidate in (_attr(copy, app_el, "AXWindows") or []):
            candidate = _window_or_none(copy, candidate)
            if candidate is None:
                continue
            title = _attr(copy, candidate, "AXTitle")
            if title and clean_tab_title(str(title)) == wanted:
                window = candidate
                break
    if window is None:
        window = _window_or_none(copy, _attr(copy, app_el, "AXFocusedWindow"))
    if window is None:
        for candidate in (_attr(copy, app_el, "AXWindows") or []):
            window = _window_or_none(copy, candidate)
            if window is not None:
                break
    if window is None:
        return None

    objects: List[Dict[str, str]] = []
    url: Optional[str] = None
    tabs: List[str] = []
    budget = [MAX_NODES]

    def walk(el, depth: int) -> None:
        nonlocal url
        if depth > MAX_DEPTH or budget[0] <= 0:
            return
        budget[0] -= 1
        role = _attr(copy, el, "AXRole")
        role = str(role) if role else ""
        title = _attr(copy, el, "AXTitle")
        value = _attr(copy, el, "AXValue")
        desc = _attr(copy, el, "AXDescription")
        desc_s = str(desc) if desc else ""

        if role == "AXTextField" and value is not None:
            low = desc_s.lower()
            if any(h in low for h in _ADDRESS_BAR_HINTS):
                candidate = str(value).strip()
                if candidate and url is None:
                    url = candidate
        if role == "AXRadioButton" and desc_s:
            # Chromium models each tab as a radio button in the tab bar; the
            # description is the tab's title plus live state (see
            # _TAB_STATE_SUFFIXES for why the state has to come off).
            cleaned = clean_tab_title(desc_s)
            if cleaned:
                tabs.append(cleaned)

        if role in _OBJECT_ROLES:
            # WHICH FIELD IS THE OBJECT'S NAME DEPENDS ON THE ROLE, and
            # getting it wrong is not cosmetic. A blanket `title or desc or
            # value` recorded the address bar as "Address and search bar" —
            # the widget's own name — and threw the URL away, which is the
            # single fact this whole module exists to capture. Caught by
            # test_objects_are_enumerated_with_their_roles.
            if role in _TEXT_ROLES:
                label = value if value is not None else (title or desc)
            elif role == "AXRadioButton" and desc_s:
                label = clean_tab_title(desc_s)      # a tab, in Chromium
            else:
                label = title or desc or value
            if label is not None and not isinstance(label, bool):
                s = str(label).strip()
                if 1 < len(s) <= 120:
                    objects.append({"role": role, "label": s})

        for kid in (_attr(copy, el, "AXChildren") or []):
            walk(kid, depth + 1)

    try:
        walk(window, 0)
    except Exception:
        return None

    window_title = _attr(copy, window, "AXTitle")
    # A WINDOW title is decorated exactly like a tab description — measured:
    # '... - YouTube - Audio playing - High memory usage - 1.3 GB - Brave
    # (Private)'. Left alone, the same window is a different thing to the
    # graph every time its memory figure moves.
    return {
        "app": app_name,
        "window_title": (clean_tab_title(str(window_title))
                         if window_title else None),
        "url": url,
        "tabs": tabs,
        "objects": objects,
        "truncated": budget[0] <= 0,
    }
