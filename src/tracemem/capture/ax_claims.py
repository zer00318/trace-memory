"""OWNS: turning one Accessibility reading into claims.

Kept apart from `ax_tree.py` on purpose: that module talks to macOS, this one
decides what a reading is WORTH. The split is what lets the claim shape be
tested with no browser, no Mac and no permission grant — and the claim shape
is the part that ends up in the founder's memory forever.

WHAT BECOMES A CLAIM, and what does not:

  · **the URL** — the whole reason this sense exists. `esa.com` appeared 13
    times in the founder's real store and `chess.com` twice, because OCR
    misreads a domain the same wrong way every time it sees it. The address
    bar holds the string. One claim, exact.
  · **the window title** — free, and the one label almost every app gets right
    about itself.
  · **each object** — the object tier measured 4.3%, the worst number in the
    product. These are what the ceiling means by "objects".
  · **NOT the tab list.** Every open tab is a page the founder is NOT looking
    at. Recording nine of them per reading would put eight things into memory
    he never had on screen, and this product's whole claim is that it does not
    hold what you did not see. The tabs are carried on the URL claim's
    structured payload as CONTEXT — retrievable, never a sighting.

SCOPE IS 'frame'. An AX reading describes one window at one instant, exactly
like a frame read, and `specs/claim_match.md` A4 wants that stamped at birth
because it can never be added later.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from tracemem.domain import (Claim, Confidence, CoordinateStack, CaptureCoord,
                             Modality, Provenance, SpanCoord, WorldCoord)
from tracemem.domain.grading import grade_for

# One reading can describe a very busy window. The founder's own browser
# produced 31 objects; a large IDE or spreadsheet produces thousands, and a
# thousand claims per reading would drown the store in one afternoon of
# scrolling. Truncation is ANNOUNCED on the claim, because a short object list
# otherwise reads as "there was little on screen".
MAX_OBJECT_CLAIMS = 40


def _coord(t_ms: int, device_id: str, app: str, place_key: str,
           frame_id: str, window: str = "") -> CoordinateStack:
    return CoordinateStack(
        world=WorldCoord(place_key=place_key),
        capture=CaptureCoord(frame_id=frame_id, device_id=device_id, app=app,
                             window=window or None),
        span=SpanCoord(bbox=(0.0, 0.0, 0.0, 0.0)),
        t_ms=t_ms,
    )


def _claim(text: str, t_ms: int, device_id: str, app: str, place_key: str,
           frame_id: str, structured: Dict[str, object],
           window: str = "") -> Claim:
    return Claim(
        text=text,
        coord=_coord(t_ms, device_id, app, place_key, frame_id, window),
        provenance=Provenance(perceiver="ax", modality=Modality.SCREEN),
        # THE table, never a literal. See domain/grading.py for why 'ax' sits
        # at VERBATIM alongside 'ocr' even though it is strictly better.
        grade=grade_for("ax"),
        # 1.0 WITHOUT HEDGING, and this is the point of the whole sense. OCR
        # reports how sure it is because it might have misread; there is no
        # misreading here — the string was handed over, not recognised.
        confidence=Confidence(1.0),
        structured=structured,
        bidder="ax:focused-window", bid_id="ax:%d" % t_ms,
        bid_predicted_value=1.0, actual_cost=0.0,
        scope="frame",
    )


def claims_from_reading(reading: Optional[dict], t_ms: int,
                        device_id: str = "mac-1",
                        place_key: str = "system",
                        frame_id: str = "") -> List[Claim]:
    """One AX reading -> the claims it earns. `None` in, nothing out."""
    if not reading:
        return []
    app = str(reading.get("app") or "unknown")
    window = str(reading.get("window_id") or reading.get("window_title") or "")
    fid = frame_id or "ax-%d" % t_ms
    out: List[Claim] = []

    url = reading.get("url")
    if url:
        tabs = [str(t) for t in (reading.get("tabs") or [])]
        out.append(_claim(
            str(url), t_ms, device_id, app, place_key, fid,
            {"ax": "url",
             # Context, not sightings — see the module docstring. Carried so a
             # question about the session can reach them, never promoted into
             # things the founder looked at.
             "tabs_open": tabs[:50],
             "source_pid": reading.get("source_pid"),
             "window_rect_points": reading.get("window_rect_points"),
             "visible_fraction": reading.get("visible_fraction")}, window))

    title = reading.get("window_title")
    if title:
        out.append(_claim(str(title), t_ms, device_id, app, place_key, fid,
                          {"ax": "window_title",
                           "source_pid": reading.get("source_pid"),
                           "window_rect_points": reading.get("window_rect_points"),
                           "visible_fraction": reading.get("visible_fraction")}, window))

    objects = list(reading.get("objects") or [])
    truncated = bool(reading.get("truncated")) or len(objects) > MAX_OBJECT_CLAIMS
    for ordinal, obj in enumerate(objects[:MAX_OBJECT_CLAIMS]):
        label = str(obj.get("label") or "").strip()
        if not label:
            continue
        out.append(_claim(label, t_ms, device_id, app, place_key, fid,
                          {"ax": "object", "role": str(obj.get("role") or ""),
                           "ordinal": ordinal,
                           "source_pid": reading.get("source_pid"),
                           "window_rect_points": reading.get("window_rect_points"),
                           "visible_fraction": reading.get("visible_fraction")}, window))
    if truncated and out:
        # Said on the reading, not swallowed. A silently short object list is
        # indistinguishable from an empty screen.
        first = out[0]
        structured = dict(first.structured or {})
        structured["objects_truncated"] = True
        out[0] = Claim(
            text=first.text, coord=first.coord, provenance=first.provenance,
            grade=first.grade, confidence=first.confidence,
            structured=structured, bidder=first.bidder, bid_id=first.bid_id,
            bid_predicted_value=first.bid_predicted_value,
            actual_cost=first.actual_cost, scope=first.scope)
    return out
