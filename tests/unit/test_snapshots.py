"""ORACLE for Snapshots & recovery (05_nodes).

PROOF: "Test: corrupt the db mid-write → restart recovers from snapshot,
witness present."

The corruption here is REAL — bytes written over the middle of the file —
not a mocked failure, because the whole question is whether the checks
notice damage they were not told about.

The tests that matter most are the refusals: a snapshot that does not
verify must be discarded rather than kept, and a store with no good
snapshot must NOT be overwritten. Both are moments where the convenient
behaviour destroys the only copy of something.
"""
import sqlite3

import pytest

from tracemem.app.witness import WitnessKind
from tracemem.text.claim_log import ClaimLog
from tracemem.text.snapshots import (
    SnapshotFailed, latest_good, prune, recover, snapshots, take, verify,
)
from tests.helpers import make_claim


def store_with(tmp_path, n=5, name="store.sqlite3"):
    path = str(tmp_path / name)
    log = ClaimLog(path)
    for i in range(n):
        log.append(make_claim(text="claim %d" % i, t_ms=1000 + i,
                              frame="f%d" % i))
    return path


def corrupt(path):
    """Write garbage over the file's pages — real damage, reliably.

    The checkpoint matters and cost me two failing tests before I saw it:
    the store runs in WAL mode, so freshly written claims live in the
    `-wal` sidecar while the main file is still small and coherent.
    Scribbling on the main file then damaged nothing SQLite cared about,
    integrity_check said "ok", and recover() correctly declined to restore
    a store that was genuinely fine. The test was wrong, not the code.

    Checkpointing folds the WAL into the main database first, so the bytes
    being overwritten are the bytes that hold the claims.
    """
    raw = sqlite3.connect(str(path))
    try:
        raw.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        raw.close()

    size = __import__("os").path.getsize(str(path))
    with open(str(path), "r+b") as fh:
        fh.seek(1024)                       # past the header, into the pages
        fh.write(b"\xde\xad\xbe\xef" * (max(size - 1024, 4096) // 4))


# -- taking a snapshot -----------------------------------------------------
def test_a_snapshot_is_a_verified_copy(tmp_path):
    store = store_with(tmp_path, n=5)
    snap = take(store, tmp_path / "snaps", now_ms=1000)

    assert snap.exists()
    report = verify(snap)
    assert report["ok"] is True and report["claims"] == 5


def test_the_backup_api_is_used_not_a_file_copy(tmp_path):
    """A file copy of a live database opens fine and is subtly torn — the
    worst kind of backup, because you learn that only when you need it."""
    import inspect

    from tracemem.text import snapshots as mod
    assert ".backup(" in inspect.getsource(mod.take)


def test_a_snapshot_taken_while_a_writer_is_open_is_consistent(tmp_path):
    """The live case. The store connection stays open and keeps writing
    after the snapshot; the snapshot must be a coherent point in time."""
    path = str(tmp_path / "live.sqlite3")
    log = ClaimLog(path)
    for i in range(10):
        log.append(make_claim(text="before %d" % i, t_ms=i, frame="b%d" % i))

    snap = take(path, tmp_path / "snaps", now_ms=1)

    for i in range(10):
        log.append(make_claim(text="after %d" % i, t_ms=100 + i,
                              frame="a%d" % i))

    report = verify(snap)
    assert report["ok"] is True
    assert report["claims"] == 10          # the moment it was taken
    assert ClaimLog(path).count() == 20    # the live store moved on


def test_a_snapshot_that_does_not_verify_is_discarded(tmp_path, monkeypatch):
    """A directory of maybe-good backups is worse than one known-good one:
    it invites recovery from a file nobody checked, at the exact moment
    nobody has time to check it."""
    store = store_with(tmp_path)
    from tracemem.text import snapshots as mod
    monkeypatch.setattr(mod, "verify",
                        lambda p: {"ok": False, "reason": "pretend", "claims": 0})

    with pytest.raises(SnapshotFailed):
        take(store, tmp_path / "snaps", now_ms=1000)
    assert snapshots(tmp_path / "snaps") == []


# -- verification is not a formality --------------------------------------
def test_verify_catches_real_corruption(tmp_path):
    store = store_with(tmp_path, n=40)
    assert verify(store)["ok"] is True
    corrupt(store)
    assert verify(store)["ok"] is False


def test_verify_catches_a_tampered_claim_that_sqlite_calls_fine(tmp_path):
    """The two checks catch different things. A file can be structurally
    perfect and hold a chain saying a claim was altered."""
    store = store_with(tmp_path, n=5)
    log = ClaimLog(store)
    victim = log.ids_in_range(0, 10 ** 12)[2]
    row = log._db.execute("SELECT ciphertext FROM claim WHERE id=?",
                          (victim,)).fetchone()
    blob = bytearray(row[0])
    blob[0] ^= 0x01
    log._db.execute("UPDATE claim SET ciphertext=? WHERE id=?",
                    (bytes(blob), victim))
    log._db.commit()

    raw = sqlite3.connect(store)
    assert raw.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    raw.close()

    report = verify(store)
    assert report["ok"] is False
    assert "chain" in report["reason"]


def test_a_missing_store_is_not_ok(tmp_path):
    assert verify(tmp_path / "never.sqlite3")["ok"] is False


# -- THE ORACLE: corrupt → recover → witness ------------------------------
def test_corruption_recovers_from_the_snapshot_with_a_witness(tmp_path):
    store = store_with(tmp_path, n=20)
    take(store, tmp_path / "snaps", now_ms=1000)

    log = ClaimLog(store)
    for i in range(10):
        log.append(make_claim(text="since snapshot %d" % i, t_ms=5000 + i,
                              frame="s%d" % i))
    del log
    corrupt(store)
    assert verify(store)["ok"] is False

    claim = recover(store, tmp_path / "snaps", now_ms=9999)

    assert claim is not None
    assert claim.structured["witness"] == WitnessKind.RECOVERY.value
    assert verify(store)["ok"] is True
    assert ClaimLog(store).count() == 20


def test_the_recovery_witness_carries_what_was_lost(tmp_path):
    """Rolling back discards every claim since the snapshot. That is real
    memory loss — hours of someone's life — and must never be quiet."""
    store = store_with(tmp_path, n=20)
    take(store, tmp_path / "snaps", now_ms=1000)
    log = ClaimLog(store)
    for i in range(10):
        log.append(make_claim(text="lost %d" % i, t_ms=5000 + i,
                              frame="l%d" % i))
    del log
    corrupt(store)

    claim = recover(store, tmp_path / "snaps", now_ms=9999)
    assert claim.structured["claims_restored"] == 20
    assert claim.structured["snapshot"].startswith("snapshot-")

    # THE LOSS IS UNKNOWABLE HERE, AND THAT IS THE ANSWER. This test
    # originally demanded 10 — the ten claims written after the snapshot.
    # It could not have that: a store damaged badly enough to be unreadable
    # reports zero claims, and the subtraction produced "0 lost", which
    # states that nothing was lost at the precise moment an unknown amount
    # was. None is the honest value; a comfortable zero is the dangerous one.
    assert claim.structured["claims_lost_estimate"] is None
    assert "UNKNOWN number lost" in claim.text


def test_a_countable_loss_is_reported_as_a_number(tmp_path):
    """The other branch: when the damaged store CAN still be read — a
    broken chain rather than shredded pages — the difference is real and
    gets stated."""
    store = store_with(tmp_path, n=10)
    take(store, tmp_path / "snaps", now_ms=1000)
    log = ClaimLog(store)
    for i in range(4):
        log.append(make_claim(text="after %d" % i, t_ms=5000 + i,
                              frame="a%d" % i))
    ids = log.ids_in_range(0, 10 ** 12)
    log._db.execute("UPDATE claim_link SET link='deadbeef' WHERE id=?",
                    (ids[3],))
    log._db.commit()
    del log

    claim = recover(store, tmp_path / "snaps", now_ms=9999)
    assert claim.structured["claims_lost_estimate"] == 4
    assert "roughly 4 lost" in claim.text


def test_a_healthy_store_is_left_alone(tmp_path):
    store = store_with(tmp_path, n=5)
    take(store, tmp_path / "snaps", now_ms=1000)
    assert recover(store, tmp_path / "snaps") is None
    assert ClaimLog(store).count() == 5


def test_the_damaged_store_is_kept_not_deleted(tmp_path):
    """It is the only record of whatever was lost, and a person may later
    want it looked at."""
    store = store_with(tmp_path, n=10)
    take(store, tmp_path / "snaps", now_ms=1000)
    corrupt(store)
    claim = recover(store, tmp_path / "snaps", now_ms=9999)

    kept = list(tmp_path.glob("store.sqlite3.damaged-*"))
    assert len(kept) == 1
    assert claim.structured["damaged_store"] == kept[0].name


def test_recovery_refuses_when_no_snapshot_verifies(tmp_path):
    """THE refusal. Overwriting a damaged store with nothing good to put
    there destroys the only copy — and a partial read of it may still be
    worth more than nothing."""
    store = store_with(tmp_path, n=10)
    take(store, tmp_path / "snaps", now_ms=1000)
    for snap in snapshots(tmp_path / "snaps"):
        corrupt(str(snap))
    corrupt(store)

    with pytest.raises(SnapshotFailed) as exc:
        recover(store, tmp_path / "snaps", now_ms=9999)
    assert "nothing honest to restore" in str(exc.value)
    assert __import__("os").path.exists(store), "the damaged store survived"


def test_recovery_skips_a_corrupt_snapshot_for_an_older_good_one(tmp_path):
    """Whatever damaged the store may have reached the newest backup too.
    Snapshots are re-verified at recovery time, not trusted from when they
    were written."""
    store = store_with(tmp_path, n=5)
    old = take(store, tmp_path / "snaps", now_ms=1000)
    ClaimLog(store).append(make_claim(text="later", t_ms=2000, frame="z"))
    new = take(store, tmp_path / "snaps", now_ms=2000)

    corrupt(str(new))
    corrupt(store)

    claim = recover(store, tmp_path / "snaps", now_ms=9999)
    assert claim.structured["snapshot"] == old.name
    assert ClaimLog(store).count() == 5


def test_latest_good_returns_none_when_nothing_verifies(tmp_path):
    assert latest_good(tmp_path / "empty") is None


# -- housekeeping ----------------------------------------------------------
def test_snapshots_are_newest_first(tmp_path):
    store = store_with(tmp_path, n=2)
    take(store, tmp_path / "snaps", now_ms=1000)
    take(store, tmp_path / "snaps", now_ms=3000)
    take(store, tmp_path / "snaps", now_ms=2000)
    names = [p.name for p in snapshots(tmp_path / "snaps")]
    assert names == ["snapshot-3000.sqlite3", "snapshot-2000.sqlite3",
                     "snapshot-1000.sqlite3"]


def test_prune_keeps_the_newest(tmp_path):
    store = store_with(tmp_path, n=2)
    for t in range(1, 6):
        take(store, tmp_path / "snaps", now_ms=t * 1000)
    assert prune(tmp_path / "snaps", keep=2) == 3
    assert [p.name for p in snapshots(tmp_path / "snaps")] == \
        ["snapshot-5000.sqlite3", "snapshot-4000.sqlite3"]
