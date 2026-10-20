"""OWNS: place_key — the co-location discriminator (spec 03 §5, ruling A2).

Composite, confidence-graded: SSID+GPS agreement > private SSID > GPS cell >
per-device (fail-safe: never wrongly merge what we can't confirm together).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import FrozenSet, Optional, Tuple

GPS_CELL_DECIMALS = 3  # ~110m cells


@dataclass(frozen=True)
class SensorCtx:
    device_id: str
    ssid: Optional[str] = None
    gps: Optional[Tuple[float, float]] = None


def gps_cell(gps: Tuple[float, float]) -> str:
    return "%.*f,%.*f" % (GPS_CELL_DECIMALS, gps[0], GPS_CELL_DECIMALS, gps[1])


def _h(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def place_key(ctx: SensorCtx, private_ssids: FrozenSet[str] = frozenset(),
              public_ssids: FrozenSet[str] = frozenset()) -> Tuple[str, float]:
    """Returns (key, co_location_confidence). A2 ruling in full."""
    if ctx.ssid and ctx.gps:
        if ctx.ssid in public_ssids:
            return "cell:" + gps_cell(ctx.gps), 0.7
        return "wifi:" + _h(ctx.ssid), 0.95
    if ctx.ssid:
        if ctx.ssid in private_ssids:
            return "wifi:" + _h(ctx.ssid), 0.9
        return "dev:" + ctx.device_id, 0.0     # unknown SSID, no GPS: never merge
    if ctx.gps:
        return "cell:" + gps_cell(ctx.gps), 0.7
    return "dev:" + ctx.device_id, 0.0
