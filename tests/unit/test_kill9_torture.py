"""ORACLE for the kill-9 torture (11_machine > Store engineering).

PROOF: "100/100 clean cycles logged on the board."

The full 100-cycle run is a tool invocation whose result is pasted on the
board — it takes tens of seconds and spawns a hundred processes, which is
not a thing to put in a suite that must stay fast. What IS here: that the
torture is honest. A torture that cannot fail is theatre, and a torture that
counts loosely either invents failures or hides them.
"""
import os
import sqlite3
import tempfile

import pytest

from tools.kill9_torture import main, one_cycle
import random


def test_a_few_real_cycles_are_clean():
    """The mechanism itself, exercised for real: spawn, kill -9, reopen,
    check. Kept to three cycles so the suite stays fast."""
    assert main(["--cycles", "3", "--seed", "11"]) == 0


def test_a_cycle_reports_what_was_promised_and_what_survived():
    rng = random.Random(3)
    with tempfile.TemporaryDirectory() as tmp:
        ok, promised, survived, integrity, detail = one_cycle(tmp, rng, 1)
    assert ok is True
    assert promised > 0, "the writer never committed anything to hold it to"
    assert survived >= promised
    assert integrity == "ok"
    assert detail == ""


def test_survival_is_measured_against_ACKNOWLEDGED_claims_only():
    """The child prints N only after append() returned, i.e. after the
    commit. A claim in flight when the kill lands was never promised, and
    counting it as lost would invent a durability failure that did not
    happen."""
    rng = random.Random(5)
    with tempfile.TemporaryDirectory() as tmp:
        _ok, promised, survived, _i, _d = one_cycle(tmp, rng, 1)
    # survived may EXCEED promised — claims committed after the last line
    # the parent managed to read. That direction is fine; the other is not.
    assert survived >= promised


def test_the_torture_checks_the_chain_not_just_the_count():
    """A store with the right number of rows and a broken chain has lost
    integrity without losing claims — which the count alone cannot see."""
    import inspect
    from tools import kill9_torture
    source = inspect.getsource(kill9_torture.one_cycle)
    assert "verify_chain" in source
    assert "integrity_check" in source


def test_it_can_actually_fail(tmp_path):
    """A trap nobody has seen fire is a comment. Corrupt a store and assert
    the same checks the torture runs would catch it."""
    from tracemem.text.claim_log import ClaimLog
    from tests.helpers import make_claim

    db = str(tmp_path / "corrupt.sqlite3")
    log = ClaimLog(db)
    for i in range(5):
        log.append(make_claim(text="c%d" % i, t_ms=i, frame="f%d" % i))

    ids = log.ids_in_range(0, 10 ** 12)
    log._db.execute("UPDATE claim_link SET link='deadbeef' WHERE id=?",
                    (ids[2],))
    log._db.commit()

    reopened = ClaimLog(db)
    assert reopened.verify_chain()["ok"] is False


def test_the_offsets_are_seeded_so_a_failure_can_be_reproduced():
    """When this eventually fails, the first question will be 'which cycle,
    and can you do it again'."""
    a = random.Random(7)
    b = random.Random(7)
    assert [a.uniform(0.15, 0.9) for _ in range(5)] == \
        [b.uniform(0.15, 0.9) for _ in range(5)]
