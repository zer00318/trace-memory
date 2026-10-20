"""ORACLE for Window z-order snapshot + Visible-region map (01_world).

Geometry gets synthetic window lists (the brief's PROOF); the sampler gets
a live read of this Mac's real window server.
"""
import pytest

from tracemem.capture.window_stack import (
    StackSnapshot, WindowInfo, read_stack, subtract, visible_area,
    visible_regions, window_at,
)


def win(wid, rect, z, layer=0, owner="App", title=""):
    return WindowInfo(window_id=wid, owner=owner, pid=100 + wid, title=title,
                      layer=layer, rect=rect, z=z)


def total_area(rects):
    return sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in rects)


# -- rectangle subtraction: the piece everything else rests on -------------
def test_no_overlap_leaves_the_rect_whole():
    assert subtract((0, 0, 10, 10), (20, 20, 30, 30)) == [(0, 0, 10, 10)]


def test_full_cover_leaves_nothing():
    assert subtract((0, 0, 10, 10), (0, 0, 10, 10)) == []


def test_a_hole_in_the_middle_makes_four_disjoint_pieces():
    pieces = subtract((0, 0, 10, 10), (4, 4, 6, 6))
    assert len(pieces) == 4
    assert total_area(pieces) == 100 - 4        # 96, the true remainder
    # disjoint: no two pieces share area
    for i, a in enumerate(pieces):
        for b in pieces[i + 1:]:
            assert not (a[0] < b[2] and b[0] < a[2]
                        and a[1] < b[3] and b[1] < a[3])


def test_corner_cover_conserves_area():
    pieces = subtract((0, 0, 10, 10), (5, 5, 15, 15))
    assert total_area(pieces) == 75


def test_band_cover_conserves_area():
    pieces = subtract((0, 0, 10, 10), (-5, 4, 15, 6))
    assert total_area(pieces) == 80


# -- visible regions -------------------------------------------------------
def test_frontmost_window_is_never_carved():
    ws = [win(1, (0, 0, 100, 100), z=0), win(2, (0, 0, 100, 100), z=1)]
    assert visible_regions(ws)[1] == [(0, 0, 100, 100)]


def test_fully_covered_window_is_visible_nowhere():
    """[] not a missing key: 'present but invisible' is the whole point."""
    ws = [win(1, (0, 0, 100, 100), z=0), win(2, (10, 10, 50, 50), z=1)]
    regions = visible_regions(ws)
    assert regions[2] == []
    assert visible_area(2, regions) == 0.0


def test_partial_cover_leaves_the_true_remainder():
    ws = [win(1, (50, 0, 100, 100), z=0), win(2, (0, 0, 100, 100), z=1)]
    assert visible_area(2, visible_regions(ws)) == 5000.0


def test_three_deep_stack_carves_cumulatively():
    ws = [win(1, (0, 0, 50, 100), z=0),
          win(2, (50, 0, 100, 100), z=1),
          win(3, (0, 0, 100, 100), z=2)]
    assert visible_area(3, visible_regions(ws)) == 0.0


# -- the WHERE lookup the peripheral consumes ------------------------------
def test_point_lookup_names_the_visible_owner_not_the_frontmost():
    """THE mis-citation trap: a small overlay sits on top, but the point
    being asked about belongs to the window behind it."""
    overlay = win(1, (0, 0, 20, 20), z=0, owner="Notification")
    editor = win(2, (0, 0, 100, 100), z=1, owner="Editor")
    ws = [overlay, editor]
    assert window_at((10, 10), ws).owner == "Notification"
    assert window_at((60, 60), ws).owner == "Editor"


def test_point_on_the_desktop_owns_nothing():
    ws = [win(1, (0, 0, 10, 10), z=0)]
    assert window_at((500, 500), ws) is None


def test_lookup_is_exclusive_on_the_far_edge():
    ws = [win(1, (0, 0, 10, 10), z=0)]
    assert window_at((9.99, 9.99), ws) is not None
    assert window_at((10.0, 10.0), ws) is None


# -- the live sampler ------------------------------------------------------
def test_live_snapshot_reads_the_real_window_server():
    snap = read_stack()
    assert isinstance(snap, StackSnapshot)
    assert snap.windows, "the window server reported zero on-screen windows"
    assert snap.t_ms > 0
    for w in snap.windows:
        x0, y0, x1, y1 = w.rect
        assert x1 >= x0 and y1 >= y0, f"disordered rect from the OS: {w}"


def test_frontmost_skips_offscreen_windows():
    """AUDIT FIX 2026-07-21: frontmost() used to take the first layer-0
    window in CGWindowList order. On this Mac that is an off-screen browser
    helper at y=-73 with no title, so the focus sense reported window=""
    while claiming titles were available — and silently disagreed with the
    older attention_mac tap, which reported the real title."""
    offscreen = win(1, (0.0, -73.0, 1512.0, -32.0), z=0, title="")
    real = win(2, (0.0, 37.0, 1512.0, 982.0), z=1, title="Inbox")
    snap = StackSnapshot(t_ms=1, windows=(offscreen, real))
    assert snap.frontmost([(0.0, 0.0, 1512.0, 982.0)]).title == "Inbox"


def test_frontmost_skips_zero_area_windows():
    snap = StackSnapshot(t_ms=1, windows=(win(1, (0, 0, 0, 0), z=0),
                                          win(2, (0, 0, 100, 100), z=1)))
    assert snap.frontmost().window_id == 2


def test_frontmost_falls_back_rather_than_returning_nothing():
    """A wrong window beats no window for a sense whose job is 'where are
    you' — but only after the on-screen filter finds nothing at all."""
    only = win(1, (5000.0, 5000.0, 5100.0, 5100.0), z=0)
    snap = StackSnapshot(t_ms=1, windows=(only,))
    assert snap.frontmost([(0.0, 0.0, 1512.0, 982.0)]).window_id == 1


def test_live_frontmost_agrees_with_the_existing_attention_tap():
    """The regression guard: two senses on one machine must not disagree
    about which window is in front.

    RE-AUDIT 2026-07-21: this used to assert on the APP name only, which
    passed straight over a live failure — both the untitled chrome strip
    and the real window belong to Brave. Asserting the TITLE is what makes
    the guard able to fail.

    FLAKE FIX 2026-07-22: this failed once in a full-suite run and passed
    alone. It reads the stack and the tap at two different instants, so ANY
    focus change in between — a notification, a background app, the founder
    switching windows while the suite runs — made the two senses "disagree"
    about a machine that was behaving perfectly.

    A racy guard is worse than none: it teaches everyone to re-run until
    green, and the day it catches a real disagreement it gets re-run too.
    So the stack read is now BRACKETED by two tap reads, and the comparison
    only happens when the brackets match — proof that focus held still for
    the whole measurement. If focus never settles, the test skips with the
    reason, because that is a fact about the environment and not about the
    code under test.
    """
    import time

    from tracemem.capture.displays import read_displays
    from tracemem.extraction.attention_mac import attention_span

    rects = [d.rect for d in read_displays()]
    # Same flake class as test_displays: with the screen asleep there is no
    # display and no frontmost window, so the two senses have nothing to
    # agree or disagree ABOUT. The bracket below would happily "hold still"
    # on two empty reads and then fail on front is None — reporting an
    # absent screen as a disagreement between senses.
    if not rects:
        pytest.skip("no display attached or the screen is asleep")

    # AUDIT 2026-07-28 — THE TEST WAS ASSERTING SOMETHING UNTRUE OF macOS.
    # Bracketing was not the real problem. Two full-suite runs produced
    # STABLE disagreements ('Claude' vs 'Brave Browser', then 'Brave
    # Browser' vs 'Microsoft Edge') with all four bracketed reads agreeing
    # that nothing had moved. Neither sense was broken. They answer
    # different questions — NSWorkspace reports who owns the KEYBOARD,
    # CGWindowList reports what is visually ON TOP — and those diverge
    # whenever one app holds focus under another app's window.
    #
    # So the senses are reconciled at the SOURCE (frontmost(focus_pid=...)
    # now lets the OS decide the app and uses z-order only to pick which of
    # that app's windows), and this test passes the tap's pid. What it
    # proves is now a real property — given the same app, both senses
    # choose the same window — instead of a coincidence that held whenever
    # focus and z-order happened to line up.
    for _attempt in range(5):
        before = attention_span()
        front = read_stack().frontmost(rects, focus_pid=before.pid)
        after = attention_span()
        if (before.app, before.window, before.pid) == (after.app, after.window,
                                                       after.pid):
            break
        time.sleep(0.05)
    else:
        pytest.skip("focus never held still across a read — environment, "
                    "not the senses")

    tap = after
    assert front is not None
    if front.pid != tap.pid:
        # The focused app has no ordinary on-screen window (minimised, all
        # closed, a menubar agent), so frontmost() fell back to somebody
        # else's. There is no window of THEIRS to agree about — and the
        # production sense handles this by reporting the OS's app with an
        # empty window rather than borrowing this one (see FocusSense.read).
        pytest.skip("focused app has no on-screen window of its own")
    assert front.owner == tap.app
    if tap.window:                    # the tap saw a title; we must see it too
        assert front.title == tap.window, (
            f"senses disagree: stack says {front.title!r} at {front.rect}, "
            f"tap says {tap.window!r}")


def test_frontmost_prefers_a_titled_window_over_untitled_chrome():
    """The live shape this Mac actually produces: an on-screen, positive-area,
    untitled chrome strip sitting above the real window. The geometry filter
    alone passes it through."""
    chrome = win(5, (0.0, 37.0, 1512.0, 150.0), z=0, title="")
    real = win(6, (0.0, 37.0, 1512.0, 982.0), z=1, title="Inbox")
    snap = StackSnapshot(t_ms=1, windows=(chrome, real))
    assert snap.frontmost([(0.0, 0.0, 1512.0, 982.0)]).title == "Inbox"


def test_frontmost_still_returns_an_untitled_window_when_that_is_all_there_is():
    """Plenty of real windows have no title; preferring titled must not mean
    requiring it."""
    only = win(1, (0.0, 37.0, 800.0, 600.0), z=0, title="")
    snap = StackSnapshot(t_ms=1, windows=(only,))
    assert snap.frontmost([(0.0, 0.0, 1512.0, 982.0)]).window_id == 1


def test_live_regions_never_exceed_their_window():
    """Carving can only remove area, never add it — the invariant that
    would catch a subtraction bug on real, messy window arrangements."""
    snap = read_stack()
    regions = visible_regions(snap.windows)
    for w in snap.windows:
        assert visible_area(w.window_id, regions) <= w.area + 1e-6


def test_snapshot_carries_a_timestamp_we_chose():
    assert read_stack(t_ms=12345).t_ms == 12345
