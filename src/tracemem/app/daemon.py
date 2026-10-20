"""OWNS: the Watcher's skeleton — supervisor, lock, boot, clean stop.

No business logic lives here: organs are duck-typed (name, start(),
stop(), alive()) and the supervisor only starts, watches, restarts,
benches, and witnesses. Every state change emits through ONE sink
(claims-bound), using the witness vocabulary — no ad-hoc strings.

Testable by construction: clock and sink injected; nothing here touches
SCStream, threads, or the store directly.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from tracemem.app.gap_detector import detect_gap
from tracemem.app.witness import WitnessKind, witness

RESTART_BENCH_THRESHOLD = 3        # deaths within the window → benched
RESTART_WINDOW_MS = 60_000

# A FAILED START IS NOT A DEATH, and conflating them is how capture stops
# forever (caught 2026-07-29 before it ever bit, by asking what happens when
# the founder unlocks his Mac).
#
# A death means the organ RAN and then broke — evidence of sickness, and the
# bench exists so a sick organ cannot restart-loop. A failed start means the
# WORLD was not ready: the display is asleep, or waking, or a permission has
# not been granted yet. Nothing is sick, and the condition will clear on its
# own.
#
# With both counted the same, the arithmetic was fatal: the loop ticks once a
# second, the bench threshold is 3 within 60s, and NOTHING EVER UN-BENCHES an
# organ. So a display that took three seconds to become available to
# ScreenCaptureKit after an unlock would have stopped the eye permanently —
# silently, with the daemon still up and answering, and only a witness buried
# in the store to say why. Exactly the failure shape this product ranks worst:
# invisible, permanent, and it looks like everything is fine.
#
# So start failures back off instead of benching. An impossible condition
# costs one retry a minute forever — cheap, visible, and it recovers the
# instant the world changes.
START_BACKOFF_MS = (1_000, 2_000, 4_000, 8_000, 16_000, 32_000, 60_000)


@dataclass
class _OrganState:
    organ: object
    restarts_in_window: List[int] = field(default_factory=list)
    benched: bool = False
    # PARKED — deliberately stopped, and therefore NOT dead (2026-07-29,
    # F-EYELID). Without this flag the two states are indistinguishable to
    # `tick()`: the eyelid stops the eye so the brain can think, the
    # supervisor sees a not-alive organ one beat later, revives it, and the
    # eye is open again before the night gets a single slice. It would have
    # looked like the eyelid simply did not work, and the restart witnesses
    # would have accumulated as evidence of an organ that keeps dying.
    parked: bool = False
    # Consecutive failed STARTS (not deaths — see START_BACKOFF_MS), and when
    # the next attempt is due. Both reset the moment a start succeeds.
    start_failures: int = 0
    next_start_ms: int = 0


class SingleInstanceLock:
    """flock on a well-known path; a second daemon exits loudly.
    Stale locks (dead pid) are reclaimed by flock semantics itself."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._fh = None

    def acquire(self) -> bool:
        import fcntl
        self._fh = open(self._path, "w")
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._fh.close()
            self._fh = None
            return False
        self._fh.write(str(os.getpid()))
        self._fh.flush()
        return True

    def release(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None


class Supervisor:
    def __init__(self, sink: Callable, now_ms: Callable[[], int],
                 device_id: str = "mac-1") -> None:
        self._sink = sink                  # witness claims land here (store-bound)
        self._now = now_ms
        self._device = device_id
        self._organs: Dict[str, _OrganState] = {}
        self._running = False

    # -- lifecycle ---------------------------------------------------------
    def register(self, organ) -> None:
        if self._running:
            raise RuntimeError("register before boot — organs don't appear mid-flight")
        self._organs[organ.name] = _OrganState(organ)

    def boot(self, last_claim_t_ms: Optional[int],
             last_witness_kind: Optional[WitnessKind]) -> None:
        """First act: the hole is written down before anything else."""
        gap = detect_gap(last_claim_t_ms, last_witness_kind, self._now(),
                         device_id=self._device)
        if gap is not None:
            self._sink(gap)
        self._sink(witness(WitnessKind.START, self._now(), device_id=self._device))
        # AN ORGAN THAT CANNOT START MUST NOT TAKE THE DAEMON WITH IT.
        #
        # WITNESSED 2026-07-29, and it took the founder's capture down: the
        # daemon was restarted while his screen was asleep, `SCStreamSession
        # .start()` raised "ScreenCaptureKit reports zero displays", the
        # exception propagated out of boot(), and the whole process died —
        # taking the ask socket, the brain and the night with it. The store
        # was fine; nothing was watching or answering, and nothing said so.
        #
        # This is the supervisor's own contract, unimplemented at exactly the
        # moment it mattered: `tick()` has always caught a dying organ,
        # witnessed it, retried it and benched it rather than dying. `boot()`
        # simply called start() bare. So a transient condition at the wrong
        # second — a sleeping display, a permission not yet granted — was
        # fatal, while the identical condition one beat later was routine.
        #
        # A failed start is now a state this class already knows how to
        # handle: the next tick retries it down the ordinary witnessed path,
        # under the backoff in START_BACKOFF_MS. It deliberately does NOT
        # bench — see that constant for why treating a sleeping display as a
        # sick organ would have stopped capture permanently.
        for st in self._organs.values():
            self._try_start(st, self._now())
        self._running = True


    def _try_start(self, st, now: int) -> bool:
        """Start one organ. Returns True if it lives. NEVER raises.

        ONE PATH FOR BOOT AND TICK, deliberately. Every bug in this area has
        been the two drifting apart: boot() called start() bare while tick()
        guarded it, so a sleeping display was fatal at second 0 and routine at
        second 1. Then the guard was added to boot() alone and tick() still
        crashed. A shared method cannot drift.
        """
        try:
            st.organ.start()
        except Exception as e:                           # noqa: BLE001
            step = min(st.start_failures, len(START_BACKOFF_MS) - 1)
            st.next_start_ms = now + START_BACKOFF_MS[step]
            st.start_failures += 1
            # Record the transition into failure and the transition onto the
            # terminal one-minute retry tier — not every retry on that tier.
            # ``step == last`` remains true forever and used to append one
            # identical witness per minute for as long as Screen Recording
            # permission was absent.  A stable known hole needs a heartbeat
            # in health(), not hundreds of duplicate immutable claims.
            if st.start_failures in (1, len(START_BACKOFF_MS)):
                self._sink(witness(
                    WitnessKind.ORGAN_START_FAILED, now,
                    detail="%s failed to start: %s" % (st.organ.name, e),
                    device_id=self._device,
                    structured_extra={"organ": st.organ.name,
                                      "attempt": st.start_failures,
                                      "retry_in_ms": START_BACKOFF_MS[step],
                                      "failed_to_start": repr(e)[:200]}))
            return False
        st.start_failures = 0
        st.next_start_ms = 0
        return True

    def tick(self) -> None:
        """One supervision beat: dead organs restart (witnessed) or get
        benched after repeated deaths (witnessed, never fatal to the whole)."""
        if not self._running:
            raise RuntimeError("tick before boot")
        now = self._now()
        for st in self._organs.values():
            if st.parked or st.benched or st.organ.alive():
                continue
            if now < st.next_start_ms:
                continue                       # backing off; the world is busy
            st.restarts_in_window = [t for t in st.restarts_in_window
                                     if now - t <= RESTART_WINDOW_MS]
            if len(st.restarts_in_window) >= RESTART_BENCH_THRESHOLD:
                st.benched = True
                self._sink(witness(WitnessKind.ORGAN_BENCHED, now,
                                   detail=st.organ.name, device_id=self._device,
                                   structured_extra={"organ": st.organ.name}))
                continue
            # SAME GUARD AS boot(), and a test caught that it was missing here
            # too: a revival that raises would kill the daemon on the NEXT beat
            # instead of the first — the identical failure deferred by a
            # second, and harder to attribute when it happened.
            # A failed start is NOT a death, so it does not touch
            # restarts_in_window and cannot bench. See START_BACKOFF_MS for
            # what conflating the two would have cost the founder the first
            # time he unlocked his Mac.
            if not self._try_start(st, now):
                continue
            # It lives. A real death after this is a real death again.
            st.restarts_in_window.append(now)
            st.start_failures = 0
            st.next_start_ms = 0
            self._sink(witness(WitnessKind.ORGAN_RESTART, now,
                               detail=st.organ.name, device_id=self._device,
                               structured_extra={"organ": st.organ.name}))

    def stop(self) -> None:
        """The clean goodbye — the gap detector trusts this witness."""
        for st in self._organs.values():
            if st.organ.alive():
                st.organ.stop()
        self._sink(witness(WitnessKind.CLEAN_STOP, self._now(),
                           device_id=self._device))
        self._running = False

    # -- parking: closing an eye on purpose --------------------------------
    #
    # THE RE-ARM COMES FREE, and that is the point of doing it this way. The
    # supervisor already revives any organ that is not alive, with a witness,
    # under a bench threshold — that machinery has been running and tested
    # since WO-091. `unpark` does not restart anything itself; it simply
    # stops lying about the organ being parked, and the NEXT tick sees a dead
    # organ and revives it down the one path that already works.
    #
    # `eyelids.py` records that its `on_reopen` hook had nothing behind it
    # and that the "Stream death & re-arm" atom was at zero. It is not at
    # zero any more, and it did not need a second re-arm implementation —
    # only an honest way to say "this one is off on purpose".

    def park(self, name: str) -> bool:
        """Stop an organ deliberately. Returns True if this changed anything."""
        st = self._organs.get(name)
        if st is None or st.parked:
            return False
        st.parked = True
        if st.organ.alive():
            st.organ.stop()
        return True

    def unpark(self, name: str) -> bool:
        """Allow an organ to live again. The next tick() revives it through
        the ordinary restart path, witnessed like any other revival."""
        st = self._organs.get(name)
        if st is None or not st.parked:
            return False
        st.parked = False
        # A park/unpark cycle is not evidence of an unhealthy organ, so it
        # must not count toward the bench threshold — otherwise five nights
        # of thinking would bench the eye and capture would stop for good.
        st.restarts_in_window = []
        return True

    def parked(self, name: str) -> bool:
        st = self._organs.get(name)
        return bool(st is not None and st.parked)

    # -- visibility (menubar/status reads this) ----------------------------
    def health(self) -> Dict[str, str]:
        out = {}
        for name, st in self._organs.items():
            # "parked" is deliberately NOT "alive", which is what lets the
            # night's contention check (`__main__` signals set_capture from
            # `any(state == "alive")`) free the GPU with no change of its own.
            out[name] = ("parked" if st.parked
                         else "benched" if st.benched
                         else "alive" if st.organ.alive() else "dead")
        return out
