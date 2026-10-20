"""OWNS: stable display identity (map: World / Screen stream / Multi-display;
atom: Display identity).

THE PROBLEM: CGDirectDisplayID is a SESSION handle, not an identity. Unplug
a monitor and plug it back in and the same physical panel can arrive with a
different ID; IDs are also reused. A FrameRecord.display carrying that
number means "left monitor" silently stops being the same thing across a
replug — and every frame before and after still looks perfectly valid. This
is the invisible-failure class the atom names.

THE BRIEF asked for CGDisplay UUIDs. WITNESSED 2026-07-21, this Mac's venv:
`Quartz.CGDisplayCreateUUIDFromDisplayID` is NOT exposed by this pyobjc
build (AttributeError). So identity is built from the panel's own EDID
fields instead — vendor + model + serial — which is what the UUID is
derived from anyway, and which survives replug for the same reason.

Its one honest weakness, declared rather than discovered later: two
identical monitors from one production batch can report the same triple
(some panels report serial 0). Such a collision is DETECTED here and
disambiguated by unit number, and the fallback is recorded on the record
itself so a reader can tell a strong identity from a weak one.

SPEC: 01 §2
ORACLE: tests/unit/test_displays.py — pure-fixture identity/collision
        tests, plus a live read of this Mac's real panel.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, List, Optional


@dataclass(frozen=True)
class DisplayIdentity:
    key: str                    # THE stable id — what FrameRecord.display carries
    vendor: int
    model: int
    serial: int
    builtin: bool
    points_w: float             # layout size, the space the points law speaks
    points_h: float
    pixels_w: int               # backing size
    pixels_h: int
    display_id: int             # the TRANSIENT session handle; never an identity
    weak_identity: bool = False  # True = EDID collision, key needed a tiebreak
    # Where this panel sits in the global desktop, in points. BUG FIXED
    # (audit 2026-07-21): the origin used to be discarded, so every display
    # was treated as starting at (0,0) and a fullscreen window on a monitor
    # to the right — rect (1512, 0, 3072, 982) — matched no display and read
    # as not-fullscreen. Always False, silently, and only on the multi-display
    # setup this section exists for.
    origin_x: float = 0.0
    origin_y: float = 0.0

    @property
    def rect(self) -> tuple:
        """This panel's rectangle in global desktop points."""
        return (self.origin_x, self.origin_y,
                self.origin_x + self.points_w, self.origin_y + self.points_h)

    @property
    def scale(self) -> float:
        """The panel's backing scale. A capture may still deliver something
        else (it can downscale), which is why FrameRecord stamps its OWN
        measured scale rather than reading this."""
        return (self.pixels_w / self.points_w) if self.points_w else 1.0


def identity_key(vendor: int, model: int, serial: int,
                 builtin: bool, unit: Optional[int] = None) -> str:
    """The stable key. `unit` participates ONLY as a collision tiebreak —
    unit numbers renumber on replug, so folding one in unconditionally
    would reintroduce the very instability this function exists to remove."""
    base = "builtin" if builtin else f"{vendor:04x}-{model:04x}-{serial:08x}"
    return base if unit is None else f"{base}#{unit}"


def _disambiguate(rows: List[dict]) -> List[dict]:
    """EDID collisions (identical panels, or serial 0) get a unit tiebreak,
    and are MARKED weak so nobody mistakes a guess for an identity."""
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["key"]] = counts.get(r["key"], 0) + 1
    out = []
    for r in rows:
        if counts[r["key"]] > 1:
            r = dict(r, key=identity_key(r["vendor"], r["model"], r["serial"],
                                         r["builtin"], r["unit"]),
                     weak_identity=True)
        out.append(r)
    return out


def read_displays() -> List[DisplayIdentity]:
    """Every active display, right now. Returns [] where Quartz is absent —
    an empty list is honest; a fabricated 'main' display is not."""
    try:
        import Quartz
    except Exception:
        return []

    err, ids, count = Quartz.CGGetActiveDisplayList(16, None, None)
    if err != 0 or not count:
        return []

    rows = []
    for did in list(ids)[:count]:
        builtin = bool(Quartz.CGDisplayIsBuiltin(did))
        bounds = Quartz.CGDisplayBounds(did)
        mode = Quartz.CGDisplayCopyDisplayMode(did)
        px_w = int(Quartz.CGDisplayModeGetPixelWidth(mode)) if mode else 0
        px_h = int(Quartz.CGDisplayModeGetPixelHeight(mode)) if mode else 0
        vendor = int(Quartz.CGDisplayVendorNumber(did))
        model = int(Quartz.CGDisplayModelNumber(did))
        serial = int(Quartz.CGDisplaySerialNumber(did))
        rows.append({
            "key": identity_key(vendor, model, serial, builtin),
            "vendor": vendor, "model": model, "serial": serial,
            "builtin": builtin, "unit": int(Quartz.CGDisplayUnitNumber(did)),
            "points_w": float(bounds.size.width),
            "points_h": float(bounds.size.height),
            "origin_x": float(bounds.origin.x),
            "origin_y": float(bounds.origin.y),
            "pixels_w": px_w or int(bounds.size.width),
            "pixels_h": px_h or int(bounds.size.height),
            "display_id": int(did), "weak_identity": False,
        })

    return [DisplayIdentity(
        key=r["key"], vendor=r["vendor"], model=r["model"], serial=r["serial"],
        builtin=r["builtin"], points_w=r["points_w"], points_h=r["points_h"],
        pixels_w=r["pixels_w"], pixels_h=r["pixels_h"],
        display_id=r["display_id"], weak_identity=r["weak_identity"],
        origin_x=r["origin_x"], origin_y=r["origin_y"])
        for r in _disambiguate(rows)]


class DisplayRegistry:
    """key → human name ("left monitor"), persisted.

    Names are the FOUNDER's vocabulary, not the OS's, and they are the whole
    reason identity has to be stable: "what was on the left monitor on
    Tuesday" is only answerable if Tuesday's left monitor and today's share
    a key. Unknown displays are auto-registered under a provisional name so
    capture never blocks on a naming ceremony.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else None
        self._names: Dict[str, str] = {}
        if self.path and self.path.exists():
            try:
                self._names = json.loads(self.path.read_text())
            except (ValueError, OSError):
                self._names = {}          # a corrupt registry re-learns names

    def name_for(self, ident: DisplayIdentity) -> str:
        if ident.key not in self._names:
            self._names[ident.key] = ("built-in display" if ident.builtin
                                      else f"display {len(self._names) + 1}")
            self._save()
        return self._names[ident.key]

    def rename(self, key: str, name: str) -> None:
        self._names[key] = name
        self._save()

    def known(self) -> Dict[str, str]:
        return dict(self._names)

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._names, indent=2, sort_keys=True))


def with_names(idents: List[DisplayIdentity],
               registry: DisplayRegistry) -> List[tuple]:
    return [(i, registry.name_for(i)) for i in idents]


__all__ = ["DisplayIdentity", "DisplayRegistry", "identity_key",
           "read_displays", "with_names", "replace"]


# -- mirroring: when the screen is not only yours -------------------------
#
# `Screen stream / Display changes / Projector / AirPlay`. macOS mirrors a
# display by giving it the same ORIGIN and SIZE as another — the two panels
# occupy one rectangle of the desktop because they are showing the same
# thing. That is detectable from the display list alone, with no extra API.
#
# THIS DOES NOT DECIDE ANYTHING. The founder already ruled on what happens
# while a screen is shared: CAPTURE EVERYTHING (board ruling ii). So this
# reports a fact and routes it; the privacy wall owns the decision, and
# putting that choice here would quietly relitigate a ruling in a helper.
#
# It is worth reporting even so. "You were mirroring to something" is a real
# fact about a moment — it is usually a meeting, a lecture, a demo — and a
# memory that cannot tell a private afternoon from a presented one is missing
# something a person would say first when describing their day.


def mirrored_groups(displays: List[DisplayIdentity]) -> List[List[str]]:
    """Groups of display keys that are showing the same rectangle.

    A group of one is not mirroring and is never returned. Anything larger
    means at least two panels are painted with the same content.
    """
    by_rect: Dict[tuple, List[str]] = {}
    for d in displays:
        # Rounded: a projector negotiating 1279.998 points is the same
        # rectangle as one reporting 1280, and floating-point equality would
        # call that a private screen.
        rect = (round(d.origin_x), round(d.origin_y),
                round(d.points_w), round(d.points_h))
        by_rect.setdefault(rect, []).append(d.key)
    return [sorted(keys) for keys in by_rect.values() if len(keys) > 1]


def is_mirroring(displays: List[DisplayIdentity]) -> bool:
    return bool(mirrored_groups(displays))


def mirroring_event(displays: List[DisplayIdentity]) -> Optional[dict]:
    """The fact, shaped for whoever routes it — or None when nothing is
    mirrored.

    None rather than a "mirroring: false" event, because an event stream that
    reports the absence of things is one nobody can read.
    """
    groups = mirrored_groups(displays)
    if not groups:
        return None
    external = [d.key for d in displays
                if not d.builtin and any(d.key in g for g in groups)]
    return {
        "mirroring": True,
        "groups": groups,
        # A built-in panel mirrored to an external one is the shape of a
        # presentation; two externals showing the same thing usually is too.
        "to_external": sorted(external),
        "detail": "screen mirrored to %d other panel(s)"
                  % sum(len(g) - 1 for g in groups),
    }
