"""ORACLE for Fullscreen flag / Active Space / Title reader (01_world)."""
from tracemem.capture.context_stream import ContextStream
from tracemem.capture.focus_context import (
    FocusSense, SpaceTracker, is_fullscreen, titles_available,
)
from tracemem.capture.window_stack import StackSnapshot, WindowInfo

DISPLAY = [(0.0, 0.0, 1512.0, 982.0)]           # this Mac's real panel, in points


def win(wid, rect=(0, 0, 1512, 982), layer=0, title="t", owner="App"):
    return WindowInfo(window_id=wid, owner=owner, pid=100 + wid, title=title,
                      layer=layer, rect=rect, z=wid)


def menubar():
    return WindowInfo(window_id=999, owner="Window Server", pid=1,
                      title="Menubar", layer=24, rect=(0, 0, 1512, 37), z=0)


def snap(windows, t_ms=1000):
    return StackSnapshot(t_ms=t_ms, windows=tuple(windows))


# -- fullscreen ------------------------------------------------------------
def test_fullscreen_app_sets_the_flag():
    """The atom's PROOF, verbatim: fullscreen app → flag true."""
    w = win(1)
    assert is_fullscreen(w, DISPLAY, [w]) is True


def test_maximized_window_with_a_menubar_is_not_fullscreen():
    """THE distinction the two-part rule exists for: same geometry, and the
    menubar is what tells them apart."""
    w = win(1)
    assert is_fullscreen(w, DISPLAY, [w, menubar()]) is False


def test_small_window_is_not_fullscreen():
    w = win(1, rect=(100, 100, 800, 600))
    assert is_fullscreen(w, DISPLAY, [w]) is False


def test_overlay_layer_is_never_fullscreen():
    w = win(1, layer=3)
    assert is_fullscreen(w, DISPLAY, [w]) is False


def test_no_frontmost_window_is_not_fullscreen():
    assert is_fullscreen(None, DISPLAY, []) is False


def test_fullscreen_survives_a_one_point_rounding_edge():
    """Window server geometry is not always exact to the point."""
    w = win(1, rect=(0.5, 0.5, 1511.5, 981.5))
    assert is_fullscreen(w, DISPLAY, [w]) is True


# -- titles / the permission hole -----------------------------------------
def test_titles_present_reads_available():
    assert titles_available([win(1, title="Inbox"), win(2, title="")]) is True


def test_no_titles_anywhere_reads_as_the_permission_hole():
    """The silent degradation this field exists to prevent: without it, a
    revoked grant looks identical to a screen full of untitled windows."""
    assert titles_available([win(1, title=""), win(2, title="")]) is False


def test_untitled_overlay_alone_does_not_accuse_the_os():
    """Layer>0 chrome routinely has no title; only real windows count."""
    assert titles_available([menubar()]) is True


def test_empty_machine_makes_no_accusation():
    assert titles_available([]) is True


def test_degraded_stream_continues_app_only_and_says_so():
    """The atom's PROOF: revoke Accessibility → app-only, declared."""
    ctx = FocusSense().read(snap([win(1, title="", owner="Brave Browser")]),
                            DISPLAY)
    assert ctx.app == "Brave Browser"          # the stream continues
    assert ctx.window == ""
    assert ctx.titles_available is False        # ...and says so


# -- active space ----------------------------------------------------------
def test_switching_spaces_produces_a_distinct_context_value():
    """The atom's PROOF, verbatim."""
    t = SpaceTracker()
    a = t.observe([win(1), win(2), win(3)])
    b = t.observe([win(10), win(11), win(12)])
    assert a != b


def test_returning_to_a_space_recovers_its_id():
    t = SpaceTracker()
    a = t.observe([win(1), win(2), win(3)])
    t.observe([win(10), win(11), win(12)])
    assert t.observe([win(1), win(2), win(3)]) == a


def test_opening_one_window_is_not_a_space_switch():
    """Without overlap matching, every new window would look like a new
    Space and the field would be noise."""
    t = SpaceTracker()
    a = t.observe([win(1), win(2), win(3)])
    assert t.observe([win(1), win(2), win(3), win(4)]) == a


def test_closing_one_window_is_not_a_space_switch():
    t = SpaceTracker()
    a = t.observe([win(1), win(2), win(3), win(4)])
    assert t.observe([win(1), win(2), win(3)]) == a


def test_a_space_signature_tracks_gradual_drift():
    """Windows churn all day; the signature must follow its Space.

    AUDIT FIX 2026-07-21: this test used to drift the set by TWO of three
    windows per step and demand the id survive. Tolerating that much churn
    is what let the tracker fold unrelated Spaces into one — the auditor
    showed two disjoint window sets both returning id 0. Gradual drift
    (one window at a time) is what a Space actually does; a wholesale swap
    is a different Space, and is now treated as one below."""
    t = SpaceTracker()
    a = t.observe([win(1), win(2), win(3)])
    assert t.observe([win(1), win(2), win(3), win(4)]) == a
    assert t.observe([win(2), win(3), win(4)]) == a
    assert t.observe([win(2), win(3), win(4), win(5)]) == a


def test_a_wholesale_swap_is_a_different_space():
    """The bug the audit found: after drift, a DISJOINT window set matched
    the drifted Space and got its id."""
    t = SpaceTracker()
    a = t.observe([win(1), win(2), win(3)])
    t.observe([win(1), win(2), win(3), win(4)])
    t.observe([win(2), win(3), win(4)])
    assert t.observe([win(10), win(11), win(12)]) != a


def test_a_subset_does_not_score_a_perfect_match():
    """A Space showing one sticky all-Desktops window must not be read as
    the big Space it is a subset of."""
    t = SpaceTracker()
    a = t.observe([win(1), win(2), win(3), win(4), win(5)])
    assert t.observe([win(1)]) != a


def test_no_windows_yields_no_space_claim():
    assert SpaceTracker().observe([]) is None


def test_menubar_alone_is_not_a_space():
    assert SpaceTracker().observe([menubar()]) is None


# -- the assembled reading -------------------------------------------------
def test_focus_context_is_edge_triggered_in_the_stream():
    """A polled focus sense must not emit on every sample."""
    sense, cs = FocusSense(), ContextStream()
    windows = [win(1, owner="Editor", title="notes.md")]
    first = cs.observe("focus", sense.read(snap(windows), DISPLAY).as_reading(), 1000)
    assert first
    assert cs.observe("focus", sense.read(snap(windows), DISPLAY).as_reading(),
                      2000) == []


def test_switching_apps_emits_the_change_with_its_previous():
    sense, cs = FocusSense(), ContextStream()
    cs.observe("focus", sense.read(snap([win(1, owner="Editor")]), DISPLAY)
               .as_reading(), 1000)
    events = cs.observe("focus", sense.read(snap([win(1, owner="Terminal")]),
                                            DISPLAY).as_reading(), 2000)
    app = [e for e in events if e.key == "app"]
    assert app and app[0].value == "Terminal" and app[0].previous == "Editor"


def test_live_read_of_this_machine():
    ctx = FocusSense().read()
    assert ctx.app, "no frontmost application on a running Mac"
    assert isinstance(ctx.fullscreen, bool)


# -- the second-display fullscreen bug the audit found ---------------------
TWO_DISPLAYS = [(0.0, 0.0, 1512.0, 982.0), (1512.0, 0.0, 3072.0, 982.0)]


def test_fullscreen_on_a_second_display_is_detected():
    """AUDIT FIX 2026-07-21: with origins discarded, a fullscreen window on
    the right-hand monitor matched nothing and read as not-fullscreen."""
    w = win(1, rect=(1512.0, 0.0, 3072.0, 982.0))
    assert is_fullscreen(w, TWO_DISPLAYS, [w]) is True


def test_a_window_spanning_neither_display_fully_is_not_fullscreen():
    w = win(1, rect=(1000.0, 0.0, 2000.0, 982.0))       # straddles the seam
    assert is_fullscreen(w, TWO_DISPLAYS, [w]) is False


def test_second_display_fullscreen_still_needs_the_menubar_gone():
    w = win(1, rect=(1512.0, 0.0, 3072.0, 982.0))
    assert is_fullscreen(w, TWO_DISPLAYS, [w, menubar()]) is False


# -- AUDIT 2026-07-28: the OS owns "which app", not the topmost window -----
def test_a_focused_app_with_no_window_does_not_borrow_someone_elses():
    """The third failure of the live guard, as a deterministic test.

    Brave holds the keyboard but has no ordinary on-screen window; Claude's
    window is topmost. Reporting 'Claude' would silently mis-attribute every
    claim the eye writes for as long as that state lasts."""
    other = win(1, owner="Claude", title="a chat")
    snap_ = snap([other])
    ctx = FocusSense().read(snap_, DISPLAY, focus_pid=999)   # 999 owns nothing
    assert ctx.window == "", "borrowed another app's window title"


def test_the_window_is_used_when_it_belongs_to_the_focused_app():
    mine = win(7, owner="Editor", title="notes.md")
    ctx = FocusSense().read(snap([mine]), DISPLAY, focus_pid=mine.pid)
    assert ctx.window == "notes.md" and ctx.pid == mine.pid
