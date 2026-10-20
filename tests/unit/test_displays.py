"""ORACLE for Display identity (01_world).

The atom's PROOF is a replug — hardware-gated, and NOT claimed here. What
these tests prove is the property the replug would exercise: the key is a
function of the panel's own EDID fields and of nothing session-scoped, so
a changed CGDirectDisplayID cannot change it. The live test reads this
Mac's real panel.
"""
import json

import pytest

from tracemem.capture.displays import (
    DisplayIdentity, DisplayRegistry, identity_key, read_displays,
)


def ident(**kw):
    base = dict(key="k", vendor=0x610, model=0xA04F, serial=0xFD5B01A2,
                builtin=False, points_w=1512.0, points_h=982.0,
                pixels_w=3024, pixels_h=1964, display_id=1)
    base.update(kw)
    base["key"] = identity_key(base["vendor"], base["model"], base["serial"],
                               base["builtin"])
    return DisplayIdentity(**base)


# -- the identity itself ---------------------------------------------------
def test_key_ignores_the_transient_display_id():
    """THE atom: same panel, new session handle, same key."""
    before = ident(display_id=1)
    after = ident(display_id=7)
    assert before.key == after.key


def test_different_panels_get_different_keys():
    assert ident(serial=1).key != ident(serial=2).key
    assert ident(model=1).key != ident(model=2).key


def test_builtin_has_a_stable_human_key():
    assert identity_key(1, 2, 3, builtin=True) == "builtin"


def test_scale_is_derived_from_the_panels_own_geometry():
    assert ident().scale == 2.0
    assert ident(pixels_w=1512, points_w=1512.0).scale == 1.0


# -- the collision the design admits to ------------------------------------
def test_identical_panels_collide_and_say_so():
    """Two monitors from one batch reporting serial 0 is a real thing. The
    tiebreak is allowed; pretending it is a strong identity is not."""
    from tracemem.capture.displays import _disambiguate
    rows = [
        {"key": "0610-a04f-00000000", "vendor": 0x610, "model": 0xA04F,
         "serial": 0, "builtin": False, "unit": 0, "weak_identity": False},
        {"key": "0610-a04f-00000000", "vendor": 0x610, "model": 0xA04F,
         "serial": 0, "builtin": False, "unit": 1, "weak_identity": False},
    ]
    out = _disambiguate(rows)
    assert out[0]["key"] != out[1]["key"]
    assert all(r["weak_identity"] for r in out)


def test_unique_panels_are_not_marked_weak():
    from tracemem.capture.displays import _disambiguate
    rows = [
        {"key": "a", "vendor": 1, "model": 1, "serial": 1, "builtin": False,
         "unit": 0, "weak_identity": False},
        {"key": "b", "vendor": 1, "model": 1, "serial": 2, "builtin": False,
         "unit": 1, "weak_identity": False},
    ]
    assert not any(r["weak_identity"] for r in _disambiguate(rows))


# -- the registry ----------------------------------------------------------
def test_registry_auto_names_so_capture_never_blocks(tmp_path):
    reg = DisplayRegistry(tmp_path / "displays.json")
    assert reg.name_for(ident(builtin=True)) == "built-in display"
    assert reg.name_for(ident(serial=99)) == "display 2"


def test_registry_persists_the_founders_vocabulary(tmp_path):
    path = tmp_path / "displays.json"
    reg = DisplayRegistry(path)
    key = ident().key
    reg.name_for(ident())
    reg.rename(key, "left monitor")
    assert DisplayRegistry(path).known()[key] == "left monitor"


def test_same_panel_keeps_its_name_across_a_replug(tmp_path):
    """What the atom is actually for: Tuesday's left monitor and today's
    are the same thing to a question about Tuesday."""
    path = tmp_path / "displays.json"
    reg = DisplayRegistry(path)
    reg.rename(ident(display_id=1).key, "left monitor")
    assert reg.name_for(ident(display_id=42)) == "left monitor"


def test_corrupt_registry_relearns_rather_than_crashing(tmp_path):
    path = tmp_path / "displays.json"
    path.write_text("{not json")
    assert DisplayRegistry(path).known() == {}


# -- live read of this Mac -------------------------------------------------
def test_live_read_finds_this_macs_real_panel():
    # FLAKE CLASS FIX 2026-07-22: several live tests assumed a display is
    # always attached and awake. When this Mac's screen slept mid-session,
    # three of them failed at once — and every one was reporting the truth
    # ("there is no display") as a defect. A sleeping screen is a fact about
    # the environment; the code under test never got a chance to be wrong.
    #
    # Skipping rather than asserting is what keeps these guards worth
    # having: a suite that goes red when the founder walks away teaches
    # everyone to re-run it, and then it teaches nothing.
    displays = read_displays()
    if not displays:
        pytest.skip("no display attached or the screen is asleep")
    d = displays[0]
    assert d.points_w > 0 and d.pixels_w > 0
    assert d.scale >= 1.0
    assert d.key


def test_live_keys_are_unique_per_panel():
    keys = [d.key for d in read_displays()]
    assert len(keys) == len(set(keys))


# -- the multi-display origin bug the audit found --------------------------
def test_display_rect_carries_its_origin(tmp_path):
    """AUDIT FIX 2026-07-21: the origin used to be discarded, so every panel
    was treated as starting at (0,0). A fullscreen window on a monitor to
    the right — rect (1512, 0, 3072, 982) — then matched no display and read
    as not-fullscreen. Always False, silently, and only on the multi-display
    setup this whole section exists for."""
    d = ident(display_id=2)
    from dataclasses import replace as _replace
    right = _replace(d, origin_x=1512.0, origin_y=0.0,
                     points_w=1560.0, points_h=982.0)
    assert right.rect == (1512.0, 0.0, 3072.0, 982.0)


def test_primary_display_rect_starts_at_the_origin():
    assert ident().rect == (0.0, 0.0, 1512.0, 982.0)


def test_live_display_rects_are_disjoint():
    """Real panels never overlap in the global desktop space."""
    rects = [d.rect for d in read_displays()]
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            assert not (a[0] < b[2] and b[0] < a[2]
                        and a[1] < b[3] and b[1] < a[3]), \
                f"overlapping display rects: {a} {b}"
