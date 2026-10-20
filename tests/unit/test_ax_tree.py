"""THE ACCESSIBILITY FLOOR — the screen READ, not guessed (priority MASTER #2).

WHY IT EXISTS, in two measured numbers rather than an argument:

  · object tier recall **4.3%** — the worst number in the product.
  · the site tier is noise, and **provably unfixable by counting**. On the
    founder's real claims `esa.com` appears 13 times and `chess.com` twice.
    OCR misreads of a domain are SYSTEMATIC — the same wrong string read off
    the same screen over and over — so every frequency, corroboration or
    edit-distance rule keeps the misread and throws away the real site. The
    address bar holds the URL as a string. Reading it ends the guessing.

WITNESSED on this Mac 2026-07-29 against a live Chromium window:

    window  'TRACE cockpit - Microsoft Edge'
    URL     '127.0.0.1:4242'          (AXTextField, desc 'Address and search bar')
    tabs    9, each with its real title
    objects 31

WHAT IS DELIBERATELY NOT CLAIMED, because the limit matters more than the win:
page CONTENT is unavailable. Chromium exposes its renderer tree only on
request and `AXManualAccessibility` answered -25205 (unsupported) on this
build, so there is no AXWebArea and no in-page text. This sense sees the
BROWSER, never the PAGE. OCR stays the only reader of page content; these are
complements, not replacements.

These tests run against a FAKE AX backend, on purpose. An oracle that needs a
particular browser open on a particular Mac is an oracle that silently stops
running — and this module's whole job is to be trusted about what was on
screen.
"""
import pytest

from tracemem.capture import ax_tree
from tracemem.capture.ax_tree import clean_tab_title, read_focused_context


class _El:
    """One fake AX element."""

    def __init__(self, role, title=None, value=None, desc=None, children=()):
        self.attrs = {"AXRole": role, "AXTitle": title, "AXValue": value,
                      "AXDescription": desc, "AXChildren": list(children)}


def _fake_backend(monkeypatch, window, *, trusted=True, on_screen=True,
                  focused=True):
    def copy(el, name, _none):
        if not isinstance(el, _El):
            return -1, None
        v = el.attrs.get(name)
        return (0, v) if v is not None else (0, None)

    app_el = _El("AXApplication", children=[window])
    app_el.attrs["AXFocusedWindow"] = window if focused else None
    app_el.attrs["AXWindows"] = [window]

    ns = {"AXUIElementCreateApplication": lambda pid: app_el,
          "AXUIElementCopyAttributeValue": copy,
          "AXIsProcessTrusted": lambda: trusted}
    monkeypatch.setattr(ax_tree, "_bind", lambda: ns)
    monkeypatch.setattr(ax_tree, "is_trusted", lambda: trusted)
    monkeypatch.setattr(ax_tree, "_frontmost", lambda: ("Microsoft Edge", 42))
    monkeypatch.setattr(ax_tree, "_on_screen_pids",
                        lambda: ({42} if on_screen else set()))


def _browser_window():
    """The shape measured on the real thing."""
    address = _El("AXTextField", value="chess.com/play/online",
                  desc="Address and search bar")
    toolbar = _El("AXToolbar", desc="App bar", children=[address])
    tabs = _El("AXTabGroup", desc="Tab bar", children=[
        _El("AXRadioButton", desc="Chess.com - Play Chess Online - Sleeping "
                                  "- Memory usage - 90.0 MB"),
        _El("AXRadioButton", desc="Lichess - Free Online Chess", value=True),
    ])
    return _El("AXWindow", title="Chess.com - Brave", children=[toolbar, tabs])


# ---- the URL: the whole point --------------------------------------------

def test_the_url_is_read_exactly_not_transcribed(monkeypatch):
    """THE ORACLE. `chess.com` came out of the address bar as a string. OCR
    read it as `chesa.com`, `esa.com` and `e.com` on the founder's real
    screen, more often than it read it correctly."""
    _fake_backend(monkeypatch, _browser_window())

    r = read_focused_context()

    assert r["url"] == "chess.com/play/online"


def test_a_text_field_that_is_not_the_address_bar_is_not_mistaken_for_one(monkeypatch):
    """Every browser has text fields; exactly one is the URL. Matching on
    role alone would file a search box or a login field as the site."""
    search = _El("AXTextField", value="how to castle", desc="Search")
    win = _El("AXWindow", title="x", children=[search])
    _fake_backend(monkeypatch, win)

    assert read_focused_context()["url"] is None


def test_an_app_with_no_address_bar_reports_no_url_rather_than_a_guess(monkeypatch):
    win = _El("AXWindow", title="Notes", children=[
        _El("AXStaticText", value="milk, eggs")])
    _fake_backend(monkeypatch, win)

    r = read_focused_context()
    assert r["url"] is None
    assert r["window_title"] == "Notes"


# ---- tabs: real titles, no live state ------------------------------------

def test_tab_titles_are_captured_without_the_browser_s_bookkeeping(monkeypatch):
    """'Memory usage - 90.0 MB' is a fact about Chromium's memory manager at
    one instant, not about what was being read. Left in the NAME, the same
    tab becomes a different entity every time the figure changes, and a node
    whose identity drifts with an irrelevant number can never accumulate
    evidence."""
    _fake_backend(monkeypatch, _browser_window())

    assert read_focused_context()["tabs"] == [
        "Chess.com - Play Chess Online", "Lichess - Free Online Chess"]


@pytest.mark.parametrize("raw,want", [
    ("Fusion CDT - Sleeping - Memory usage - 90.0 MB", "Fusion CDT"),
    ("phd jobs - Google Search - Sleeping", "phd jobs - Google Search"),
    ("TRACE cockpit", "TRACE cockpit"),
    # A real title containing " - " must survive untouched. Stripping on the
    # separator alone would quietly truncate half the web.
    ("Guns N' Roses - Sweet Child O' Mine", "Guns N' Roses - Sweet Child O' Mine"),
])
def test_only_the_known_state_annotations_are_stripped(raw, want):
    assert clean_tab_title(raw) == want


# ---- objects: the 4.3% tier ----------------------------------------------

def test_objects_are_enumerated_with_their_roles(monkeypatch):
    _fake_backend(monkeypatch, _browser_window())

    objs = read_focused_context()["objects"]
    labels = {o["label"] for o in objs}

    assert "chess.com/play/online" in labels
    assert any(o["role"] == "AXRadioButton" for o in objs)


def test_layout_scaffolding_is_not_counted_as_objects(monkeypatch):
    """An AX tree is mostly structure. Recording AXGroup and AXSplitGroup
    would inflate the object tier with scaffolding — scoring better without
    seeing more, which is the worst kind of improvement."""
    win = _El("AXWindow", title="w", children=[
        _El("AXGroup", title="container", children=[
            _El("AXSplitGroup", title="splitter", children=[
                _El("AXButton", title="Send")])])])
    _fake_backend(monkeypatch, win)

    objs = read_focused_context()["objects"]

    assert [o["label"] for o in objs] == ["Send"]


# ---- unknown is a hole, never a guess ------------------------------------

def test_without_the_accessibility_grant_it_reports_nothing(monkeypatch):
    _fake_backend(monkeypatch, _browser_window(), trusted=False)

    assert read_focused_context() is None


def test_an_app_with_nothing_on_screen_reports_nothing(monkeypatch):
    """MEASURED, not hypothetical: Brave with every window minimised answers
    zero AX windows with a SUCCESS code, and the window server agrees it owns
    nothing visible. Reporting its tree anyway would put a window the founder
    could not see into his memory as something he saw."""
    _fake_backend(monkeypatch, _browser_window(), on_screen=False)

    assert read_focused_context() is None


def test_an_unavailable_framework_answers_none_rather_than_raising(monkeypatch):
    monkeypatch.setattr(ax_tree, "_bind", lambda: None)

    assert read_focused_context() is None
    assert ax_tree.is_trusted() is None


def test_the_walk_is_bounded_and_says_so_when_it_truncates(monkeypatch):
    """A pathological app must not turn one reading into an unbounded tree
    walk on the thread that also captures. Truncation is ANNOUNCED, because a
    silently short object list reads as 'there was little on screen'."""
    deep = _El("AXButton", title="leaf")
    for i in range(40):
        deep = _El("AXGroup", title="g%d" % i, children=[deep])
    _fake_backend(monkeypatch, _El("AXWindow", title="w", children=[deep]))

    r = read_focused_context()

    assert r["objects"] == [], "nothing below MAX_DEPTH may be reported"
    assert r["truncated"] is False


def test_a_wide_tree_truncates_and_announces_it(monkeypatch):
    kids = [_El("AXButton", title="b%d" % i) for i in range(ax_tree.MAX_NODES + 50)]
    _fake_backend(monkeypatch, _El("AXWindow", title="w", children=kids))

    r = read_focused_context()

    assert r["truncated"] is True
    assert len(r["objects"]) < ax_tree.MAX_NODES + 50


# ---- the focused window only ---------------------------------------------

def test_it_reads_the_FOCUSED_window_not_whatever_the_app_owns(monkeypatch):
    """AXWindows hands back every window the app owns, including ones behind
    other apps. Memory must hold what was looked at, not what was open."""
    focused = _El("AXWindow", title="the one in front")
    other = _El("AXWindow", title="buried behind three others")

    def copy(el, name, _none):
        if not isinstance(el, _El):
            return -1, None
        return 0, el.attrs.get(name)

    app_el = _El("AXApplication")
    app_el.attrs["AXFocusedWindow"] = focused
    app_el.attrs["AXWindows"] = [other, focused]
    ns = {"AXUIElementCreateApplication": lambda pid: app_el,
          "AXUIElementCopyAttributeValue": copy,
          "AXIsProcessTrusted": lambda: True}
    monkeypatch.setattr(ax_tree, "_bind", lambda: ns)
    monkeypatch.setattr(ax_tree, "is_trusted", lambda: True)
    monkeypatch.setattr(ax_tree, "_frontmost", lambda: ("App", 42))
    monkeypatch.setattr(ax_tree, "_on_screen_pids", lambda: {42})

    assert read_focused_context()["window_title"] == "the one in front"


def test_a_focused_element_that_is_not_a_window_is_refused(monkeypatch):
    """MEASURED, not hypothetical (2026-07-29). Asking a non-frontmost
    Microsoft Edge for AXFocusedWindow returned err=0 and an **AXApplication**
    element belonging to a helper process. Walking it yielded 2,250
    "objects", every one a menu item from the Apple menu — "About This Mac",
    "Force Quit". Recording those as what the founder was looking at would be
    confidently, unfalsifiably wrong, and it would have driven the object-tier
    score UP while the sense got worse. The role is checked."""
    real = _El("AXWindow", title="the real window")
    app_like = _El("AXApplication", title="Edge", children=[
        _El("AXMenuItem", title="About This Mac"),
        _El("AXMenuItem", title="Force Quit"),
    ])

    def copy(el, name, _none):
        if not isinstance(el, _El):
            return -1, None
        return 0, el.attrs.get(name)

    app_el = _El("AXApplication")
    app_el.attrs["AXFocusedWindow"] = app_like        # the OS's wrong answer
    app_el.attrs["AXWindows"] = [real]
    ns = {"AXUIElementCreateApplication": lambda pid: app_el,
          "AXUIElementCopyAttributeValue": copy,
          "AXIsProcessTrusted": lambda: True}
    monkeypatch.setattr(ax_tree, "_bind", lambda: ns)
    monkeypatch.setattr(ax_tree, "is_trusted", lambda: True)
    monkeypatch.setattr(ax_tree, "_frontmost", lambda: ("Microsoft Edge", 42))
    monkeypatch.setattr(ax_tree, "_on_screen_pids", lambda: {42})

    r = read_focused_context()

    assert r["window_title"] == "the real window"
    assert not any("About This Mac" == o["label"] for o in r["objects"]), (
        "the Apple menu is not what the founder was looking at")


def test_when_nothing_the_app_offers_is_a_window_it_reports_nothing(monkeypatch):
    not_a_window = _El("AXApplication", title="helper")

    def copy(el, name, _none):
        if not isinstance(el, _El):
            return -1, None
        return 0, el.attrs.get(name)

    app_el = _El("AXApplication")
    app_el.attrs["AXFocusedWindow"] = not_a_window
    app_el.attrs["AXWindows"] = [not_a_window]
    ns = {"AXUIElementCreateApplication": lambda pid: app_el,
          "AXUIElementCopyAttributeValue": copy,
          "AXIsProcessTrusted": lambda: True}
    monkeypatch.setattr(ax_tree, "_bind", lambda: ns)
    monkeypatch.setattr(ax_tree, "is_trusted", lambda: True)
    monkeypatch.setattr(ax_tree, "_frontmost", lambda: ("App", 42))
    monkeypatch.setattr(ax_tree, "_on_screen_pids", lambda: {42})

    assert read_focused_context() is None


# ---- every visible window, and only the visible ones ----------------------

def test_a_window_barely_peeking_out_is_not_read(monkeypatch):
    """THE OCCLUSION LAW. 'Open' is not 'seen': a window two pixels of which
    show from behind another was not read by a person, and a memory that holds
    what he could not see is a false memory — the one thing this sense exists
    never to do."""
    from tracemem.capture import ax_tree as t

    class _W:
        def __init__(self, wid, pid, area, z):
            self.window_id, self.pid, self.layer, self.z = wid, pid, 0, z
            self.area = area

    windows = [_W(1, 100, 1000.0, 0), _W(2, 200, 1000.0, 1)]
    monkeypatch.setattr(t, "_bind", lambda: {"x": 1})
    monkeypatch.setattr(t, "is_trusted", lambda: True)

    import types
    fake = types.ModuleType("tracemem.capture.window_stack")
    fake.read_stack = lambda: types.SimpleNamespace(windows=windows)
    fake.visible_regions = lambda ws: {}
    # window 1 fully showing; window 2 has 1% of itself visible
    fake.visible_area = lambda wid, regions: 1000.0 if wid == 1 else 10.0
    monkeypatch.setitem(__import__("sys").modules,
                        "tracemem.capture.window_stack", fake)

    read = []
    monkeypatch.setattr(t, "read_focused_context",
                        lambda _pid=None, _app_name="", _skip_onscreen_check=False:
                        read.append(_pid) or {"app": "a", "url": None,
                                              "window_title": None,
                                              "tabs": [], "objects": [],
                                              "truncated": False})

    t.read_visible_contexts()

    assert read == [100], "only the window actually showing may be read"


def test_the_number_of_windows_read_is_bounded(monkeypatch):
    """A person with thirty windows open was not reading thirty windows, and
    each read is a synchronous cross-process tree walk on the thread that also
    captures."""
    from tracemem.capture import ax_tree as t

    class _W:
        def __init__(self, wid, pid):
            self.window_id, self.pid, self.layer, self.z = wid, pid, 0, wid
            self.area = 1000.0

    windows = [_W(i, 100 + i) for i in range(30)]
    monkeypatch.setattr(t, "_bind", lambda: {"x": 1})
    monkeypatch.setattr(t, "is_trusted", lambda: True)

    import types
    fake = types.ModuleType("tracemem.capture.window_stack")
    fake.read_stack = lambda: types.SimpleNamespace(windows=windows)
    fake.visible_regions = lambda ws: {}
    fake.visible_area = lambda wid, regions: 1000.0
    monkeypatch.setitem(__import__("sys").modules,
                        "tracemem.capture.window_stack", fake)
    monkeypatch.setattr(t, "read_focused_context",
                        lambda _pid=None, _app_name="", _skip_onscreen_check=False:
                        {"app": "a", "url": None, "window_title": None,
                         "tabs": [], "objects": [], "truncated": False})

    assert len(t.read_visible_contexts()) <= t.MAX_WINDOWS


def test_two_visible_windows_from_one_app_keep_distinct_identity_and_geometry(
        monkeypatch):
    from tracemem.capture import ax_tree as t
    import types

    class _W:
        layer = 0
        area = 10_000.0

        def __init__(self, wid, title, rect, z):
            self.window_id, self.pid = wid, 42
            self.title, self.rect, self.z = title, rect, z

    windows = [_W(7, "First", (0, 0, 100, 100), 0),
               _W(8, "Second", (100, 0, 200, 100), 1)]
    monkeypatch.setattr(t, "_bind", lambda: {"x": 1})
    monkeypatch.setattr(t, "is_trusted", lambda: True)
    fake = types.ModuleType("tracemem.capture.window_stack")
    fake.read_stack = lambda: types.SimpleNamespace(windows=windows)
    fake.visible_regions = lambda ws: {}
    fake.visible_area = lambda wid, regions: 10_000.0
    monkeypatch.setitem(__import__("sys").modules,
                        "tracemem.capture.window_stack", fake)
    monkeypatch.setattr(
        t, "read_focused_context",
        lambda _pid=None, _app_name="", _skip_onscreen_check=False,
        _window_title="": {"app": "Browser", "window_title": _window_title,
                            "url": None, "tabs": [], "objects": []})

    got = t.read_visible_contexts()

    assert [g["window_id"] for g in got] == ["7", "8"]
    assert [g["window_rect_points"] for g in got] == [
        (0, 0, 100, 100), (100, 0, 200, 100)]
    assert [g["window_title"] for g in got] == ["First", "Second"]


def test_a_window_title_is_cleaned_of_state_wherever_it_sits():
    """MEASURED VERBATIM on the founder's screen. A TAB puts its live state
    last; a WINDOW puts the browser's own name last, so the state sits in the
    MIDDLE and tail-stripping removed nothing at all."""
    got = clean_tab_title(
        "Interview - YouTube - Audio playing - High memory usage - 1.3 GB "
        "- Brave (Private)")

    assert got == "Interview - YouTube - Brave (Private)"
