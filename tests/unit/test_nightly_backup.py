"""ORACLE for Backup & vacuum (11_machine > Store engineering).

PROOF: "A week of nightly verified backups; one restore drill passed."

The week is wall-clock and belongs to a real deployment. What is provable
now: seven consecutive nightly runs each produce a VERIFIED backup, the
retention keeps what it says, the restore drill actually restores, and —
the one worth the most — the hash chain survives a vacuum.
"""
import os
import sqlite3

import pytest

from tracemem.text import snapshots
from tracemem.text.claim_log import ClaimLog
from tracemem.text.nightly_backup import DEFAULT_KEEP, nightly, vacuum
from tests.helpers import make_claim


def store_with(tmp_path, n=20, name="store.sqlite3"):
    path = str(tmp_path / name)
    log = ClaimLog(path)
    for i in range(n):
        log.append(make_claim(text="claim %d" % i, t_ms=1000 + i,
                              frame="f%d" % i))
    return path


# -- the nightly job -------------------------------------------------------
def test_a_night_produces_a_verified_backup(tmp_path):
    store = store_with(tmp_path)
    report = nightly(store, tmp_path / "snaps", now_ms=1000)

    assert report["verified"] is True
    assert report["claims"] == 20
    assert report["snapshot"].startswith("snapshot-")


def test_seven_nights_keep_seven_backups(tmp_path):
    """The week, compressed. Each night verifies its own backup, and the
    retention window holds."""
    store = store_with(tmp_path, n=5)
    for night in range(1, 8):
        log = ClaimLog(store)
        log.append(make_claim(text="night %d" % night, t_ms=9000 + night,
                              frame="n%d" % night))
        del log
        report = nightly(store, tmp_path / "snaps", now_ms=night * 100000)
        assert report["verified"] is True

    kept = snapshots.snapshots(tmp_path / "snaps")
    assert len(kept) == DEFAULT_KEEP
    for snap in kept:
        assert snapshots.verify(snap)["ok"] is True


def test_the_eighth_night_prunes_the_oldest(tmp_path):
    store = store_with(tmp_path, n=3)
    for night in range(1, 9):
        nightly(store, tmp_path / "snaps", now_ms=night * 100000)

    names = [p.name for p in snapshots.snapshots(tmp_path / "snaps")]
    assert len(names) == DEFAULT_KEEP
    assert "snapshot-100000.sqlite3" not in names      # night 1, gone
    assert "snapshot-800000.sqlite3" in names          # night 8, kept


def test_a_failed_backup_stops_the_night_before_vacuuming(tmp_path, monkeypatch):
    """A night that could not produce a good backup is not a night to start
    rewriting every page of the store."""
    store = store_with(tmp_path)
    vacuumed = []
    import tracemem.text.nightly_backup as mod
    monkeypatch.setattr(mod.snapshots, "take",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            snapshots.SnapshotFailed("pretend")))
    monkeypatch.setattr(mod, "vacuum", lambda p: vacuumed.append(p))

    with pytest.raises(snapshots.SnapshotFailed):
        nightly(store, tmp_path / "snaps", now_ms=1000)
    assert vacuumed == [], "vacuumed despite having no verified backup"


def test_the_snapshot_is_taken_before_the_vacuum(tmp_path):
    """ORDER IS THE ONE DECISION IN THIS FILE. Vacuum rewrites every page
    and is the most interruptible thing the product does to its own store;
    the safety net must already be on disk before it starts."""
    import inspect

    from tracemem.text import nightly_backup
    source = inspect.getsource(nightly_backup.nightly)
    assert source.index("snapshots.take") < source.index("vacuum(")


# -- vacuum ----------------------------------------------------------------
def test_vacuum_reclaims_space_after_a_deletion(tmp_path):
    """SQLite does not return freed pages to the filesystem on its own. On
    a laptop carrying a life's memory, 'forever' is the operative word."""
    store = store_with(tmp_path, n=400)
    raw = sqlite3.connect(store)
    raw.execute("DELETE FROM claim_link")          # a derived, rebuildable index
    raw.commit()
    raw.close()

    report = vacuum(store)
    assert report["reclaimed"] > 0
    assert report["bytes_after"] < report["bytes_before"]


def test_vacuum_leaves_every_claim_readable(tmp_path):
    store = store_with(tmp_path, n=50)
    vacuum(store)
    assert ClaimLog(store).count() == 50


def test_THE_CHAIN_SURVIVES_A_VACUUM(tmp_path):
    """The test this file exists for.

    verify_chain walks claims in ROWID order, and SQLite may renumber
    rowids during a vacuum for tables without an INTEGER PRIMARY KEY —
    which `claim` is, since its key is a TEXT content id. Measured: rowids
    survive, because the claim table is strictly append-only and its rowids
    are dense with nothing to compact.

    That reasoning rests on a property a future tombstone feature could
    quietly remove. If it ever does, the chain would start reporting FALSE
    BREAKS on perfectly good stores — a very confusing way to discover a
    design change — so the outcome is pinned here rather than left to the
    argument.
    """
    store = store_with(tmp_path, n=60)
    before = ClaimLog(store).verify_chain()
    assert before["ok"] is True and before["checked"] == 60

    vacuum(store)

    after = ClaimLog(store).verify_chain()
    assert after["ok"] is True, "VACUUM reordered claims: %r" % (after,)
    assert after["checked"] == 60


def test_the_chain_survives_a_full_nightly_run(tmp_path):
    store = store_with(tmp_path, n=40)
    nightly(store, tmp_path / "snaps", now_ms=1000)
    assert ClaimLog(store).verify_chain()["ok"] is True


# -- the restore drill -----------------------------------------------------
def test_the_restore_drill(tmp_path):
    """PROOF's second clause: 'one restore drill passed.' Backup, corrupt,
    restore, verify — the whole round trip."""
    store = store_with(tmp_path, n=30)
    nightly(store, tmp_path / "snaps", now_ms=1000)

    raw = sqlite3.connect(store)
    raw.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    raw.close()
    size = os.path.getsize(store)
    with open(store, "r+b") as fh:
        fh.seek(1024)
        fh.write(b"\xde\xad\xbe\xef" * (max(size - 1024, 4096) // 4))

    assert snapshots.verify(store)["ok"] is False
    claim = snapshots.recover(store, tmp_path / "snaps", now_ms=2000)

    assert claim is not None
    assert snapshots.verify(store)["ok"] is True
    assert ClaimLog(store).count() == 30
    assert ClaimLog(store).verify_chain()["ok"] is True
