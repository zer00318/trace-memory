"""OWNS: the gaze auction — CLOSED ENGINE (map: Extraction / The gaze
auction).

The digital execution of how a brain points an eye. One shared priority
map. Bottom-up value arrives free from peripheral events (the world's own
hand); top-down bids are posted by the brain and specialists and AGE
UPWARD (starvation fairness — hungers grow, loud regions can't win
forever). Each beat: the highest-value region wins the fovea — serial,
like saccades, never parallel. The meal is prepared ONCE and broadcast to
every matching diet simultaneously. Nobody pulls the river.

The weight function exists to serve ONE objective (LAW): minimize
gap-to-ceiling — feed whatever closes the distance to what the frontier
would have extracted from the same river.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from .diet import Bid, DietRegistry

Region = Tuple[int, int]

CHANGE_VALUE = 1.0        # bottom-up: a change event's worth on the map
DWELL_VALUE = 0.6         # bottom-up: stillness earning a deeper look
AGING_PER_S = 0.05        # starvation fairness: hunger grows per second
BID_TTL_MS = 600_000      # a hunger unmet for 10 minutes expires (witnessed)


@dataclass(frozen=True)
class Gaze:
    region: Region
    t_ms: int
    meal: Any                      # prepared ONCE
    served: Tuple[str, ...]        # every diet fed by this one gaze
    satisfied: Tuple[Bid, ...]     # hungers this gaze answered


class Auction:
    def __init__(self, registry: DietRegistry,
                 prepare: Callable[[Region], Any]):
        self._registry = registry
        self._prepare = prepare
        self._bottom_up: Dict[Region, float] = {}
        self._bids: List[Bid] = []
        self.prepare_calls = 0     # proof meals are never cooked twice

    # -- the two hands on the map -----------------------------------------
    def on_events(self, events) -> None:
        for e in events:
            worth = CHANGE_VALUE if e.kind == "change" else DWELL_VALUE
            self._bottom_up[e.region] = self._bottom_up.get(e.region, 0.0) + worth

    def post_bid(self, bid: Bid) -> None:
        self._bids.append(bid)

    def has_bid(self, bidder: str) -> bool:
        return any(b.bidder == bidder for b in self._bids)

    # -- the map itself ----------------------------------------------------
    def _bid_value(self, bid: Bid, now_ms: int) -> float:
        return bid.value + AGING_PER_S * max(0, now_ms - bid.born_ms) / 1000.0

    def priority_map(self, now_ms: int) -> Dict[Region, float]:
        value = dict(self._bottom_up)
        for bid in self._bids:
            if bid.region is not None:
                value[bid.region] = value.get(bid.region, 0.0) \
                    + self._bid_value(bid, now_ms)
        return value

    # -- one beat: one gaze ------------------------------------------------
    def beat(self, now_ms: int) -> Optional[Gaze]:
        self._expire_bids(now_ms)
        value = self.priority_map(now_ms)
        if not value:
            return None
        region = max(value, key=lambda r: value[r])
        meal = self._prepare(region)         # ONCE
        self.prepare_calls += 1
        served = tuple(d.name for d in self._registry.meal_eaters())
        satisfied = tuple(b for b in self._bids if b.region == region)
        self._bids = [b for b in self._bids if b.region != region]
        self._bottom_up.pop(region, None)    # the world must speak again
        return Gaze(region, now_ms, meal, served, satisfied)

    def _expire_bids(self, now_ms: int) -> List[Bid]:
        dead = [b for b in self._bids if now_ms - b.born_ms >= BID_TTL_MS]
        if dead:
            self._bids = [b for b in self._bids
                          if now_ms - b.born_ms < BID_TTL_MS]
        return dead
