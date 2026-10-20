"""OWNS: the coordinate stack — world ⊃ capture ⊃ span (spec 00 §1).

The anti-silent-loss type: a Claim cannot exist without the full STRUCTURE
(inner fields may be None when a sensor is absent; the layers may not be).
INVARIANT C1: bbox normalized [0,1], well-ordered. C2: all three layers present.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class WorldCoord:
    gps: Optional[Tuple[float, float]] = None
    pose: Optional[Tuple[float, ...]] = None
    place_key: Optional[str] = None


@dataclass(frozen=True)
class CaptureCoord:
    """WHERE a claim was captured, in one of TWO worlds (SCHEMA §2.1 WHERE):

    - LIVED  — observed on a screen: it carries a `frame_id`.
    - OWNED  — read from the user's own corpus (a file, a mail, a history
               row): it carries a `source` locator (path / message-id / url).

    The device that captured or read is ALWAYS known (`device_id`). A claim
    must know which world it came from: exactly one of `frame_id`/`source` is
    set — never neither (that would be a claim from nowhere, the silent-loss
    class C2 exists to forbid), never both (a fact cannot be simultaneously
    seen and read).
    """
    device_id: str
    frame_id: Optional[str] = None       # LIVED: the screen frame
    source: Optional[str] = None         # OWNED: the corpus locator
    app: Optional[str] = None
    window: Optional[str] = None

    def __post_init__(self) -> None:
        has_frame = self.frame_id is not None
        has_source = self.source is not None
        if has_frame == has_source:
            raise ValueError(
                "CaptureCoord must carry exactly one of frame_id (LIVED) or "
                "source (OWNED); got frame_id=%r source=%r"
                % (self.frame_id, self.source))

    @classmethod
    def lived(cls, frame_id: str, device_id: str,
              app: Optional[str] = None, window: Optional[str] = None) -> "CaptureCoord":
        """A screen observation — the LIVED world."""
        return cls(device_id=device_id, frame_id=frame_id, app=app, window=window)

    @classmethod
    def owned(cls, source: str, device_id: str,
              app: Optional[str] = None) -> "CaptureCoord":
        """A read from the user's own corpus — the OWNED world. `source` is the
        re-readable locator (absolute path / message-id / url)."""
        return cls(device_id=device_id, source=source, app=app)


@dataclass(frozen=True)
class SpanCoord:
    bbox: Optional[Tuple[float, float, float, float]] = None  # normalized x0,y0,x1,y1

    def __post_init__(self) -> None:
        if self.bbox is not None:
            x0, y0, x1, y1 = self.bbox
            if not all(0.0 <= v <= 1.0 for v in self.bbox):
                raise ValueError("bbox must be normalized to [0,1]")
            if x0 > x1 or y0 > y1:
                raise ValueError("bbox must be well-ordered (x0<=x1, y0<=y1)")


@dataclass(frozen=True)
class CoordinateStack:
    world: WorldCoord
    capture: CaptureCoord
    span: SpanCoord
    t_ms: int

    def __post_init__(self) -> None:
        # C2: the STRUCTURE is mandatory — a stack built without real layer
        # objects is a construction error, not a warning.
        if not isinstance(self.world, WorldCoord):
            raise TypeError("world layer must be a WorldCoord")
        if not isinstance(self.capture, CaptureCoord):
            raise TypeError("capture layer must be a CaptureCoord")
        if not isinstance(self.span, SpanCoord):
            raise TypeError("span layer must be a SpanCoord")
