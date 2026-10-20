"""A migration that half-happens is worse than one that fails.

`Always running / Updates & migrations`, its oracle: v(n)→v(n+1) migrates,
and an injected failure rolls back intact.

Two columns are pending on a store written before the crypto work. Each ALTER
used to run on its own, so if the second failed — a disk that filled between
statements, a lock, a kill at the wrong instant — the first stayed applied.
The store then sits in a state no version of this code has ever expected:
half-migrated, opening cleanly, and failing later somewhere that looks
unrelated.

A failure is visible at the moment it happens. A half-migration is discovered
weeks later by a query that returns something odd.
"""
from __future__ import annotations

import sqlite3

import pytest

from tracemem.text.migrations import _ADDITIVE_COLUMNS, migrate_claim_table

OLD_SCHEMA = """
CREATE TABLE claim (
  id TEXT PRIMARY KEY, t_ms INTEGER NOT NULL, ciphertext BLOB NOT NULL
)
"""


def _old_store():
    db = sqlite3.connect(":memory:")
    db.execute(OLD_SCHEMA)
    db.execute("INSERT INTO claim (id, t_ms, ciphertext) VALUES (?, ?, ?)",
               ("a", 1, b"bytes"))
    db.commit()
    return db


def _columns(db):
    return {r[1] for r in db.execute("PRAGMA table_info(claim)")}


def test_an_old_store_migrates_forward():
    db = _old_store()
    added = migrate_claim_table(db)
    assert added == [n for n, _ in _ADDITIVE_COLUMNS]
    assert _columns(db) >= {n for n, _ in _ADDITIVE_COLUMNS}


def test_the_rows_survive_the_migration():
    """ADD COLUMN edits the table header; every stored claim's bytes are
    untouched, which is why the hash chain still verifies across one."""
    db = _old_store()
    migrate_claim_table(db)
    rows = list(db.execute("SELECT id, ciphertext FROM claim"))
    assert rows == [("a", b"bytes")]


def test_migrating_a_current_store_does_nothing():
    db = _old_store()
    migrate_claim_table(db)
    assert migrate_claim_table(db) == []


class _FailsOnSecondAlter:
    """A connection that dies part-way through, the way a full disk does."""

    def __init__(self, db):
        self._db = db
        self.alters = 0

    def execute(self, sql, *a, **k):
        if sql.strip().upper().startswith("ALTER"):
            self.alters += 1
            if self.alters == 2:
                raise sqlite3.OperationalError("disk full")
        return self._db.execute(sql, *a, **k)

    def __getattr__(self, name):
        return getattr(self._db, name)


def test_an_injected_failure_rolls_back_intact():
    """THE ORACLE. The first ALTER succeeds, the second is made to fail, and
    the store must end where it started rather than half-way."""
    db = _old_store()
    before = _columns(db)
    flaky = _FailsOnSecondAlter(db)

    with pytest.raises(sqlite3.OperationalError):
        migrate_claim_table(flaky)

    assert flaky.alters == 2, "the test did not reach the failure it injects"
    assert _columns(db) == before, \
        "the store is half-migrated: %r" % (_columns(db) - before)

    # And it is still usable — a rolled-back migration must leave a store you
    # can open and migrate again, not a wedged one.
    assert list(db.execute("SELECT id FROM claim")) == [("a",)]
    assert migrate_claim_table(db) == [n for n, _ in _ADDITIVE_COLUMNS]


def test_a_failure_is_raised_never_swallowed():
    """A caller that believes the store is current when it is not will write
    claims the schema cannot hold."""
    import inspect

    from tracemem.text import migrations

    src = inspect.getsource(migrations.migrate_claim_table)
    i = src.index("ROLLBACK")
    assert "raise" in src[i:i + 300]


def test_the_column_list_is_append_only():
    """A store in the field may be at any point in this list. Editing or
    removing an entry strands every store that stopped there."""
    import inspect

    from tracemem.text import migrations

    src = inspect.getsource(migrations)
    assert "Append only" in src
    for name, ddl in _ADDITIVE_COLUMNS:
        assert ddl.strip().upper().startswith("ALTER TABLE CLAIM ADD COLUMN")
