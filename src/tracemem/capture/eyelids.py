"""OWNS: when the eyes are shut (map: World / Screen stream / Lock &
screensaver, Sleep / wake; atoms: Lock event tap, Sleep/wake taps,
Closed-eyes witness, Wake re-arm).

The product truth at stake is #4, never lies. A gap in the store has two
possible meanings — "nothing happened" and "I could not see" — and they are
opposite answers to the same question. Without these witnesses the supplier
cannot tell them apart, so the Negative question class ("did anything
happen while I was at lunch?") has no honest answer available. That is why
a boring subscription to two OS notifications is load-bearing.

THE NESTING PROBLEM, which is the real design content here: lock and sleep
are not exclusive. A laptop lid closed at 18:00 typically fires
screenIsLocked THEN willSleep, and on opening fires didWake THEN
screenIsUnlocked. Treating each pair independently emits two overlapping
closed-eyes claims for one absence — double-counting every night, forever,
in a way that looks entirely reasonable in the log.

The fix is a DEPTH COUNTER, not a boolean: eyes close on the first closing
cause and reopen only when the last one clears, producing exactly one claim
spanning the true blind interval, with every contributing cause named in
its structured payload.

WAKE RE-ARM: the brief says didWake triggers the same re-arm path as stream
death — one re-arm, many causes. This module therefore does not own
restarting capture; it emits the open-eyes edge and calls the injected
`on_reopen` hook.

    NOT DONE, and previously overstated here (audit 2026-07-21): there is
    no re-arm path in this repo for that hook to point AT. The prerequisite
    atom "Stream death & re-arm" is at 0/NOT STARTED, and grepping the tree
    for a re-arm finds only this paragraph. What exists is the injection
    seam and nothing behind it. The Wake re-arm atom is not startable until
    stream re-arm exists; saying otherwise made a parameter look like a
    feature.

STATUS: the pairing state machine below is pure and fully tested. The live
subscription (`start_taps`) needs a running CFRunLoop and a physical
lock/lid event to witness, which no unit test can stage — it is written,
and it is NOT claimed as witnessed. The atom's live proof stays open.

SPEC: 01 §4
ORACLE: tests/unit/test_eyelids.py — lock@t1 + unlock@t2 → one claim
        covering [t1, t2]; a nested sleep does not double-count.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional

from tracemem.app.witness import WitnessKind, witness

# The causes that shut the eyes, and the events that clear them.
CLOSERS = {"locked": WitnessKind.LOCKED, "sleep": WitnessKind.SLEEP}
OPENERS = {"unlocked": "locked", "wake": "sleep"}


@dataclass(frozen=True)
class EyelidEdge:
    t_ms: int
    cause: str                  # "locked" / "unlocked" / "sleep" / "wake"
    eyes_open: bool             # the state AFTER this edge
    depth: int                  # how many closing causes are still active


class Eyelids:
    """Pairs closing/opening causes into one closed-eyes interval.

    Pure state machine — no notifications, no clock — so the nesting
    arithmetic is testable to the millisecond.
    """

    def __init__(self, on_reopen: Optional[Callable[[int], None]] = None) -> None:
        self._active: List[str] = []        # closing causes currently in force
        self._closed_since_ms: Optional[int] = None
        self._causes: List[str] = []        # everything that contributed
        self._on_reopen = on_reopen
        self.edges: List[EyelidEdge] = []

    @property
    def eyes_open(self) -> bool:
        return not self._active

    def observe(self, cause: str, t_ms: int):
        """Feed one OS event. Returns (edge, claim-or-None).

        The claim appears only on the edge that fully reopens the eyes.
        """
        claim = None
        if cause in CLOSERS:
            if cause not in self._active:       # duplicate notifications happen
                self._active.append(cause)
                if cause not in self._causes:
                    self._causes.append(cause)
            if self._closed_since_ms is None:
                self._closed_since_ms = t_ms
        elif cause in OPENERS:
            closer = OPENERS[cause]
            if closer in self._active:
                self._active.remove(closer)
            if not self._active and self._closed_since_ms is not None:
                claim = self._close(t_ms)
        else:
            raise ValueError(f"unknown eyelid cause: {cause!r}")

        edge = EyelidEdge(t_ms=t_ms, cause=cause, eyes_open=self.eyes_open,
                          depth=len(self._active))
        self.edges.append(edge)
        if claim is not None and self._on_reopen is not None:
            self._on_reopen(t_ms)               # shared re-arm path, one caller
        return edge, claim

    def _close(self, t_ms: int):
        t1, self._closed_since_ms = self._closed_since_ms, None
        causes, self._causes = list(self._causes), []
        if t_ms <= t1:
            # BUG FIXED (audit 2026-07-21): this used to return None, which
            # made a REAL blind interval vanish with no witness at all — the
            # store would show an unexplained hole and claim nothing about
            # it. A clock that moved backwards during sleep (NTP correcting
            # on wake is a common cause, and a lid close is exactly where it
            # happens) is itself the news. Witness the jump instead of
            # swallowing the interval; CLOCK_JUMP exists in the vocabulary
            # for precisely this.
            return witness(
                WitnessKind.CLOCK_JUMP, t_ms=t1, span_t2_ms=t1,
                detail=f"eyes reopened at {t_ms} but closed at {t1} — clock "
                       f"moved backwards {t1 - t_ms}ms while shut "
                       f"({'+'.join(causes)}); blind interval length unknown",
                structured_extra={"causes": causes, "reopened_t_ms": t_ms,
                                  "backwards_ms": t1 - t_ms})
        return witness(
            WitnessKind.CLOSED_EYES, t_ms=t1, span_t2_ms=t_ms,
            detail=f"could not see for {(t_ms - t1) / 1000:.0f}s "
                   f"({'+'.join(causes)})",
            structured_extra={"causes": causes})

    def force_open(self, t_ms: int):
        """Boot found the eyes closed with no opener (the Mac was killed
        mid-sleep). Close the interval honestly rather than leaving it to
        run forever."""
        if self._closed_since_ms is None:
            return None
        self._active = []
        return self._close(t_ms)


# -- the live taps: written, NOT witnessed (see module docstring) ----------
def start_taps(eyelids: Eyelids, clock: Optional[Callable[[], float]] = None):
    """Subscribe to the OS's lock/unlock and sleep/wake notifications.

    Lock/unlock live on the DISTRIBUTED centre (they are system-wide);
    sleep/wake live on NSWorkspace's own centre. Getting that pairing wrong
    is the classic reason a lock tap silently never fires. Requires a
    running CFRunLoop — the daemon's, not one started here.

    Returns the observer objects; the caller must keep them referenced or
    ObjC will release them and the taps die silently.
    """
    import time
    from AppKit import NSWorkspace
    from Foundation import NSDistributedNotificationCenter

    now = clock or time.time

    def _handler(cause):
        def _fire(_note):
            eyelids.observe(cause, int(now() * 1000))
        return _fire

    observers = []
    dnc = NSDistributedNotificationCenter.defaultCenter()
    for name, cause in (("com.apple.screenIsLocked", "locked"),
                        ("com.apple.screenIsUnlocked", "unlocked")):
        observers.append(dnc.addObserverForName_object_queue_usingBlock_(
            name, None, None, _handler(cause)))

    wnc = NSWorkspace.sharedWorkspace().notificationCenter()
    for name, cause in (("NSWorkspaceWillSleepNotification", "sleep"),
                        ("NSWorkspaceDidWakeNotification", "wake")):
        observers.append(wnc.addObserverForName_object_queue_usingBlock_(
            name, None, None, _handler(cause)))
    return observers
