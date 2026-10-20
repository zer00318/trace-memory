"""OWNS: when the eye closes so the brain can think (F-EYELID, 2026-07-29).

WHY THIS EXISTS. `run_night()` had never executed once on the founder's own
machine, and the daemon said so itself:

    night: {slices: 0,
            skipped_because: "capture is live or a turn is held —
                              the night never shares the GPU (O3)"}

One GPU means capture and thinking cannot both hold it. That guard is
correct and must not be weakened. But `./trace start` is `--eye --ask`, and
NOTHING in the product ever closed the eye — so the entire node half of the
napkin, the differentiator, ran zero times in production while looking, from
the outside, like a feature that was merely "unproven".

THE RULING (F-EYELID, founder, 2026-07-29). The eye closes when the screen is
LOCKED or ASLEEP, **or** after 15+ minutes with no keyboard or mouse input.
He was shown the cost and took it: a playing video or a long read is real
screen life, and under the idle rule the eye stops watching it to go think.
Locked-or-asleep alone would have been unambiguous but would only fire on
days he actually locks up — and on the others the brain would never think.

WHAT THIS MODULE IS NOT. It is not a second state machine. `capture/eyelids.py`
already pairs nested lock/sleep causes into ONE closed interval (a boolean
double-counts every night, forever, in a way that looks entirely reasonable
in the log), and `capture/presence.py` already backdates a departure by the
idle reading and floors that backdate at wake time (the idle clock keeps
counting through sleep, so a naive read asserts "screen lit, nobody home for
28810s" about a night the Mac was off). Both are pure, tested, and were
sitting unwired. This module is the WIRING: it polls the two sensors, drives
the supervisor's park/unpark, and makes sure the blind interval is witnessed.

THE WITNESS IS NOT OPTIONAL. The founder's real store contains ZERO
CLOSED_EYES and ZERO AWAY witnesses, which is precisely why coverage would
report ~100% watched for days the machine was asleep. A gap in the store has
two possible meanings — "nothing happened" and "I could not see" — and they
are opposite answers to the same question. An eyelid that closes without
writing the interval down converts an honest hole into a silent lie, which is
the top of the failure hierarchy. So the witness ships with the eyelid, or
the eyelid does not ship.

DEGRADATION, stated because silence is the enemy. An unknown idle reading or
an unreadable lock state is a HOLE, never "nobody is here": the eye stays
open and the brain does not think. Refusing to think is a cost we can see in
the digest; closing the eye on a failed sensor read is a blind spot we
cannot.
"""
from __future__ import annotations

from typing import Callable, Optional

from tracemem.app.witness import WitnessKind, witness

# F-EYELID: fifteen minutes, chosen BY THE FOUNDER on 2026-07-29 with the
# trade-off named. This is not a tuning knob and not an assumed constant —
# it is a recorded product decision, and moving it needs him, not a probe.
#
# Deliberately NOT `capture_config.IDLE_AWAY_THRESHOLD_S` (300s), which is a
# different question with a different owner: that one asks "has the human
# left?" for the AWAY witness and coverage math. This one asks "may the eye
# close?", and closing the eye is a much heavier act than noting an absence.
# One constant serving two questions is how a later session silently changes
# an answer the founder gave.
EYELID_IDLE_S = 15 * 60.0

# How often the sensors are read. The reads are cheap (one Quartz call each);
# the rate is set by how long the eye should stay shut needlessly after the
# founder comes back, not by cost.
POLL_INTERVAL_MS = 5_000

_EYE = "eye"


class Eyelid:
    """Polls the world and parks or revives the eye. One decision per beat."""

    def __init__(self, supervisor, sink: Callable, now_ms: Callable[[], int],
                 organ_name: str = _EYE, device_id: str = "mac-1",
                 idle_threshold_s: float = EYELID_IDLE_S,
                 read_idle=None, read_locked=None, read_console=None,
                 log: Optional[Callable[[str], None]] = None) -> None:
        self._sup = supervisor
        self._sink = sink
        self._now = now_ms
        self._organ = organ_name
        self._device = device_id
        # A NUMBER, OR SOMETHING THAT KNOWS THE CURRENT NUMBER.
        #
        # `eyelid_idle_minutes` was a setting nobody read. It was written to
        # the file, printed on the settings screen, and the eyelid used the
        # hard-coded 15 minutes regardless — so a person could set it to 60,
        # see 60 on the screen, and have their eye close at 15 for ever. The
        # screen was describing a machine that did not exist.
        #
        # Accepting a callable keeps the eyelid testable with a plain float
        # and lets production hand it a live settings source, so a change
        # takes effect on the next beat rather than the next restart.
        self._threshold_src = idle_threshold_s
        self._log = log or (lambda m: None)
        if read_console is None:
            from tracemem.capture.machine_sense import read_on_console
            read_console = read_on_console
        self._read_console = read_console
        if read_idle is None or read_locked is None:
            from tracemem.capture.machine_sense import (read_idle_seconds,
                                                        read_screen_locked)
            read_idle = read_idle or read_idle_seconds
            read_locked = read_locked or read_screen_locked
        self._read_idle = read_idle
        self._read_locked = read_locked

        self._closed_since_ms: Optional[int] = None
        self._causes: list = []
        self._last_poll_ms: Optional[int] = None
        self.closes = 0
        self.reopens = 0
        self.last_reason: Optional[str] = None

    # -- the decision ------------------------------------------------------

    @property
    def _threshold_s(self) -> float:
        """The threshold as it stands now — see __init__."""
        src = self._threshold_src
        if callable(src):
            try:
                return float(src())
            except Exception:                                # noqa: BLE001
                # A settings read that stumbles must not change when the eye
                # closes. Fall back to the shipped default rather than to
                # zero, which would shut the eye instantly and for ever.
                return EYELID_IDLE_S
        return float(src)

    def why_close(self) -> Optional[str]:
        """The reason the eye may close right now, or None to stay open.

        A sentence rather than a bool, for the same reason the night's
        `why_not()` is one: "it just didn't" costs an afternoon.
        """
        # SOMEBODY ELSE AT THE KEYBOARD OUTRANKS EVERY OTHER REASON.
        #
        # Fast user switching leaves this session running with its capture
        # stream alive while a DIFFERENT PERSON uses the Mac. Checked FIRST,
        # and before the lock, because a switched-away session can report
        # unlocked — and because if two reasons apply this is the one worth
        # recording: every other wall here is the founder declining to record
        # his own life, and this is the only one about someone else's.
        #
        # `read_on_console` returns None when it cannot tell, and None does
        # NOT mean yes. An unreadable answer closes the eye, because the cost
        # of being wrong is recording a stranger.
        on_console = self._read_console()
        if on_console is False:
            return "other-user"
        locked = self._read_locked()
        if locked is True:
            return "locked"
        idle = self._read_idle()
        if idle is None:
            # A FAILED READ IS NOT AN ABSENCE. See the module docstring.
            return None
        if idle >= self._threshold_s:
            return "idle"
        return None

    # -- the beat ----------------------------------------------------------

    def tick(self) -> Optional[str]:
        """One opportunity to open or close. Returns the transition, if any."""
        now = self._now()
        if (self._last_poll_ms is not None
                and now - self._last_poll_ms < POLL_INTERVAL_MS):
            return None
        self._last_poll_ms = now

        try:
            reason = self.why_close()
        except Exception as e:                            # noqa: BLE001
            # A sensor that raises must not take the daemon with it, and must
            # not close the eye either — an unreadable world is a hole.
            self.last_reason = "sensor failed: %s" % type(e).__name__
            self._log("eyelid sensor failed: %s: %s" % (type(e).__name__, e))
            return None
        self.last_reason = reason

        if reason is not None and self._closed_since_ms is None:
            return self._close(now, reason)
        if reason is None and self._closed_since_ms is not None:
            return self._open(now)
        if reason is not None and reason not in self._causes:
            # Deepened while already shut (idle, then locked). Recorded as a
            # contributing cause of ONE interval rather than starting a
            # second — the nesting lesson from capture/eyelids.py.
            self._causes.append(reason)
        return None

    def _close(self, now: int, reason: str) -> str:
        if not self._sup.park(self._organ):
            return ""
        self._closed_since_ms = now
        self._causes = [reason]
        self.closes += 1
        self._log("eyelid: eye closed (%s) — the brain may think" % reason)
        return "closed"

    def _open(self, now: int) -> str:
        t1, self._closed_since_ms = self._closed_since_ms, None
        causes, self._causes = list(self._causes), []
        self._sup.unpark(self._organ)
        self.reopens += 1
        # THE INTERVAL, WRITTEN DOWN. AWAY when only the idle clock closed
        # the eye (the screen was lit and nobody was home) and CLOSED_EYES
        # when the machine itself was shut — they are different facts and a
        # distinction that lives only in free text is one no query can make.
        # ANOTHER PERSON'S TIME IS ITS OWN FACT, not a variety of "closed
        # eyes". Someone reading their own gaps must be able to tell an
        # afternoon they were away from an afternoon somebody else was using
        # their Mac — and a distinction that lives only in free text is one
        # no query can make.
        if "other-user" in causes:
            kind = WitnessKind.OTHER_USER
        else:
            kind = (WitnessKind.AWAY if causes == ["idle"]
                    else WitnessKind.CLOSED_EYES)
        if now <= t1:
            # The clock moved backwards while the eye was shut (NTP
            # correcting on wake is common, and a lid close is exactly where
            # it happens). Witness the jump; never silently drop a real
            # blind interval. Same rule as capture/eyelids._close.
            self._sink(witness(
                WitnessKind.CLOCK_JUMP, t_ms=t1, span_t2_ms=t1,
                device_id=self._device,
                detail="eye reopened at %d but closed at %d — clock moved "
                       "backwards %dms while shut (%s); blind interval "
                       "length unknown" % (now, t1, t1 - now, "+".join(causes)),
                structured_extra={"causes": causes, "reopened_t_ms": now,
                                  "backwards_ms": t1 - now}))
        else:
            self._sink(witness(
                kind, t_ms=t1, span_t2_ms=now, device_id=self._device,
                detail="eye was closed for %.0fs (%s)"
                       % ((now - t1) / 1000.0, "+".join(causes)),
                structured_extra={"causes": causes}))
        self._log("eyelid: eye reopened after %.0fs (%s)"
                  % ((now - t1) / 1000.0, "+".join(causes)))
        return "opened"

    def force_open(self, now: int) -> Optional[str]:
        """Shutdown found the eye shut. Close the interval honestly rather
        than leaving a hole that runs to the end of the store."""
        if self._closed_since_ms is None:
            return None
        return self._open(now)

    # -- visibility --------------------------------------------------------

    @property
    def eyes_open(self) -> bool:
        return self._closed_since_ms is None

    def stats(self) -> dict:
        return {"eyes_open": self.eyes_open, "closes": self.closes,
                "reopens": self.reopens, "closed_because": self.last_reason,
                "idle_threshold_s": self._threshold_s}
