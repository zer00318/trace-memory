"""OWNS: the permissions matrix and revocation detection (map: The machine
it lives on / Permissions matrix + Revocation detection).

macOS can take a sense away at any moment — a System Settings toggle, an OS
update, an MDM policy — and it does not tell the application. The whole
design of this module follows from one consequence of that:

    BLINDNESS MUST BE KNOWLEDGE, NOT SILENCE.

A denied permission is a first-class, honest, WITNESSED state, not an error
path. The eye losing Screen Recording does not stop the ear. Accessibility
going away does not make window titles wrong — it makes them absent, and
the cards say "unknown window" rather than guessing, because a guessed
window title is a false memory and false memory is the one thing this
product may never produce.

THE NUDGE LAW. On losing a permission the user gets exactly ONE quiet
notice. Not one per poll, not one per hour. A nagging app teaches its user
to dismiss it without reading, and the one notice that mattered gets
dismissed with the rest. The counter is per-loss: re-granting and losing
again earns a fresh single nudge.

THE STATUS READS ARE REAL. Each of the four is the actual system call, and
each was verified against this Mac on 2026-07-22:
  Screen Recording  CGPreflightScreenCaptureAccess()          -> True
  Accessibility     AXIsProcessTrusted()                      -> True
  Microphone        AVCaptureDevice.authorizationStatusFor..  -> 3
  Speech            SFSpeechRecognizer.authorizationStatus    -> read below
Preflight, never request, at read time: the requesting variants raise a
system dialog, and a status poll that can pop a dialog is a status poll
nobody dares run on a timer.

WHY POLLING IS THE BACKSTOP AND NOT THE PLAN: the stream's own errors are
the fast path (SCStream fails immediately when access is pulled). The poll
exists because that path only fires if the stream is running — a permission
revoked while the eye is idle would otherwise be discovered at next boot.
POLL_INTERVAL_S bounds the discovery delay; the atom's oracle demands
under a minute.
"""
from __future__ import annotations

import ctypes
from enum import Enum
from typing import Callable, Dict, List, Optional

from tracemem.app.witness import WitnessKind, witness
from tracemem.domain import Claim

# The atom's oracle: "Revoke during a live run -> detected <60s". The poll
# is set below the bar rather than at it, so that one slow cycle does not
# turn a passing guarantee into a failing one.
POLL_INTERVAL_S = 30


class Permission(Enum):
    SCREEN_RECORDING = "screen-recording"
    MICROPHONE = "microphone"
    ACCESSIBILITY = "accessibility"
    SPEECH = "speech"
    NOTIFICATIONS = "notifications"


class Status(Enum):
    GRANTED = "granted"
    DENIED = "denied"
    NOT_DETERMINED = "not-determined"   # never asked; not the same as refused
    UNKNOWN = "unknown"                 # the read itself failed — say so


# What the product LOSES when each permission is gone, in the product's own
# words. Held as data because two places need it: the witness detail the
# store keeps, and whatever the status item eventually shows a human.
DEGRADED_BY = {
    Permission.SCREEN_RECORDING:
        "eyeless — the daemon stays alive, the ear is unaffected, and the "
        "interval is witnessed as a hole rather than left as silence",
    Permission.MICROPHONE:
        "deaf — the eye is unaffected; speech around you is simply not heard",
    Permission.ACCESSIBILITY:
        "window titles vanish — cards say 'unknown window' and NEVER guess",
    Permission.SPEECH:
        "no on-device transcription — the local ASR path carries it instead",
    Permission.NOTIFICATIONS:
        "no banners — the morning surface never needed them and still works",
}

# The capability each permission gates. An organ asks "may I?" by capability
# so that no organ has to know which macOS toggle backs it.
CAPABILITY = {
    Permission.SCREEN_RECORDING: "eye",
    Permission.MICROPHONE: "ear",
    Permission.ACCESSIBILITY: "window-titles",
    Permission.SPEECH: "apple-speech",
    Permission.NOTIFICATIONS: "banners",
}

# AVAuthorizationStatus / SFSpeechRecognizerAuthorizationStatus share this
# encoding. 1 (restricted, e.g. MDM) reads as DENIED: the practical question
# is only "can we use it", and restricted answers no.
_AV_STATUS = {0: Status.NOT_DETERMINED, 1: Status.DENIED,
              2: Status.DENIED, 3: Status.GRANTED}


def _read_screen_recording() -> Status:
    try:
        import Quartz
        return Status.GRANTED if Quartz.CGPreflightScreenCaptureAccess() \
            else Status.DENIED
    except Exception:
        return Status.UNKNOWN


def _read_accessibility() -> Status:
    """AXIsProcessTrusted via the framework directly — pyobjc's
    ApplicationServices wrapper is not installed in this venv, and ctypes
    needs no wrapper for a function returning a Boolean."""
    try:
        lib = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices"
                          ".framework/ApplicationServices")
        lib.AXIsProcessTrusted.restype = ctypes.c_bool
        return Status.GRANTED if lib.AXIsProcessTrusted() else Status.DENIED
    except Exception:
        return Status.UNKNOWN


def _load_bundle(name: str):
    import objc
    namespace: Dict[str, object] = {}
    objc.loadBundle(name, namespace,
                    bundle_path="/System/Library/Frameworks/%s.framework" % name)
    return namespace


def _read_microphone() -> Status:
    try:
        cls = _load_bundle("AVFoundation")["AVCaptureDevice"]
        # 'soun' is AVMediaTypeAudio's four-char code.
        return _AV_STATUS.get(cls.authorizationStatusForMediaType_("soun"),
                              Status.UNKNOWN)
    except Exception:
        return Status.UNKNOWN


def _read_speech() -> Status:
    try:
        cls = _load_bundle("Speech")["SFSpeechRecognizer"]
        return _AV_STATUS.get(cls.authorizationStatus(), Status.UNKNOWN)
    except Exception:
        return Status.UNKNOWN


def _read_notifications() -> Status:
    """Deliberately NOT_DETERMINED rather than a real read.

    UNUserNotificationCenter's status is only readable from a bundled app
    with a notification entitlement, and asking from a plain process returns
    a value that means nothing. The brief's requirement for this one is to
    VERIFY THE MORNING SURFACE WORKS WITHOUT IT — so the honest report is
    "never asked", and the degraded mode is the only mode until there is an
    app bundle to ask from.
    """
    return Status.NOT_DETERMINED


READERS: Dict[Permission, Callable[[], Status]] = {
    Permission.SCREEN_RECORDING: _read_screen_recording,
    Permission.MICROPHONE: _read_microphone,
    Permission.ACCESSIBILITY: _read_accessibility,
    Permission.SPEECH: _read_speech,
    Permission.NOTIFICATIONS: _read_notifications,
}


def read_all(readers: Optional[Dict[Permission, Callable[[], Status]]] = None
             ) -> Dict[Permission, Status]:
    readers = readers or READERS
    return {p: readers[p]() for p in Permission}


class PermissionsMatrix:
    """Tracks every permission, witnesses each change, nudges once per loss.

    Poll-driven for the same reason the memory tap is: a callback that stops
    firing is indistinguishable from good news.
    """

    def __init__(self, readers: Optional[Dict[Permission, Callable[[], Status]]] = None,
                 nudge: Optional[Callable[[Permission, str], None]] = None,
                 poll_interval_s: int = POLL_INTERVAL_S) -> None:
        self._readers = readers or READERS
        self._nudge = nudge
        self.poll_interval_s = poll_interval_s
        self.status: Dict[Permission, Status] = {}
        self._nudged: set = set()
        self.nudges = 0

    # -- the questions organs actually ask ---------------------------------
    def granted(self, permission: Permission) -> bool:
        return self.status.get(permission) is Status.GRANTED

    def may(self, capability: str) -> bool:
        """Ask by capability ("eye", "ear", "window-titles") so no organ has
        to know which macOS toggle backs it."""
        for permission, name in CAPABILITY.items():
            if name == capability:
                return self.granted(permission)
        raise ValueError("unknown capability %r — capabilities are %r"
                         % (capability, sorted(CAPABILITY.values())))

    def degraded(self) -> List[str]:
        """Plain-words list of what is currently lost. Empty when whole."""
        return [DEGRADED_BY[p] for p in Permission
                if self.status.get(p) not in (Status.GRANTED, None)]

    # -- the loop ----------------------------------------------------------
    def poll(self, now_ms: int) -> List[Claim]:
        """Read every permission; return a witness per CHANGE.

        The first poll is special: it establishes the baseline. A permission
        that was never granted is not a LOSS, and reporting it as one would
        put a permission-loss witness at every boot, which teaches whoever
        reads witnesses that they mean nothing.
        """
        first = not self.status
        out: List[Claim] = []

        for permission in Permission:
            now = self._readers[permission]()
            was = self.status.get(permission)
            self.status[permission] = now
            if first or now is was:
                continue

            if was is Status.GRANTED and now is not Status.GRANTED:
                out.append(self._loss(permission, now, now_ms))
            elif now is Status.GRANTED:
                out.append(self._regain(permission, was, now_ms))
            # Any other transition (not-determined -> denied, unknown ->
            # denied) is a change in what we KNOW, not in what we HAVE. It is
            # deliberately not witnessed: witnesses record holes in
            # perception, and this is a hole in bookkeeping.

        return out

    def _loss(self, permission: Permission, now: Status, now_ms: int) -> Claim:
        nudged_now = False
        if permission not in self._nudged:
            self._nudged.add(permission)
            self.nudges += 1
            nudged_now = True
            if self._nudge is not None:
                self._nudge(permission, DEGRADED_BY[permission])

        return witness(
            WitnessKind.PERMISSION_LOSS, t_ms=now_ms,
            detail="%s %s — %s" % (permission.value, now.value,
                                   DEGRADED_BY[permission]),
            structured_extra={"permission": permission.value,
                              "status": now.value,
                              "capability": CAPABILITY[permission],
                              "nudged": nudged_now})

    def _regain(self, permission: Permission, was: Optional[Status],
                now_ms: int) -> Claim:
        # Re-arm the nudge: losing it again is news again.
        self._nudged.discard(permission)
        return witness(
            WitnessKind.PERMISSION_REGAIN, t_ms=now_ms,
            detail="%s granted (was %s)" % (permission.value,
                                            was.value if was else "unknown"),
            structured_extra={"permission": permission.value,
                              "was": was.value if was else "unknown",
                              "capability": CAPABILITY[permission]})


def worst_case_detection_delay_s(poll_interval_s: int = POLL_INTERVAL_S) -> int:
    """How long blindness can last before it becomes knowledge.

    A function rather than a comment because the atom's oracle is a NUMBER
    ("detected <60s"), and a number that cannot be asserted is a promise
    nobody checks.
    """
    return poll_interval_s
