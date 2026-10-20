"""OWNS: the fsync law — when is a claim REALLY on disk (map: Text /
Durability policy).

THE DECISION (2026-07-21, this atom): **WAL + synchronous=NORMAL, one
commit per claim.** Declared loss windows, stated as a promise the product
must keep:

  process death (kill -9, crash, OOM kill) ......... ZERO claims lost
  OS panic or power loss .......................... up to the last WAL
                                                    checkpoint may be lost

WHY NOT the two obvious alternatives:

  synchronous=FULL per claim (the "absolute" option) buys durability against
  POWER LOSS at the cost of an fsync per claim. Capture writes continuously,
  all day, on a laptop — an fsync per claim is a physical write amplification
  the eye pays for forever, to defend against a failure mode (sudden power
  loss on a battery-backed machine) that is rarer than the one it slows down.

  Batched commits with a time window (the "fast" option) would lose claims to
  a plain process kill. That trade is wrong HERE specifically, because of what
  a claim is: the eye cannot re-observe the moment. A dropped web request gets
  retried; a dropped claim is a hole in a life that nothing can refill. Losing
  the last 2 seconds of perception to a crash is the one loss this system
  cannot make good.

WAL + NORMAL sits exactly at the seam: durable against everything the
software can do to itself, honest about the hardware failure it does not
cover. The number that makes it defensible is measured, not assumed —
tests/unit/test_durability.py kills a live appender with SIGKILL and counts
what survived (the atom's PROOF).

THE HONESTY CLAUSE: the power-loss window is REAL and must never be
described away. A user asking "is it saved?" is owed "yes, unless the
machine loses power in the next few seconds", not "yes".
"""
from __future__ import annotations

# The declared policy, importable so nothing re-decides it locally.
JOURNAL_MODE = "WAL"
SYNCHRONOUS = "NORMAL"

# Claims lost when the PROCESS dies. Not an estimate — the measured result
# of tests/unit/test_durability.py::test_kill_9_during_heavy_append_loses_nothing
MAX_LOSS_ON_PROCESS_DEATH = 0

# Claims lost on power loss: bounded by WAL checkpoint cadence, NOT zero, and
# not measurable without cutting real power to real hardware. Labeled as the
# open hole it is rather than given a comfortable number.
MAX_LOSS_ON_POWER_LOSS = "unmeasured — bounded by the WAL checkpoint interval"


def apply(db) -> None:
    """Stamp the policy onto a live sqlite3 connection.

    Called by ClaimLog at open. Kept as a function so there is exactly one
    place these pragmas are set, and so a test can prove they took effect
    on a real connection rather than trusting that they were requested.
    """
    db.execute(f"PRAGMA journal_mode={JOURNAL_MODE}")
    db.execute(f"PRAGMA synchronous={SYNCHRONOUS}")


def describe(db) -> dict:
    """What the connection ACTUALLY has — SQLite silently ignores a
    journal_mode change it cannot honour (e.g. in-memory databases), so the
    policy is read back rather than assumed."""
    mode = db.execute("PRAGMA journal_mode").fetchone()[0]
    sync = db.execute("PRAGMA synchronous").fetchone()[0]
    return {"journal_mode": str(mode).upper(),
            "synchronous": int(sync)}     # 0=OFF 1=NORMAL 2=FULL
