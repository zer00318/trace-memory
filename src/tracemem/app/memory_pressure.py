"""OWNS: the memory-pressure tap and the ring's response to it (map: The
machine it lives on / Memory pressure).

WHAT THE SIGNAL ACTUALLY IS. macOS exposes memory pressure two ways, and
they are the same number: `DISPATCH_SOURCE_TYPE_MEMORYPRESSURE`, which
calls you back on a dispatch queue, and the sysctl
`kern.memorystatus_vm_pressure_level`, which you read. Both report the
kernel's `kVMPressure*` state with the same values (1 normal / 2 warn /
4 critical). This module reads the sysctl.

WHY THE SYSCTL RATHER THAN THE DISPATCH SOURCE, since the brief names the
dispatch source: the callback form needs a live run loop and a C function
pointer held alive for the process's lifetime, and its failure mode is
silence — a tap that stopped firing looks exactly like a Mac that is not
under pressure. That is the worst possible shape for a signal whose whole
job is to warn. A poll returns a value or raises; it cannot quietly stop.
The daemon already has a loop to poll from, so the callback buys nothing
here. Recorded as a deliberate deviation from the brief, not an oversight;
if sub-poll-interval latency ever matters, the dispatch source can feed
the same `PressureTap.observe()` door.

WHAT IS PROVEN AND WHAT IS NOT (2026-07-22): the read is real and verified
against this Mac. The response logic is unit-proven. The clause "verify
with real allocation pressure that swap stays at zero" is NOT run — it
needs `memory_pressure -l critical`, which stresses the whole machine, and
this Mac is running the old stack's live capture, which the laws forbid
disturbing. That half is a drill, and it is written down as un-run rather
than quietly counted.
"""
from __future__ import annotations

import ctypes
import ctypes.util
from enum import Enum
from typing import Callable, List, Optional

from tracemem.app.witness import WitnessKind, witness
from tracemem.domain import Claim
from tracemem.extraction.retina import DEFAULT_BUDGET_BYTES

MIB = 1024 * 1024

SYSCTL_NAME = b"kern.memorystatus_vm_pressure_level"

# The kernel's values, not ours. Named here so a reader does not have to
# guess why 4 follows 2 (it is a bitmask in the dispatch API).
_NORMAL, _WARN, _CRITICAL = 1, 2, 4


class Pressure(Enum):
    NORMAL = "normal"
    WARN = "warn"
    CRITICAL = "critical"


_FROM_KERNEL = {_NORMAL: Pressure.NORMAL, _WARN: Pressure.WARN,
                _CRITICAL: Pressure.CRITICAL}
_ORDER = (Pressure.NORMAL, Pressure.WARN, Pressure.CRITICAL)


def severity(p: Pressure) -> int:
    return _ORDER.index(p)


# The ring's target size at each level.
#
# WARN -> half the ceiling: the brief's number. It is a give-bytes-back
# gesture chosen to be large enough to matter and small enough not to blind
# the eye.
#
# CRITICAL -> "minimum viable ring". Anchored to a mechanism rather than
# taste: the ring must still hold the CURRENT scene plus the one being
# compared against it, or motion detection has nothing to difference and
# capture has effectively stopped while still claiming to run. A 4K frame at
# 4 bytes/pixel is ~32 MiB, so two of them plus slack is 96 MiB. Below this
# the honest move is to close the eye and witness it, NOT to keep a ring too
# small to see with.
WARN_FRACTION = 0.5
MIN_VIABLE_RING_BYTES = 96 * MIB


def _read_sysctl_int(name: bytes) -> int:
    """Read an integer sysctl through libc.

    Via ctypes rather than shelling out to /usr/sbin/sysctl because this is
    polled on the daemon's loop, and a subprocess per poll is a fork per
    poll — on a laptop, that is battery spent to ask a question that costs
    a syscall.
    """
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    value = ctypes.c_int(0)
    size = ctypes.c_size_t(ctypes.sizeof(value))
    rc = libc.sysctlbyname(name, ctypes.byref(value), ctypes.byref(size),
                           None, ctypes.c_size_t(0))
    if rc != 0:
        raise OSError(ctypes.get_errno(),
                      "sysctlbyname(%s) failed" % name.decode())
    return value.value


def read_pressure() -> Pressure:
    """The machine's CURRENT pressure level, read from the kernel.

    An unknown value maps to CRITICAL, not NORMAL: if the kernel starts
    reporting something this code does not understand, the safe assumption
    is that things are bad, because the failure that costs the user data is
    "we thought everything was fine".
    """
    raw = _read_sysctl_int(SYSCTL_NAME)
    return _FROM_KERNEL.get(raw, Pressure.CRITICAL)


def ring_budget_for(pressure: Pressure,
                    ceiling_bytes: int = DEFAULT_BUDGET_BYTES) -> int:
    """The ring's byte budget at this pressure level."""
    if pressure is Pressure.NORMAL:
        return ceiling_bytes
    if pressure is Pressure.WARN:
        return max(MIN_VIABLE_RING_BYTES, int(ceiling_bytes * WARN_FRACTION))
    return MIN_VIABLE_RING_BYTES


class PressureTap:
    """Reads the level and fans changes out to whoever must react.

    Consumers are callables taking the Pressure. Two are expected (the
    retina's shrink and the auction's governor) but the tap does not name
    them: it is a signal source, and a signal source that knows its
    consumers is one that has to be edited to add a third.
    """

    def __init__(self, consumers: Optional[List[Callable[[Pressure], None]]] = None,
                 reader: Optional[Callable[[], Pressure]] = None) -> None:
        self._consumers = list(consumers or [])
        self._read = reader or read_pressure
        self.level = Pressure.NORMAL
        self.events = 0

    def subscribe(self, consumer: Callable[[Pressure], None]) -> None:
        self._consumers.append(consumer)

    def poll(self, now_ms: int) -> Optional[Claim]:
        return self.observe(self._read(), now_ms)

    def observe(self, level: Pressure, now_ms: int) -> Optional[Claim]:
        """The door a dispatch-source callback would use. Returns the witness
        on a CHANGE, else None."""
        if level is self.level:
            return None
        was, self.level = self.level, level
        self.events += 1

        # Fan out before witnessing: the point of the signal is the reaction,
        # and a store hiccup must not cost the ring its shrink.
        for consumer in self._consumers:
            consumer(level)

        return witness(
            WitnessKind.MEMORY_PRESSURE, t_ms=now_ms,
            detail="memory pressure %s -> %s; ring budget %d MiB"
                   % (was.value, level.value, ring_budget_for(level) // MIB),
            structured_extra={"level": level.value, "was": was.value,
                              "ring_budget_bytes": ring_budget_for(level)})


class RingShrinkResponse:
    """Wires a tap to a Retina. Give this to `PressureTap(consumers=[...])`.

    Holds the ceiling the ring started with, so recovery restores the real
    budget instead of whatever the ring happened to be shrunk to — a
    detail that decides whether the eye ever gets its resolution back after
    one bad afternoon.
    """

    def __init__(self, retina, ceiling_bytes: Optional[int] = None) -> None:
        self._retina = retina
        self.ceiling_bytes = ceiling_bytes or retina.budget_bytes
        self.evicted_total = 0

    def __call__(self, pressure: Pressure) -> None:
        target = ring_budget_for(pressure, self.ceiling_bytes)
        evicted = self._retina.shrink(target)
        self.evicted_total += len(evicted)
