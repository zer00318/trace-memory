"""OWNS: the activation math — lazy decay, surprise-scaled boost, 1-hop spread
(spec 04 §2). Pure functions; the store applies the returned deltas.

The economy's core property: no O(N) sweep exists. Decay is computed on read;
updates happen only on events; spread touches at most degree(node) neighbors.
"""
from __future__ import annotations

from typing import Dict, Iterable, Tuple

HALF_LIFE_MS = 7 * 24 * 3600 * 1000  # τ: 7 days (tunable, spec 09 A1 telemetry)
BASE_BOOST = {"sighting": 0.30, "query_hit": 0.20, "combined": 0.25}
SPREAD_ALPHA = 0.4


def activation_now(stored: float, last_active_t_ms: int, t_ms: int,
                   half_life_ms: int = HALF_LIFE_MS) -> float:
    if t_ms <= last_active_t_ms:
        return stored
    return stored * 2.0 ** (-(t_ms - last_active_t_ms) / float(half_life_ms))


def boost(stored: float, last_active_t_ms: int, t_ms: int,
          event: str, surprise: float) -> float:
    """New activation after an event; surprise∈[0,1] doubles the boost at max."""
    base = BASE_BOOST[event] * (1.0 + surprise)
    return min(1.0, activation_now(stored, last_active_t_ms, t_ms) + base)


def spread_deltas(boost_amount: float,
                  neighbor_weights: Iterable[Tuple[str, float]],
                  ) -> Dict[str, float]:
    """1-hop spread: neighbor gets boost*α*(w/max_w). Strictly non-recursive."""
    pairs = list(neighbor_weights)
    if not pairs:
        return {}
    max_w = max(w for _, w in pairs)
    if max_w <= 0:
        return {}
    return {nid: boost_amount * SPREAD_ALPHA * (w / max_w) for nid, w in pairs}
