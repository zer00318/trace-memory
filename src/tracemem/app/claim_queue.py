"""OWNS: the hand-off from capture threads to the one store writer (WO-093).

THE DEFECT THIS REPLACES. SCStream delivers frames on its own serial queue —
a different thread from the one that owns the daemon. sqlite3 connections
are thread-bound, so appending a claim directly from the capture callback
raises `ProgrammingError: SQLite objects created in a thread can only be
used in that same thread`. A live capture run hit exactly that: 3 errors,
0 claims durable.

tools/first_beat.py worked around it by constructing a ClaimLog INSIDE the
capture thread (first_beat.py:84). That unblocks a probe and is wrong for a
daemon: it yields one connection per capture thread, so there is no longer a
single writer, and every durability property the claim log declares — WAL
policy, one commit per claim, the hash chain's ordering — is stated about a
connection that is now one of several. The chain in particular cannot be
right if two threads are both appending: `link_n` is computed from the
current tail, and two writers racing on that read produce two claims
claiming the same predecessor.

So the hand-off is a queue instead. Capture threads only ever `submit()`,
which touches no sqlite at all; the owning thread `drain()`s on its
supervision beat and does every append itself. One writer, by construction.

THE COST, DECLARED RATHER THAN HIDDEN: a claim sitting in the queue when
the process dies is lost. That is a REAL durability window on top of the one
text/durability.py already declares, and it is bounded by the drain
interval, not by anything clever. The daemon ticks every second, so the
window is about a second of perception. Stated plainly because the store's
promise ("process death loses zero claims") is now only true of claims that
reached the store — and someone reading that promise deserves to know where
the boundary moved.

OVERFLOW IS WITNESSED, NEVER SILENT. A bounded queue that quietly drops is
the worst of both worlds: the eye believes it saw something and the memory
has no record and no hole either. When the queue is full the drop is
counted and an OVERWHELM witness is written — the vocabulary already has
that kind, for exactly this (backpressure shed: what, how much).
"""
from __future__ import annotations

import queue
import threading
from typing import Callable, List, Optional

from tracemem.app.witness import WitnessKind, witness
from tracemem.domain import Claim

# Bounded on purpose. An unbounded queue turns a slow store into unbounded
# memory growth, which on a laptop is a swap storm and then a kill — and the
# claims are lost anyway, having first taken the machine down with them.
# Sized for a few seconds of a fast capture burst at the daemon's 1s beat.
DEFAULT_MAXSIZE = 4096


class ClaimQueue:
    """Thread-safe submit, single-threaded drain.

    `submit` is callable from any thread and never touches the store.
    `drain` must only ever be called by the thread that owns the sink.
    """

    def __init__(self, sink: Callable[[Claim], None],
                 maxsize: int = DEFAULT_MAXSIZE,
                 now_ms: Optional[Callable[[], int]] = None) -> None:
        self._q: "queue.Queue[Claim]" = queue.Queue(maxsize=maxsize)
        self._sink = sink
        self._now = now_ms or (lambda: 0)
        self._lock = threading.Lock()
        self.submitted = 0
        self.drained = 0
        self.dropped = 0
        self._dropped_since_witness = 0
        self._owner: Optional[int] = None

    # -- any thread --------------------------------------------------------
    def submit(self, claim: Claim) -> bool:
        """Hand a claim over. Returns False if it was dropped.

        Never blocks: blocking here would stall the capture callback, and a
        stalled SCStream callback drops FRAMES, which is a worse loss than
        the claim we are trying to save.
        """
        try:
            self._q.put_nowait(claim)
        except queue.Full:
            with self._lock:
                self.dropped += 1
                self._dropped_since_witness += 1
            return False
        with self._lock:
            self.submitted += 1
        return True

    def pending(self) -> int:
        return self._q.qsize()

    # -- the owning thread only -------------------------------------------
    def drain(self, limit: Optional[int] = None) -> int:
        """Append everything waiting. Returns how many claims landed.

        Records the calling thread the first time and refuses a second one:
        the single-writer property is the whole point, and a second drainer
        would reintroduce the race the queue exists to remove.
        """
        me = threading.get_ident()
        if self._owner is None:
            self._owner = me
        elif self._owner != me:
            raise RuntimeError(
                "drain() called from thread %d but %d owns the sink — the "
                "single-writer property is what makes the hash chain and the "
                "durability policy true. Submit from other threads instead."
                % (me, self._owner))

        landed = 0
        while limit is None or landed < limit:
            try:
                claim = self._q.get_nowait()
            except queue.Empty:
                break
            self._sink(claim)
            landed += 1

        with self._lock:
            self.drained += landed
            shed, self._dropped_since_witness = self._dropped_since_witness, 0

        if shed:
            # Written by the owning thread, like every other append.
            self._sink(witness(
                WitnessKind.OVERWHELM, t_ms=self._now(),
                detail="claim queue full — shed %d claim(s) before they "
                       "reached the store" % shed,
                structured_extra={"dropped": shed, "queue_maxsize": self._q.maxsize}))
        return landed

    def stats(self) -> dict:
        return {"submitted": self.submitted, "drained": self.drained,
                "dropped": self.dropped, "pending": self.pending()}


def guarded(fn: Callable, on_error: Callable[[BaseException], None]) -> Callable:
    """Wrap a capture callback so an exception is RECORDED, not swallowed.

    The ObjC delegate that calls back into Python discards exceptions
    silently (tools/first_beat.py:88 found this the hard way). A capture
    thread that starts raising therefore looks exactly like a quiet
    afternoon: no error, no claims, no hole. This makes the failure visible
    to the thread that can act on it.
    """
    def _wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except BaseException as exc:            # noqa: BLE001 — deliberate
            on_error(exc)
            return None
    return _wrapped
