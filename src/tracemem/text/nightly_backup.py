"""OWNS: the nightly backup and vacuum (map: The machine it lives on /
Store engineering / Backup & vacuum).

The snapshot MECHANISM lives in text/snapshots.py. This is the job that
runs it on a schedule, plus the housekeeping that keeps the store from
growing forever.

ORDER MATTERS, AND IT IS THE ONE DECISION IN THIS FILE:

    snapshot  →  verify  →  prune  →  vacuum

The snapshot is taken BEFORE the vacuum, never after. VACUUM rewrites the
entire database file — every page, in place. It is the single most invasive
thing this product does to its own store on an ordinary night, and it is
therefore the operation most likely to be interrupted by a laptop lid, a
kernel panic, or a battery running out. Taking the backup first means the
night's safety net is already on disk and verified before the risky part
begins. Vacuuming first would mean the one moment the store is most exposed
is also the moment its newest backup does not exist yet.

WHY VACUUM AT ALL: SQLite does not return freed pages to the filesystem.
A store that has had indexes rebuilt, or tombstones applied, or a large
night's derivations pruned, keeps that space allocated forever. On a laptop
carrying a life's memory, "forever" is the operative word.

THE CHAIN SURVIVES VACUUM, and that is checked rather than assumed.
`verify_chain` walks claims in rowid order, and SQLite is permitted to
renumber rowids during a vacuum for tables without an INTEGER PRIMARY KEY —
which `claim` is, since its primary key is a TEXT content id. Measured on
this machine: rowids are preserved and the chain verifies, because the
claim table is strictly append-only and its rowids are therefore dense with
nothing to compact. That reasoning depends on a property (append-only) that
a future tombstone feature could quietly remove, so a test pins the
outcome — if a change ever makes VACUUM reorder claims, the chain would
start reporting false breaks on perfectly good stores, and that is a very
confusing way to find out.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Optional

from tracemem.text import snapshots

DEFAULT_KEEP = 7


def vacuum(store_path) -> dict:
    """Reclaim freed pages. Returns bytes before/after.

    Its own connection, opened and closed here: VACUUM cannot run inside a
    transaction, and borrowing a caller's connection risks inheriting one.
    """
    import os
    before = os.path.getsize(str(store_path))
    db = sqlite3.connect(str(store_path), isolation_level=None)
    try:
        db.execute("VACUUM")
    finally:
        db.close()
    after = os.path.getsize(str(store_path))
    return {"bytes_before": before, "bytes_after": after,
            "reclaimed": before - after}


def nightly(store_path, snapshot_dir, keep: int = DEFAULT_KEEP,
            now_ms: Optional[int] = None, do_vacuum: bool = True) -> dict:
    """One night's store housekeeping. Returns a report worth pasting.

    Raises SnapshotFailed if the backup does not verify — and does NOT
    vacuum in that case. A night that could not produce a good backup is
    not a night to start rewriting every page of the store.
    """
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms

    snapshot = snapshots.take(store_path, snapshot_dir, now_ms=now_ms)
    verified = snapshots.verify(snapshot)
    pruned = snapshots.prune(snapshot_dir, keep=keep)

    report = {
        "snapshot": snapshot.name,
        "claims": verified["claims"],
        "verified": verified["ok"],
        "pruned": pruned,
        "kept": len(snapshots.snapshots(snapshot_dir)),
        "vacuum": None,
    }
    if do_vacuum:
        report["vacuum"] = vacuum(store_path)
    return report
