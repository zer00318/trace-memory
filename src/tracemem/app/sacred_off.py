"""OWNS: the sacred OFF (map: Running / The sacred OFF).

The one control in this product that never negotiates. Pause stops capture
AT THE SOURCE — the stream is stopped, the mic tap is closed — and the hole
is witnessed. Resume writes the twin witness.

THREE PROPERTIES, and each is a refusal:

1. NO CONFIRMATION DIALOG, EVER. `pause()` takes no confirmation argument
   and cannot be talked out of it. "Are you sure?" on an off switch means
   the off switch belongs to the software, not to you. There is deliberately
   no code path here that can ask a question.

2. NO CONDITIONS. Pause cannot fail, cannot be refused, cannot be deferred
   because a night job is mid-flight or an organ is busy. A pause that
   sometimes does not pause is worse than none: you would stop trusting it
   and never know which times it worked.

3. AT THE SOURCE, NOT DOWNSTREAM. Filtering frames after capture would mean
   pixels of a paused moment existed in this process, however briefly. The
   promise is that they were never taken. So the stop callbacks run
   synchronously inside pause(), before it returns, and the gate below is a
   SECOND line of defence rather than the mechanism.

WHY THE GATE EXISTS AT ALL, given the source is stopped: a stream stop is
asynchronous at the OS level, and a sample buffer already in flight can
arrive after the stop call returns. The gate catches exactly that frame.
`frames_refused` counts them, because "zero frames entered the retina" is
the oracle and a count is how you prove it rather than assert it.
"""
from __future__ import annotations

from typing import Callable, List, Optional

from tracemem.app.witness import WitnessKind, witness
from tracemem.domain import Claim


class SacredOff:
    """The pause switch. Construct with the callables that stop and start
    capture at its source."""

    def __init__(self, stoppers: Optional[List[Callable[[], None]]] = None,
                 starters: Optional[List[Callable[[], None]]] = None) -> None:
        self._stoppers = list(stoppers or [])
        self._starters = list(starters or [])
        self.paused = False
        self.frames_refused = 0
        self.paused_at_ms: Optional[int] = None

    def register(self, stop: Callable[[], None],
                 start: Callable[[], None]) -> None:
        """An organ declares how it is silenced and revived."""
        self._stoppers.append(stop)
        self._starters.append(start)

    # -- the switch --------------------------------------------------------
    def pause(self, now_ms: int) -> Claim:
        """Stop capture at the source. Returns the founder-pause witness.

        Takes no confirmation and returns no failure. Calling it while
        already paused is not an error and not a second witness — it is the
        same state, and a duplicate witness would put a hole in the record
        that never existed.
        """
        if self.paused:
            return self._witness(WitnessKind.FOUNDER_PAUSE, now_ms,
                                 "already paused", redundant=True)

        self.paused = True
        self.paused_at_ms = now_ms
        # Stop FIRST, witness second: if writing the witness somehow fails,
        # capture is already stopped. The reverse order would leave a
        # recorded pause that did not happen, which is the more dangerous lie.
        self._silence()
        return self._witness(WitnessKind.FOUNDER_PAUSE, now_ms,
                             "capture stopped at the source by the founder")

    def resume(self, now_ms: int) -> Claim:
        """Start capture again and witness the twin, closing the interval."""
        if not self.paused:
            return self._witness(WitnessKind.FOUNDER_RESUME, now_ms,
                                 "was not paused", redundant=True)

        # Explicit None check: t_ms == 0 is a real timestamp and falsy, so
        # `self.paused_at_ms or now_ms` would report a zero-length pause for
        # any run whose clock starts at zero.
        started = now_ms if self.paused_at_ms is None else self.paused_at_ms
        span = now_ms - started
        self.paused = False
        self.paused_at_ms = None
        for start in self._starters:
            start()
        return self._witness(
            WitnessKind.FOUNDER_RESUME, now_ms,
            "capture resumed after %.1f min of founder pause"
            % (span / 60_000.0),
            extra={"paused_ms": span})

    def _silence(self) -> None:
        """Every stopper runs, even if one raises.

        A stopper that throws must not leave the remaining organs recording:
        the whole point of the switch is that after it returns, nothing is
        capturing. The exception is re-raised after the others have run, so
        the failure is still loud.
        """
        first_error = None
        for stop in self._stoppers:
            try:
                stop()
            except Exception as exc:            # noqa: BLE001 — deliberate
                first_error = first_error or exc
        if first_error is not None:
            raise first_error

    # -- the second line of defence ---------------------------------------
    def accepts_frames(self) -> bool:
        return not self.paused

    def admit(self, frame) -> bool:
        """The gate the capture path calls before handing a frame onward.

        Returns False and counts the refusal while paused. Exists for the
        in-flight sample buffer that arrives after an asynchronous stream
        stop — not as the mechanism of pausing, which is the stop itself.
        """
        if self.paused:
            self.frames_refused += 1
            return False
        return True

    def _witness(self, kind: WitnessKind, now_ms: int, detail: str,
                 redundant: bool = False, extra: Optional[dict] = None) -> Claim:
        structured = {"redundant": redundant}
        structured.update(extra or {})
        return witness(kind, t_ms=now_ms, detail=detail,
                       structured_extra=structured)
