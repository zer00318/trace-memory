"""OWNS: schema migration of an existing claim store. ADDITIVE ONLY.

WHY THIS IS A SEPARATE MODULE, and not four lines inside claim_log.py.

claim_log.py holds a property its own docstring states and a test enforces
by grepping the file: no row-rewriting or table-reshaping SQL against the
claim table appears in it — not update, not delete, not drop, not alter.
(The test spells those four out; this file deliberately does not, since it
is grepped by the same rule.) That is the reasoning the hash chain already
lives by — the chain is a separate table precisely so that sealing a store
never needs a write path INTO raw. Once a statement that can rewrite the
claim table exists in the module that owns the claim table, "raw is
immutable" stops being a property and becomes a comment about intentions.

Adding the `nonce` column (2026-07-23) needed exactly such a statement. The
honest options were to weaken the guard, to skip migration and abandon
existing stores, or to move the statement somewhere it can be constrained
harder than it was before. This file is the third.

WHAT IS PERMITTED HERE, and it is checked by test_durability: adding a
column, and nothing else. ADD COLUMN is the one schema change SQLite
performs by editing the table header alone; every existing claim's bytes
are untouched, which is why the hash chain — computed over wire bytes —
still verifies across a migration. Any statement that could rewrite a
stored claim belongs to no module in this system.
"""
from __future__ import annotations

import sqlite3
from typing import List

# (column name, full DDL). Append only; never edit or remove an entry — a
# store in the field may be at any point in this list.
_ADDITIVE_COLUMNS = [
    ("nonce", "ALTER TABLE claim ADD COLUMN nonce BLOB NOT NULL DEFAULT x''"),
    # The epistemic class (SCHEMA §2.1 part 7). Existing rows default to
    # 'lived' — every claim written before this column existed was a live
    # screen observation, so the default is the truth, not a guess.
    ("klass", "ALTER TABLE claim ADD COLUMN klass TEXT NOT NULL DEFAULT 'lived'"),
    ("identity_version", "ALTER TABLE claim ADD COLUMN identity_version "
                         "INTEGER NOT NULL DEFAULT 1"),
]


def migrate_claim_table(db: sqlite3.Connection) -> List[str]:
    """Bring an existing claim table up to the current schema.

    Returns the list of columns actually added, so a caller can log a
    migration rather than have it happen invisibly. Idempotent: a store
    already current gets no statements and an empty list.
    """
    cols = {r[1] for r in db.execute("PRAGMA table_info(claim)")}
    pending = [(n, ddl) for n, ddl in _ADDITIVE_COLUMNS if n not in cols]
    if not pending:
        return []

    # ALL OF IT OR NONE OF IT.
    #
    # This used to run each ALTER on its own. Two columns are pending on a
    # store from before the crypto work, and if the second failed — a disk
    # that filled between statements, a lock, a kill at the wrong instant —
    # the first stayed applied. The store then sits in a state no version of
    # this code has ever expected: half-migrated, opening cleanly, and
    # failing later somewhere that looks unrelated.
    #
    # A migration that half-happens is worse than one that fails, because a
    # failure is visible at the moment it happens and a half-migration is
    # discovered weeks later by a query that returns something odd.
    #
    # SQLite performs DDL inside a transaction, so this is genuinely
    # atomic — ADD COLUMN edits the table header, and a rollback puts the
    # header back. The existing claims' bytes are never touched either way,
    # which is why the hash chain still verifies across a migration.
    db.commit()                       # close any implicit transaction first
    added = []
    try:
        db.execute("BEGIN")
        for name, ddl in pending:
            db.execute(ddl)
            added.append(name)
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        # RAISED, NEVER SWALLOWED. A caller that believes the store is
        # current when it is not will write claims the schema cannot hold.
        raise
    return added
