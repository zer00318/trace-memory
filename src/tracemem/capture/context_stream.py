"""OWNS: the context stream — slow-moving machine facts, as EDGE events.

Several senses in the map (Power state, Online/VPN state, Fullscreen flag,
Active Space, Idle seconds, Window z-order snapshot) all say the same thing
in their briefs: "lands events". They all face the same trap, so it is
solved once, here, instead of five times badly.

THE TRAP: these senses are polled — power every 30s, idle every 10s. A
naive sampler emits a reading per poll, so "on battery" becomes 2,880
identical events a day, and the one that matters (the moment it CHANGED)
is indistinguishable from the 2,879 that don't. Worse, the binder then
sees constant activity where the world was actually still.

THE RULE: a context channel emits only on CHANGE. Same value, no event.
Every event carries what it changed FROM, so a downstream reader can
reconstruct an interval without holding the whole history — which is what
makes "plugged in from 09:12 to 14:40" answerable from two events.

Level (what is true now) stays readable via `current()`. Edges are the
stream; levels are the state. Confusing the two is the bug this file
exists to prevent.

SPEC: 01 §7 (machine context senses)
ORACLE: tests/unit/test_context_stream.py — a repeated reading emits once.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ContextEvent:
    t_ms: int
    channel: str            # "power", "network", "presence", "focus", ...
    key: str                # the field within the channel, e.g. "on_battery"
    value: Any              # the new value
    previous: Any = None    # what it changed FROM (None = first-ever reading)
    first: bool = False     # True = the channel's opening reading, not a change

    @property
    def is_change(self) -> bool:
        """First readings are not changes: nothing transitioned, we just
        opened our eyes. The distinction matters to the binder, which must
        not read a boot as an event in the founder's life."""
        return not self.first


class ContextStream:
    """Edge-triggered fan-in for the polled machine senses.

    Not thread-safe by construction: every sense that feeds it is polled
    from the one sampler loop. If that ever stops being true, the fix is a
    queue at the sampler, not a lock in here — the class stays a pure state
    machine so it can be tested without a clock or a thread.
    """

    def __init__(self) -> None:
        self._level: Dict[str, Dict[str, Any]] = {}

    # -- levels: what is true right now ------------------------------------
    def current(self, channel: str) -> Dict[str, Any]:
        return dict(self._level.get(channel, {}))

    def value(self, channel: str, key: str, default: Any = None) -> Any:
        return self._level.get(channel, {}).get(key, default)

    # -- edges: what changed ------------------------------------------------
    def observe(self, channel: str, reading: Dict[str, Any],
                t_ms: int) -> List[ContextEvent]:
        """Feed one poll of one channel. Returns ONLY the fields that moved.

        A field absent from `reading` is left alone rather than treated as
        None — a sense that degrades (permission lost, CLI missing) reports
        fewer fields, and that must not fabricate a transition to null.
        """
        prev = self._level.setdefault(channel, {})
        events: List[ContextEvent] = []
        for key, value in reading.items():
            seen = key in prev
            if seen and prev[key] == value:
                continue                     # the still world emits nothing
            events.append(ContextEvent(
                t_ms=t_ms, channel=channel, key=key, value=value,
                previous=prev.get(key), first=not seen))
            prev[key] = value
        return events

    def forget(self, channel: str) -> None:
        """Drop a channel's level so its next reading opens fresh.

        Used when a sense goes dark (permission revoked, device unplugged):
        the next reading after the dark period is a FIRST, not a change —
        claiming a transition across a blind interval would be a lie about
        a moment nobody witnessed.
        """
        self._level.pop(channel, None)


def interval_from_edges(events: List[ContextEvent], key: str, value: Any,
                        now_ms: Optional[int] = None):
    """Reconstruct [start, end] spans where `key` held `value`.

    Returns a list of (t1_ms, t2_ms) with t2 None for a span still open at
    the end of the event list — an open span is honest; closing it at `now`
    without evidence would invent a transition. Pass `now_ms` only when the
    caller genuinely knows the span is still open now.
    """
    spans = []
    open_at = None
    for e in events:
        if e.key != key:
            continue
        if e.value == value and open_at is None:
            open_at = e.t_ms
        elif e.value != value and open_at is not None:
            spans.append((open_at, e.t_ms))
            open_at = None
    if open_at is not None:
        spans.append((open_at, now_ms))
    return spans
