"""OWNS: the raw stream's short home (map: Extraction / Retina).

Nobody above this layer thinks in frames: consumers ask scene_now() /
stream_since(). Pins hold a scene for an in-flight deep look; pins have a
byte budget and an expiry clock so a stuck helper can never freeze the
ring. Eviction is oldest-first among unpinned records. The ring KNOWS its
own byte weight, always. Pure: the payload is opaque — the ring never
inspects pixels, so silicon capture plugs in later without touching this.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_BUDGET_BYTES = 256 * 1024 * 1024   # ring ceiling; pressure may shrink it
PIN_BUDGET_FRAC = 0.5                      # pins may hold at most half the ring
PIN_TTL_MS = 30_000                        # abandoned pins die on this clock

_token_counter = itertools.count(1)


@dataclass(frozen=True)
class FrameRecord:
    t_ms: int
    display: str
    frame: Any          # opaque payload
    nbytes: int
    # Scale stamping atom (01_world): the backing-scale factor this frame was
    # grabbed at, carried WITH the frame so the points conversion never has to
    # guess which display it came from. 1.0 = non-Retina (and the safe default
    # for synthetic/fixture frames, which have no physical display at all).
    scale: float = 1.0

    def __post_init__(self) -> None:
        if self.scale <= 0:
            raise ValueError(f"frame scale must be positive, got {self.scale!r}")


@dataclass(frozen=True)
class Pin:
    token: str
    born_ms: int
    records: Tuple[FrameRecord, ...]

    @property
    def nbytes(self) -> int:
        return sum(r.nbytes for r in self.records)


class Retina:
    def __init__(self, budget_bytes: int = DEFAULT_BUDGET_BYTES):
        self.budget_bytes = budget_bytes
        self._ring: List[FrameRecord] = []
        self._pins: Dict[str, Pin] = {}

    # -- accounting: the ring knows its own weight -------------------------
    @property
    def bytes_used(self) -> int:
        return sum(r.nbytes for r in self._ring) + self.bytes_pinned

    @property
    def bytes_pinned(self) -> int:
        return sum(p.nbytes for p in self._pins.values())

    def _pinned_records(self) -> set:
        return {id(r) for p in self._pins.values() for r in p.records}

    # -- intake ------------------------------------------------------------
    def push(self, record: FrameRecord) -> List[FrameRecord]:
        """Land a frame; evict unpinned-oldest until it fits. Returns evicted."""
        evicted: List[FrameRecord] = []
        while self._ring and self.bytes_used + record.nbytes > self.budget_bytes:
            evicted.append(self._ring.pop(0))
        self._ring.append(record)
        return evicted

    def shrink(self, new_budget_bytes: int) -> List[FrameRecord]:
        """Memory pressure: give bytes back BEFORE the Mac swaps."""
        self.budget_bytes = new_budget_bytes
        evicted: List[FrameRecord] = []
        while self._ring and self.bytes_used > self.budget_bytes:
            evicted.append(self._ring.pop(0))
        return evicted

    # -- the only doors upward --------------------------------------------
    def scene_now(self, display: Optional[str] = None) -> Optional[FrameRecord]:
        for r in reversed(self._ring):
            if display is None or r.display == display:
                return r
        return None

    def stream_since(self, t_ms: int,
                     display: Optional[str] = None) -> List[FrameRecord]:
        return [r for r in self._ring if r.t_ms >= t_ms
                and (display is None or r.display == display)]

    # -- pinning: deep looks hold their scene, bounded ---------------------
    def pin(self, since_ms: int, now_ms: int,
            display: Optional[str] = None) -> Optional[Pin]:
        """Pin the scene for a deep look. Refuses (loudly: None) over budget."""
        records = tuple(self.stream_since(since_ms, display))
        if not records:
            return None
        cost = sum(r.nbytes for r in records)
        if self.bytes_pinned + cost > self.budget_bytes * PIN_BUDGET_FRAC:
            return None                       # refusal is loud, never silent
        pin = Pin(f"pin-{next(_token_counter)}", now_ms, records)
        # pinned records leave the ring: they now survive eviction
        keep = {id(r) for r in records}
        self._ring = [r for r in self._ring if id(r) not in keep]
        self._pins[pin.token] = pin
        return pin

    def unpin(self, token: str) -> bool:
        return self._pins.pop(token, None) is not None

    def expire_pins(self, now_ms: int) -> List[str]:
        """Abandoned pins die on the clock. Returns tokens (witness these)."""
        dead = [t for t, p in self._pins.items()
                if now_ms - p.born_ms >= PIN_TTL_MS]
        for t in dead:
            del self._pins[t]
        return dead
