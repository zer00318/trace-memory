"""OWNS: the loop that actually polls the machine senses (map: World —
the wiring half of Power state / Online-VPN state / Idle seconds / Focus
sense / Window z-order snapshot).

WHY THIS EXISTS: audit 2026-07-21 found every sense in capture/ to be an
orphan — well-built, well-tested, and called by nothing. Their briefs all
say "lands events", and nothing was landing anywhere. A sense with no
sampler is a library, not a sense, and the bar for such an atom is honestly
low no matter how good its tests are.

DESIGN: one thread, not five. Each channel declares its own interval and
the loop runs whichever are due — so the idle clock can be read every 10s
while power is read every 30s, without five threads contending for the same
window server and five separate failure modes to supervise.

Every channel is INDEPENDENTLY guarded: one sense raising must not take the
loop down, because the alternative is that a transient Quartz error at 3am
silently ends all sensing until someone notices. A failing channel is
recorded and retried on its next tick.

The loop OWNS NO POLICY. It polls, feeds the ContextStream, and hands
witness claims to a sink. What a claim means, when an absence starts, how
an edge is priced — all of that lives in the senses and in the organs
above. This file is plumbing, deliberately.

SPEC: 01 §6-7
ORACLE: tests/unit/test_sampler.py — a fake clock drives N ticks; each
        channel is polled at its own declared rate; a raising channel does
        not stop the others; witnesses reach the sink.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from tracemem.capture.capture_config import IDLE_POLL_INTERVAL_S
from tracemem.capture.context_stream import ContextEvent, ContextStream
from tracemem.capture.eyelids import Eyelids
from tracemem.capture.machine_sense import (
    NETWORK_POLL_INTERVAL_S, POWER_POLL_INTERVAL_S, read_idle_seconds,
    read_network, read_power,
)
from tracemem.capture.presence import PresenceSense
from tracemem.capture.window_stack import SNAPSHOT_INTERVAL_S


@dataclass
class Channel:
    name: str
    interval_s: float
    read: Callable[[], dict]        # returns the ContextStream reading
    next_due_s: float = 0.0
    failures: int = 0
    last_error: Optional[str] = None


@dataclass
class SamplerStats:
    ticks: int = 0
    polls: Dict[str, int] = field(default_factory=dict)
    events: int = 0
    witnesses: int = 0
    errors: Dict[str, int] = field(default_factory=dict)


class MachineSampler:
    """Polls the machine senses on one thread and lands their edges.

    Injectable everywhere (clock, channels, senses) so the scheduling logic
    is testable without waiting real seconds or owning a real Mac.
    """

    def __init__(self,
                 on_event: Optional[Callable[[ContextEvent], None]] = None,
                 on_witness: Optional[Callable[[object], None]] = None,
                 eyes_open: Optional[Callable[[], bool]] = None,
                 clock: Callable[[], float] = time.monotonic,
                 wall_clock: Callable[[], float] = time.time,
                 channels: Optional[List[Channel]] = None) -> None:
        self.stream = ContextStream()
        self.presence = PresenceSense()
        # RE-AUDIT FIX 2026-07-21: this used to default to `lambda: True` and
        # nothing ever constructed an Eyelids, so the sleep-lie fix one layer
        # down was UNREACHABLE — the sampler kept reporting the eyes open
        # through every sleep and the false "screen lit, nobody home for
        # 29100s" claim came straight back. A correct fix behind a default
        # that bypasses it is not a fix. The sampler now OWNS an Eyelids and
        # reads its real state; the daemon feeds it via eyelid_event() (or
        # capture.eyelids.start_taps(sampler.eyelids) once a run loop exists).
        self.eyelids = Eyelids()
        self._on_event = on_event
        self._on_witness = on_witness
        self._eyes_open = eyes_open or (lambda: self.eyelids.eyes_open)
        self._clock = clock
        self._wall = wall_clock
        self._awake_since_ms: Optional[int] = None
        self.stats = SamplerStats()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.channels = channels if channels is not None else self._default_channels()

    def _default_channels(self) -> List[Channel]:
        return [
            Channel("power", POWER_POLL_INTERVAL_S,
                    lambda: read_power().as_reading()),
            Channel("network", NETWORK_POLL_INTERVAL_S,
                    lambda: read_network().as_reading()),
            Channel("presence", IDLE_POLL_INTERVAL_S, self._read_presence),
        ]

    # -- the presence channel is special: it also emits witnesses ---------
    def _read_presence(self) -> dict:
        idle = read_idle_seconds()
        t_ms = int(self._wall() * 1000)
        open_eyes = self._eyes_open()
        if open_eyes and self._awake_since_ms is None:
            self._awake_since_ms = t_ms
        elif not open_eyes:
            self._awake_since_ms = None

        _edge, claim = self.presence.observe_and_witness(
            idle, t_ms, eyes_open=open_eyes,
            awake_since_ms=self._awake_since_ms)
        if claim is not None:
            self.stats.witnesses += 1
            if self._on_witness is not None:
                self._on_witness(claim)
        return {"state": self.presence.state}

    # -- the eyelid door: lock/sleep events enter the sampler here --------
    def eyelid_event(self, cause: str, t_ms: int):
        """Feed one OS lock/unlock/sleep/wake event. Returns its witness.

        The daemon points capture.eyelids.start_taps() at this (or at
        self.eyelids directly). Routing it through the sampler is what keeps
        presence and the closed-eyes witness reading ONE eyelid state rather
        than two that can disagree.
        """
        _edge, claim = self.eyelids.observe(cause, t_ms)
        if claim is not None:
            self.stats.witnesses += 1
            if self._on_witness is not None:
                self._on_witness(claim)
        return claim

    # -- one pass over whatever is due ------------------------------------
    def tick(self) -> List[ContextEvent]:
        now_s = self._clock()
        t_ms = int(self._wall() * 1000)
        self.stats.ticks += 1
        landed: List[ContextEvent] = []

        for ch in self.channels:
            if now_s < ch.next_due_s:
                continue
            ch.next_due_s = now_s + ch.interval_s
            try:
                reading = ch.read()
            except Exception as exc:
                # One sense dying must never end all sensing. Record and
                # retry on the next tick; the channel's level is forgotten
                # so the reading after the outage opens FRESH rather than
                # claiming a transition nobody witnessed.
                ch.failures += 1
                ch.last_error = repr(exc)
                self.stats.errors[ch.name] = self.stats.errors.get(ch.name, 0) + 1
                self.stream.forget(ch.name)
                continue
            self.stats.polls[ch.name] = self.stats.polls.get(ch.name, 0) + 1
            events = self.stream.observe(ch.name, reading, t_ms)
            landed.extend(events)

        self.stats.events += len(landed)
        if self._on_event is not None:
            for e in landed:
                self._on_event(e)
        return landed

    # -- lifecycle ---------------------------------------------------------
    def start(self, tick_interval_s: float = 1.0) -> None:
        if self._thread is not None:
            return
        self._stop.clear()

        def _loop():
            while not self._stop.is_set():
                try:
                    self.tick()
                except Exception:
                    pass            # tick() already guards per channel
                self._stop.wait(tick_interval_s)

        self._thread = threading.Thread(target=_loop, daemon=True,
                                        name="trace.sampler")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None
