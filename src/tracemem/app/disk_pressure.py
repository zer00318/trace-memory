"""OWNS: the free-space monitor and the degrade ladder (map: The machine it
lives on / Disk pressure).

Two atoms, one file, because they are one decision split in half: the
monitor decides WHEN the floor is close, the ladder decides WHAT dies
first, and separating them into modules would only mean a caller could
wire one without the other.

THE LADDER'S ORDER IS THE WHOLE DESIGN (the brief states it, this file
enforces it):

    1. indexes            rebuildable from raw — free, in the sense that
                          nothing is lost, only recomputed later
    2. deep extraction    expensive looks stop; shallow capture continues,
                          so the day is still WITNESSED even if thinly read
    3. capture            the eye closes. Information is now being lost
                          that no later work can recover.
    4. the store's writes NEVER SHED. Not a rung.

Rung 4 is the one that matters. When capture stops, the ONLY thing that
makes the hole honest is a witness claim written to the store saying so.
A ladder that shed store writes under pressure would, at exactly the
moment the product starts failing, also lose its ability to admit it —
and the user would later see silence where there should be a gap. Silence
is indistinguishable from "nothing happened". So the store keeps its
write path down to the last byte, and the reserve below STOP exists to
guarantee it.

THRESHOLD PROVENANCE (read this before trusting the numbers): they are
DECLARED, not measured. The growth meter atom (03_text > Storage math) is
not built, so bytes/day for this store is unknown, and any threshold
expressed as "N days of headroom" would be a number wearing a lab coat.
What the constants below are anchored to instead is stated per-constant,
and they are marked provisional so the growth meter's first real number
replaces them rather than being averaged into them.
"""
from __future__ import annotations

import os
from enum import Enum
from typing import Callable, List, Optional, Tuple

from tracemem.app.witness import WitnessKind, witness
from tracemem.domain import Claim

GIB = 1024 ** 3

# PROVISIONAL (2026-07-22) — declared, not measured. Replace with the growth
# meter's first real bytes/day.
#
# STOP is anchored to a mechanism, not to a guess: SQLite in WAL mode needs
# room for the write-ahead log plus a checkpoint, and a VACUUM transiently
# needs roughly the size of the database again. 1 GiB is chosen to be far
# above the former and to leave the witness path working even mid-checkpoint.
STOP_BYTES = 1 * GIB
# DEGRADE is where expensive work stops so the remaining space lasts longer.
DEGRADE_BYTES = 5 * GIB
# WARN sheds nothing. It exists so the FIRST notice of a disk filling up is
# a recorded event rather than a user noticing their Mac is unhappy.
WARN_BYTES = 20 * GIB

# Crossing back up must clear the threshold by this much before the system
# calls itself recovered. Without it, a volume sitting exactly on a boundary
# writes a witness on every poll — and a witness stream that cries wolf is
# how the coverage audit gets taught to ignore witnesses.
HYSTERESIS_BYTES = 1 * GIB


class Level(Enum):
    """Ordered by severity; compare with `severity()`, never with `<`, since
    Enum ordering is not defined and an accidental string compare would be
    silently wrong."""
    OK = "ok"
    WARN = "warn"
    DEGRADE = "degrade"
    STOP = "stop"


_ORDER = (Level.OK, Level.WARN, Level.DEGRADE, Level.STOP)


def severity(level: "Level") -> int:
    return _ORDER.index(level)


# The rungs, worst-to-shed-last. Order is load-bearing; the test asserts it.
RUNG_INDEXES = "indexes"
RUNG_DEEP_EXTRACTION = "deep-extraction"
RUNG_CAPTURE = "capture"
LADDER: Tuple[str, ...] = (RUNG_INDEXES, RUNG_DEEP_EXTRACTION, RUNG_CAPTURE)

# What is shed AT each level (cumulative — a level sheds everything the
# levels below it shed, plus its own).
_SHED_AT = {
    Level.OK: 0,
    Level.WARN: 0,          # warn is notice, not action
    Level.DEGRADE: 2,       # indexes + deep extraction
    Level.STOP: 3,          # ...and capture
}


def shed_for(level: Level) -> Tuple[str, ...]:
    """Which rungs are switched OFF at this level. The store's writes are
    never in this tuple — they are not a rung (see the module docstring)."""
    return LADDER[:_SHED_AT[level]]


def level_for(free: int) -> Level:
    if free < STOP_BYTES:
        return Level.STOP
    if free < DEGRADE_BYTES:
        return Level.DEGRADE
    if free < WARN_BYTES:
        return Level.WARN
    return Level.OK


def free_bytes(path: str) -> int:
    """Bytes actually usable on the volume holding `path`.

    f_bavail, not f_bfree: the difference is the root-reserved pool, which
    this process cannot write into. Reporting f_bfree would mean the monitor
    stays calm while writes are already failing.
    """
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


class FreeSpaceMonitor:
    """Polls one volume and witnesses every threshold crossing — once.

    Not a thread and not a timer: `poll()` is called by whatever loop
    already exists, so this stays testable without sleeping and there is no
    second scheduler to reason about.
    """

    def __init__(self, path: str, sink: Optional[Callable[[Level, int], None]] = None,
                 reader: Optional[Callable[[str], int]] = None) -> None:
        self.path = path
        self._sink = sink
        self._read = reader or free_bytes
        self.level = Level.OK
        self._crossings = 0

    @property
    def crossings(self) -> int:
        return self._crossings

    def poll(self, now_ms: int) -> Optional[Claim]:
        """Returns the witness to append when the level CHANGED, else None.

        Returning the claim instead of writing it keeps this module free of
        a store dependency: the caller owns the write path, which is the
        same path the ladder must protect.
        """
        free = self._read(self.path)
        new = level_for(free)

        if severity(new) < severity(self.level):
            # Recovering: demand real headroom past the boundary first.
            if free < self._recovery_floor():
                return None

        if new is self.level:
            return None

        was, self.level = self.level, new
        self._crossings += 1
        if self._sink is not None:
            self._sink(new, free)

        direction = "worse" if severity(new) > severity(was) else "better"
        return witness(
            WitnessKind.DISK_PRESSURE, t_ms=now_ms,
            detail=("free space %.1f GiB — %s -> %s (%s); shed: %s"
                    % (free / GIB, was.value, new.value, direction,
                       ", ".join(shed_for(new)) or "nothing")),
            structured_extra={
                "level": new.value, "was": was.value,
                "free_bytes": free, "shed": list(shed_for(new)),
                "volume": self.path,
            })

    def _recovery_floor(self) -> int:
        """The bar a recovery has to clear: the threshold it is climbing out
        of, plus hysteresis."""
        boundary = {Level.STOP: STOP_BYTES, Level.DEGRADE: DEGRADE_BYTES,
                    Level.WARN: WARN_BYTES}.get(self.level)
        return 0 if boundary is None else boundary + HYSTERESIS_BYTES


class DegradeLadder:
    """Holds which rungs are currently off, and reports transitions.

    Deliberately dumb: it owns the ORDER and the record of what is off, not
    the mechanics of stopping an index build or closing the eye. Those live
    with the organs; this is the thing they all agree to read, so the order
    is written down exactly once.
    """

    def __init__(self) -> None:
        self.off: Tuple[str, ...] = ()

    def apply(self, level: Level) -> dict:
        """Move to the rung set for `level`. Returns what changed, so the
        caller can act on the delta rather than re-stopping what is already
        stopped."""
        target = shed_for(level)
        newly_off = [r for r in target if r not in self.off]
        restored = [r for r in self.off if r not in target]
        self.off = target
        return {"level": level.value, "off": list(target),
                "newly_off": newly_off, "restored": restored}

    def is_running(self, rung: str) -> bool:
        if rung not in LADDER:
            raise ValueError(
                "%r is not a ladder rung — the store's own writes are "
                "deliberately not one (see module docstring); asking whether "
                "they are running suggests a caller that intends to shed "
                "them." % (rung,))
        return rung not in self.off

    def store_writes_permitted(self) -> bool:
        """Always True, by construction, at every level including STOP.

        A method rather than a comment because this is the property the
        whole ladder exists to guarantee, and a property nobody can call is
        a property nobody can test.
        """
        return True


def order_of_death() -> List[str]:
    """The declared order, for the board and for the drill to assert against."""
    return list(LADDER)
