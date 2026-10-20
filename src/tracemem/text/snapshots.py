"""OWNS: snapshots and recovery (map: Nodes / Snapshots & recovery).

A store that cannot be restored is a store that is one bad sector away from
being a life nobody has any more. This is the seatbelt.

THREE RULES, and the second is the one that makes this worth having.

1. SNAPSHOTS USE SQLITE'S BACKUP API, not a file copy. Copying a live
   database while a writer holds it produces a file that opens fine and is
   subtly torn — the worst kind of backup, because you find out only when
   you need it. The backup API is transactionally consistent against a
   live connection.

2. AN UNVERIFIED BACKUP DOES NOT COUNT AS ONE. Every snapshot is opened
   immediately and checked: integrity_check, and the hash chain walked end
   to end. A snapshot that fails is DELETED rather than kept, because a
   directory of maybe-good backups is worse than one known-good backup —
   it invites recovery from a file nobody checked, at the exact moment
   nobody has time to check it.

3. RECOVERY IS LOUD AND LOSSY, AND SAYS BOTH. Rolling back to a snapshot
   discards every claim written since it was taken. That is real memory
   loss — hours of a person's life — and it is witnessed with the numbers,
   never performed quietly. A product that silently rolled back would leave
   its user believing they still had Tuesday afternoon.

WHAT THIS DOES NOT DO: it does not decide WHEN to snapshot. The schedule is
the night job's business (Backup & vacuum, 11_machine); this is the
mechanism it calls.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import List, Optional

from tracemem.app.witness import WitnessKind, witness
from tracemem.domain import Claim

SNAPSHOT_PREFIX = "snapshot-"
SNAPSHOT_SUFFIX = ".sqlite3"


class SnapshotFailed(RuntimeError):
    """The snapshot did not verify. Never a silent skip: a backup that
    failed and said nothing is the one you will reach for."""


def _snapshot_path(snapshot_dir, t_ms: int) -> Path:
    return Path(snapshot_dir) / ("%s%d%s" % (SNAPSHOT_PREFIX, t_ms,
                                             SNAPSHOT_SUFFIX))


def verify(path) -> dict:
    """Is this file a store we would be willing to restore from?

    Checks both halves: SQLite's own structural integrity, and the claim
    chain. They catch different things — a file can be structurally perfect
    and hold a chain that says a claim was altered.
    """
    path = str(path)
    if not os.path.exists(path):
        return {"ok": False, "reason": "missing", "claims": 0}

    try:
        raw = sqlite3.connect(path)
        try:
            integrity = raw.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            raw.close()
    except sqlite3.DatabaseError as exc:
        return {"ok": False, "reason": "unopenable: %s" % exc, "claims": 0}

    if integrity != "ok":
        return {"ok": False, "reason": "integrity_check: %s" % integrity,
                "claims": 0}

    try:
        from tracemem.text.claim_log import ClaimLog
        log = ClaimLog(path)
        chain = log.verify_chain()
        claims = log.count()
    except Exception as exc:            # noqa: BLE001 — any failure disqualifies
        return {"ok": False, "reason": "unreadable: %r" % exc, "claims": 0}

    if not chain["ok"]:
        return {"ok": False, "claims": claims,
                "reason": "chain %s at %s" % (chain["reason"],
                                              chain["broken_at"])}
    return {"ok": True, "reason": "", "claims": claims}


def take(store_path, snapshot_dir, now_ms: Optional[int] = None) -> Path:
    """Snapshot the store, verify it, and keep it only if it verified.

    Raises SnapshotFailed if the copy does not verify — and removes it, so
    the directory never accumulates files that look like backups and are
    not.
    """
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    Path(snapshot_dir).mkdir(parents=True, exist_ok=True)
    target = _snapshot_path(snapshot_dir, now_ms)

    source = sqlite3.connect(str(store_path))
    try:
        dest = sqlite3.connect(str(target))
        try:
            source.backup(dest)         # transactionally consistent, live-safe
        finally:
            dest.close()
    finally:
        source.close()

    report = verify(target)
    if not report["ok"]:
        target.unlink(missing_ok=True)
        raise SnapshotFailed(
            "snapshot of %s did not verify (%s) and was discarded — a "
            "directory of maybe-good backups is worse than one known-good "
            "one" % (store_path, report["reason"]))
    return target


def snapshots(snapshot_dir) -> List[Path]:
    """Newest first."""
    directory = Path(snapshot_dir)
    if not directory.is_dir():
        return []
    found = [p for p in directory.iterdir()
             if p.name.startswith(SNAPSHOT_PREFIX)
             and p.name.endswith(SNAPSHOT_SUFFIX)]
    return sorted(found, key=lambda p: p.name, reverse=True)


def latest_good(snapshot_dir) -> Optional[Path]:
    """The newest snapshot that still verifies TODAY.

    Re-verified at recovery time rather than trusted from when it was
    written: the thing that corrupted the store may well have reached the
    backups, and that is precisely the moment to check rather than assume.
    """
    for candidate in snapshots(snapshot_dir):
        if verify(candidate)["ok"]:
            return candidate
    return None


def recover(store_path, snapshot_dir,
            now_ms: Optional[int] = None) -> Optional[Claim]:
    """Roll the store back to the newest good snapshot.

    Returns the RECOVERY witness to append, or None if the store was fine
    and nothing was done. Returns the witness rather than writing it because
    the caller owns the write path — and because the witness must land in
    the RESTORED store, not the broken one.
    """
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    current = verify(store_path)
    if current["ok"]:
        return None

    good = latest_good(snapshot_dir)
    if good is None:
        raise SnapshotFailed(
            "store %s is damaged (%s) and NO snapshot verifies — there is "
            "nothing honest to restore from. Refusing to overwrite the "
            "damaged store, because it is now the only copy and a partial "
            "read of it may still be worth more than nothing."
            % (store_path, current["reason"]))

    restored = verify(good)

    # HOW MANY CLAIMS DID THE ROLLBACK COST? Often UNKNOWABLE, and saying so
    # is the whole point. A store damaged badly enough to be unreadable
    # reports zero claims — subtracting that gives "0 lost", which is a
    # confident statement that nothing was lost at the exact moment an
    # unknown amount was. (Found by its own test, which expected 10.)
    #
    # None means "I cannot tell you". A number means the damaged store was
    # still countable and this is the difference.
    if current["claims"] > 0:
        lost = max(0, current["claims"] - restored["claims"])
        lost_text = "roughly %d lost" % lost
    else:
        lost = None
        lost_text = ("an UNKNOWN number lost — the damaged store could not "
                     "be read to count them")

    # The damaged store is MOVED, never deleted. It is the only record of
    # whatever was lost, and a person may later want it looked at.
    damaged = Path(str(store_path) + ".damaged-%d" % now_ms)
    shutil.move(str(store_path), str(damaged))
    shutil.copy2(str(good), str(store_path))

    return witness(
        WitnessKind.RECOVERY, t_ms=now_ms,
        detail=("store rolled back to %s — %s claims restored, %s. "
                "Damaged store kept at %s"
                % (good.name, restored["claims"], lost_text, damaged.name)),
        structured_extra={
            "snapshot": good.name,
            "claims_restored": restored["claims"],
            "claims_lost_estimate": lost,
            "damage": current["reason"],
            "damaged_store": damaged.name,
        })


def prune(snapshot_dir, keep: int = 7) -> int:
    """Keep the newest `keep` snapshots; delete the rest. Returns how many
    were removed. Only ever called on a directory this module wrote."""
    extra = snapshots(snapshot_dir)[keep:]
    for path in extra:
        path.unlink(missing_ok=True)
    return len(extra)
