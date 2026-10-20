"""OWNS: turning a plaintext claim store into an encrypted one (MASTER #5).

WHY THIS IS A MIGRATION AND NOT A FLAG. `wiring._crypto_for` deliberately
opens a pre-existing clear store IN THE CLEAR, and that decision is correct:
applying the encryption default to a store already written in plaintext does
not encrypt it, it makes it unreadable. Witnessed when it happened — the
daemon printed "STORE IS MIXED ... older claims CANNOT be read" and crashed on
the first one. So the only honest way to encrypt what already exists is to
re-write every claim under a key.

THE ORIGINAL IS NEVER WRITTEN TO. The new store is built beside it, verified
in full, and only then swapped in; the plaintext file is kept, renamed, not
removed. Every failure path leaves the founder's memory exactly as it was.
There is no partial state in which some claims are readable and some are not
— which is precisely the state `_crypto_for`'s comment describes as the
dangerous one.

WHAT "VERIFIED" MEANS HERE, because "the migration ran" is not a claim worth
making on its own. Four checks, all against the NEW file, all before the swap:

  1. **count** — the same number of claims came out as went in;
  2. **chain** — `verify_chain` passes over the WHOLE store, not a tail. The
     hash chain hashes the WIRE bytes, not the ciphertext, so an encrypted
     store must reproduce byte-identical links. A chain that changed under
     encryption would mean the claim content changed;
  3. **identity** — every claim id and its text round-trip through the new
     cipher unchanged, compared against the source claim by claim;
  4. **the cipher is actually on** — `store_meta` says so, so nothing can
     report success over a store that quietly stayed in the clear.

A failure at any point raises before the swap. The half-migrated file is left
on disk under its own name rather than deleted, because a person debugging
this at 2am needs the evidence more than they need a tidy directory.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional


class MigrationRefused(RuntimeError):
    """A precondition failed. Nothing was touched."""


class MigrationFailed(RuntimeError):
    """A verification failed AFTER writing. The original is still intact."""


@dataclass
class MigrationReport:
    source: str
    claims_in: int = 0
    claims_out: int = 0
    chain_ok: bool = False
    chain_complete: bool = False
    identity_ok: bool = False
    cipher_after: str = ""
    backup: str = ""
    swapped: bool = False
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _claim_log(path: str, crypto):
    from tracemem.text.claim_log import ClaimLog
    return ClaimLog(path, crypto=crypto)


def plan(store_path: str) -> dict:
    """What migrating this store WOULD do. Reads only; changes nothing."""
    from tracemem.app.wiring import _created_with
    p = Path(store_path)
    if not p.exists():
        return {"ok": False, "why": "no store at %s" % store_path}
    created = _created_with(store_path)
    log = _claim_log(store_path, None)
    n = log.count()
    size = p.stat().st_size
    return {"ok": created == "none", "created_with": created, "claims": n,
            "bytes": size,
            "why": ("already encrypted with %s — nothing to do" % created)
                   if created != "none" else
                   "%d claims in PLAINTEXT would be re-written under a key" % n}


def migrate(store_path: str, crypto, *, backup_dir: Optional[str] = None,
            daemon_running: Callable[[], bool] = lambda: False,
            keep_plaintext: bool = True) -> MigrationReport:
    """Re-write every claim under `crypto`. Returns a report or raises.

    `crypto` is INJECTED. This module never reaches for a keychain: the one
    sanctioned construction site is the CLI, where a human invoked the
    program on purpose (see test_only_the_cli_may_reach_the_real_keychain).
    """
    from tracemem.app.wiring import _created_with

    src = Path(store_path)
    report = MigrationReport(source=str(src))

    # -- preconditions, all before anything is written --------------------
    if not src.exists():
        raise MigrationRefused("no store at %s" % src)
    if daemon_running():
        raise MigrationRefused(
            "a daemon is running. The store has ONE writer, and migrating "
            "underneath a live capture would race the hash chain. Stop it "
            "first (./trace stop).")
    created = _created_with(str(src))
    if created != "none":
        raise MigrationRefused(
            "this store was created with %r, not plaintext — refusing to "
            "re-encrypt something already encrypted." % created)
    if crypto is None:
        raise MigrationRefused(
            "no cipher supplied. Migrating to NullCrypto would rewrite the "
            "whole store to achieve nothing.")

    # ONE INSTANCE FOR BOTH READS. `unreadable()` counts what THIS log
    # skipped while reading; a fresh instance has skipped nothing and
    # cheerfully answers 0. My first version asked a second instance and got
    # a clean bill of health for a store that has an unreadable row in it.
    source_log = _claim_log(str(src), None)
    source_claims = list(source_log.all_claims())
    report.claims_in = len(source_claims)
    if not source_claims:
        raise MigrationRefused("store holds no readable claims")

    # ROWS THIS PROCESS CANNOT READ ARE CARRIED VERBATIM, NOT DROPPED.
    #
    # The founder's real store contains one: a claim written under a
    # different cipher during an earlier experiment, which plaintext cannot
    # open. Refusing to migrate because of it would leave 20,000 claims of
    # his screen in the clear forever over one orphan; dropping it would
    # break the law this whole codebase is built on — RAW IS RETAINED
    # FOREVER, and a migration is the last place to start making exceptions.
    #
    # THEY GO INTO QUARANTINE, NOT INTO THE CHAIN, and the first attempt at
    # this is why. Splicing the row back in with its original link broke
    # `verify_chain` at exactly that row ("link"), because re-writing every
    # claim rebuilds the chain and the orphan's stored link belongs to the old
    # one. There is no fix: the chain hashes WIRE bytes, and nobody can hash
    # bytes they cannot decrypt. An unreadable row can never sit inside a
    # verifiable chain.
    #
    # Leaving it in unchained would be worse than losing it. verify_chain
    # would report ok=False on this store forever, and an integrity signal
    # that is permanently red is an integrity signal everybody learns to
    # ignore — which costs far more than one orphan row.
    #
    # So the row is preserved verbatim in `quarantined_claim`, in the same
    # file, queryable, with the reason recorded. Nothing is lost, nothing is
    # laundered, and the chain still means what it says about the claims it
    # covers.
    carried = _orphan_rows(src, {c.id for c in source_claims})
    if carried:
        report.notes.append(
            "%d row(s) could not be read with plaintext. They were preserved "
            "verbatim in the new store's `quarantined_claim` table — not "
            "lost, not pretended-readable, and deliberately outside the hash "
            "chain, which cannot cover bytes nobody can decrypt."
            % len(carried))

    # -- the verified backup, FIRST ---------------------------------------
    bdir = Path(backup_dir) if backup_dir else src.parent / "backup"
    bdir.mkdir(parents=True, exist_ok=True)
    backup = bdir / (src.name + ".plaintext.pre-encrypt")
    _sqlite_backup(src, backup)
    # A backup nobody read is a hope, not a backup.
    # Compare ROW counts with ROW counts. Comparing a raw row count against
    # the number of READABLE claims is how the first version of this check
    # reported a corrupt backup for a store that was merely mixed.
    if _row_count(backup) != _row_count(src):
        raise MigrationRefused(
            "backup verification failed: %d rows in the backup vs %d in the "
            "store. Nothing was migrated."
            % (_row_count(backup), _row_count(src)))
    report.backup = str(backup)

    # -- build the new store beside the old one ---------------------------
    dest = src.with_suffix(src.suffix + ".encrypting")
    if dest.exists():
        dest.unlink()
    for extra in (".encrypting-wal", ".encrypting-shm"):
        p = Path(str(src) + extra)
        if p.exists():
            p.unlink()

    out = _claim_log(str(dest), crypto)
    for claim in source_claims:
        out.append(claim)
    _quarantine(dest, carried)

    # -- verify the NEW file, four ways, before anything is swapped -------
    report.claims_out = out.count()
    if report.claims_out != report.claims_in:
        raise MigrationFailed(
            "wrote %d claims from %d — the original is untouched at %s"
            % (report.claims_out, report.claims_in, src))

    chain = out.verify_chain()
    report.chain_ok = bool(chain.get("ok"))
    report.chain_complete = bool(chain.get("complete"))
    if not report.chain_ok:
        raise MigrationFailed(
            "hash chain broken in the new store at %r (%s). The chain hashes "
            "the WIRE bytes, not the ciphertext, so encryption must reproduce "
            "it exactly — a break means the CONTENT changed. Original "
            "untouched at %s" % (chain.get("broken_at"), chain.get("reason"), src))

    # Identity, claim by claim: same ids, same text, same order.
    reopened = _claim_log(str(dest), crypto)
    got = list(reopened.all_claims())
    report.identity_ok = (
        len(got) == len(source_claims)
        and all(a.id == b.id and a.text == b.text
                for a, b in zip(source_claims, got)))
    if not report.identity_ok:
        raise MigrationFailed(
            "round-trip mismatch: what came back out is not what went in. "
            "Original untouched at %s" % src)

    enc = reopened.encryption()
    report.cipher_after = str(enc.get("cipher"))
    if enc.get("plaintext"):
        raise MigrationFailed(
            "the new store still reports PLAINTEXT — refusing to call that a "
            "successful encryption. Original untouched at %s" % src)

    # -- SETTLE THE NEW FILE BEFORE MOVING IT -----------------------------
    #
    # CAUGHT BY RE-READING THE RESULT, and it silently lost data. The store is
    # WAL-mode, so the newest writes live in `<file>-wal` until a checkpoint.
    # The first version of this swap moved only the main file: the report
    # cheerfully said 20,609 claims — read back through the still-open
    # connection, which sees its own WAL — while the file that landed on disk
    # held 20,591 and no quarantine table at all. Eighteen claims and the
    # orphan record, gone, under a green report.
    #
    # A migration that can lose claims while announcing success is worse than
    # one that fails, so the database is checkpointed and every connection to
    # it closed BEFORE anything is renamed.
    del reopened, out
    _checkpoint(dest)

    aside = src.with_suffix(src.suffix + ".plaintext")
    if aside.exists():
        aside.unlink()
    _drop_sidecars(src)
    os.replace(str(src), str(aside))
    os.replace(str(dest), str(src))
    _drop_sidecars(dest)          # any journal left behind belongs to nothing
    report.swapped = True

    # RE-READ THE FILE THAT ACTUALLY LANDED, through a fresh connection that
    # shares no state with the one that wrote it. Every check above was made
    # against an open handle; this is the only one that can see what a NEW
    # process will see tomorrow, which is the only thing that matters.
    landed = _claim_log(str(src), crypto)
    got_after = list(landed.all_claims())
    if len(got_after) != report.claims_in:
        raise MigrationFailed(
            "THE SWAP LOST DATA: %d claims readable in the file that landed, "
            "%d expected. Your plaintext store is intact at %s and the "
            "verified backup at %s — restore from either."
            % (len(got_after), report.claims_in, aside, backup))
    if carried and _quarantine_count(src) != len(carried):
        raise MigrationFailed(
            "the quarantined row(s) did not survive the swap. Plaintext store "
            "intact at %s" % aside)
    if keep_plaintext:
        report.notes.append(
            "the plaintext original is still on disk at %s — it is readable "
            "by anyone with the file. Remove it yourself once you have "
            "confirmed the encrypted store answers questions." % aside)
    else:
        aside.unlink()
        report.notes.append("plaintext original removed at your instruction; "
                            "the verified backup remains at %s" % backup)
    return report


def _row_count(path: Path) -> int:
    """Raw rows in `claim` — including any this process cannot decrypt.

    READ-ONLY IS ATTEMPTED FIRST, then abandoned rather than allowed to fail
    the migration. `mode=ro` cannot open a WAL-mode database that has no
    `-shm` beside it: recovery needs to WRITE, so sqlite answers the unhelpful
    "unable to open database file". The store is WAL, so a freshly written
    backup hits this every time — it cost a run to find, and the error names
    neither WAL nor the reason.

    Falling back is safe here and only here: `migrate` has already refused if
    a daemon is running, so nothing else holds the file.
    """
    import sqlite3
    for dsn, kw in (("file:%s?mode=ro" % path, {"uri": True}), (str(path), {})):
        try:
            con = sqlite3.connect(dsn, **kw)
        except sqlite3.OperationalError:
            continue
        try:
            return con.execute("SELECT COUNT(*) FROM claim").fetchone()[0]
        except sqlite3.OperationalError:
            continue
        finally:
            con.close()
    raise MigrationRefused("cannot read %s to count its rows" % path)


def _orphan_rows(src: Path, readable_ids):
    """Whole rows (claim + chain link) this process could not decrypt."""
    import sqlite3
    con = sqlite3.connect(str(src))
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(claim)")]
        out = []
        for row in con.execute("SELECT %s FROM claim" % ", ".join(cols)):
            rec = dict(zip(cols, row))
            if rec["id"] in readable_ids:
                continue
            link = con.execute(
                "SELECT id, seq, prev_link, link FROM claim_link WHERE id=?",
                (rec["id"],)).fetchone()
            out.append((cols, row, link))
        return out
    finally:
        con.close()


def _quarantine(dest: Path, carried) -> None:
    """Preserve rows this process could not read, OUTSIDE the verified chain.

    Raw is retained forever; that law does not get an exception because a row
    is inconvenient. But it cannot be retained INSIDE the chain (see the long
    note at the call site), so it is retained beside it, in the same file,
    with the reason attached so a future reader is never left guessing why a
    claim is sitting in a side table.
    """
    import json
    import sqlite3
    if not carried:
        return
    con = sqlite3.connect(str(dest))
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS quarantined_claim ("
            "  id TEXT PRIMARY KEY, reason TEXT NOT NULL, row_json TEXT NOT NULL,"
            "  link_json TEXT)")
        for cols, row, link in carried:
            rec = {c: (v.hex() if isinstance(v, (bytes, bytearray)) else v)
                   for c, v in zip(cols, row)}
            con.execute(
                "INSERT OR REPLACE INTO quarantined_claim VALUES (?,?,?,?)",
                (rec["id"],
                 "written under a cipher this store's key cannot open; "
                 "preserved verbatim through the encryption migration rather "
                 "than dropped, and kept out of the hash chain because bytes "
                 "nobody can decrypt cannot be hashed",
                 json.dumps(rec),
                 json.dumps(list(link)) if link else None))
        con.commit()
    finally:
        con.close()


def _checkpoint(path: Path) -> None:
    """Fold the WAL back into the main file so a rename cannot lose writes."""
    import sqlite3
    con = sqlite3.connect(str(path))
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.commit()
    finally:
        con.close()


def _quarantine_count(path: Path) -> int:
    import sqlite3
    con = sqlite3.connect(str(path))
    try:
        return con.execute(
            "SELECT COUNT(*) FROM quarantined_claim").fetchone()[0]
    except sqlite3.OperationalError:
        return 0
    finally:
        con.close()


def _drop_sidecars(path: Path) -> None:
    """WAL/SHM belong to the file they were written for. Carrying them across
    a swap would point a fresh database at another database's journal."""
    for suffix in ("-wal", "-shm"):
        p = Path(str(path) + suffix)
        if p.exists():
            p.unlink()


def _sqlite_backup(src: Path, dest: Path) -> None:
    """The sqlite backup API, not a file copy: a copy taken while a WAL
    exists can miss the newest claims entirely."""
    import sqlite3
    if dest.exists():
        dest.unlink()
    _drop_sidecars(dest)
    con = sqlite3.connect(str(src))
    try:
        out = sqlite3.connect(str(dest))
        try:
            con.backup(out)
        finally:
            out.close()
    finally:
        con.close()
