"""ORACLE for Durability policy + No-edit API surface (03_text).

The durability PROOF is not a simulation: a REAL child process appends
claims as fast as it can and is killed with SIGKILL mid-flight; this test
then reopens the store and counts what survived against what the child
reported having committed.
"""
import inspect
import os
import re
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from tracemem.text import claim_log as claim_log_module
from tracemem.text import durability
from tracemem.text.claim_log import ClaimLog

REPO = Path(__file__).resolve().parents[2]

APPENDER = textwrap.dedent("""
    import sys, time
    sys.path.insert(0, {src!r})
    from tracemem.text.claim_log import ClaimLog
    from tracemem.domain import (CaptureCoord, Claim, Confidence,
        CoordinateStack, Grade, Modality, Provenance, SpanCoord, WorldCoord)

    log = ClaimLog({db!r})
    i = 0
    while True:
        c = Claim(
            text="claim %d" % i,
            coord=CoordinateStack(
                world=WorldCoord(place_key="p"),
                capture=CaptureCoord(frame_id="f%d" % i, device_id="mac-1"),
                span=SpanCoord(bbox=(0.0, 0.0, 1.0, 1.0)), t_ms=i),
            provenance=Provenance(perceiver="test", modality=Modality.SCREEN),
            grade=Grade.VERBATIM, confidence=Confidence(1.0),
            structured={{}}, bidder="test", bid_id="b%d" % i,
            bid_predicted_value=0.0, actual_cost=0.0, scope="frame")
        log.append(c)
        i += 1
        # Report AFTER the commit returned: this is what the store promised.
        print(i, flush=True)
""")


def test_kill_9_during_heavy_append_loses_nothing(tmp_path):
    """THE PROOF: kill -9 during heavy append; loss measured ≤ the declared
    window (MAX_LOSS_ON_PROCESS_DEATH = 0).

    WHAT THIS DOES NOT PROVE, stated so nobody reads it as more: SIGKILL
    destroys process memory but leaves the OS page cache intact, so any
    policy that actually called write() would survive it. This test pins
    the PROCESS-death promise only. The chosen pragmas are pinned
    separately (test_the_policy_actually_took_effect_on_a_real_file), and
    the power-loss window remains honestly unmeasured — proving it needs
    real power cut from real hardware, which no test in this suite can do.
    """
    db = tmp_path / "kill9.sqlite3"
    script = tmp_path / "appender.py"
    script.write_text(APPENDER.format(src=str(REPO / "src"), db=str(db)))

    proc = subprocess.Popen([sys.executable, str(script)],
                            stdout=subprocess.PIPE, text=True)
    try:
        committed = 0
        deadline = time.time() + 3.0
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            committed = int(line.strip())
            if committed >= 200:            # enough writes to be "heavy"
                break
        assert committed >= 50, f"appender only committed {committed} claims"
        os.kill(proc.pid, signal.SIGKILL)   # no cleanup, no flush, no mercy
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()

    assert proc.returncode in (-signal.SIGKILL, -9), \
        f"child exited {proc.returncode} instead of being killed"

    survived = ClaimLog(str(db)).count()
    lost = committed - survived
    print(f"\nDURABILITY: committed>={committed} survived={survived} lost={lost}")
    assert lost <= durability.MAX_LOSS_ON_PROCESS_DEATH, (
        f"{lost} claims lost to a process kill; declared window is "
        f"{durability.MAX_LOSS_ON_PROCESS_DEATH}")


def test_the_policy_actually_took_effect_on_a_real_file(tmp_path):
    """SQLite silently ignores a journal_mode it cannot honour — so the
    policy is read back off the live connection, never assumed."""
    got = ClaimLog(str(tmp_path / "policy.sqlite3")).durability()
    assert got["journal_mode"] == "WAL"
    assert got["synchronous"] == 1                  # 1 = NORMAL


def test_the_power_loss_window_is_not_described_away():
    """The honesty clause: an unmeasurable hole must stay labeled, not be
    given a comfortable number."""
    assert isinstance(durability.MAX_LOSS_ON_POWER_LOSS, str)
    assert "unmeasured" in durability.MAX_LOSS_ON_POWER_LOSS


def test_a_store_reopens_and_reads_what_it_wrote(tmp_path):
    """WAL adds sidecar files; a reopen must still see the data."""
    from tests.helpers import make_claim
    path = str(tmp_path / "reopen.sqlite3")
    log = ClaimLog(path)
    log.append(make_claim("hello", t_ms=5))
    assert ClaimLog(path).count() == 1


# -- No-edit API surface (03_text) ----------------------------------------
MUTATING_VERBS = ("update", "delete", "remove", "edit", "set_", "modify",
                  "overwrite", "replace", "drop", "purge")


def test_the_claim_log_has_no_mutating_verb_on_its_public_surface():
    """PROOF: a test asserts the API surface. Raw immutability is not a
    convention here — the code physically lacks the path."""
    public = [n for n in dir(ClaimLog) if not n.startswith("_")]
    offenders = [n for n in public
                 if any(v in n.lower() for v in MUTATING_VERBS)]
    assert not offenders, f"mutating verbs on the raw store: {offenders}"


def test_no_sql_mutation_of_the_claim_table_anywhere_in_the_module():
    """PROOF: grep the module. A method named innocently could still run an
    UPDATE — so the SQL itself is checked, not just the names."""
    src = Path(inspect.getfile(claim_log_module)).read_text()
    sql = " ".join(re.findall(r'"""(.*?)"""|"(.*?)"|\'(.*?)\'', src, re.S)[0]) \
        if False else src
    for stmt in ("UPDATE claim", "DELETE FROM claim", "DROP TABLE claim",
                 "ALTER TABLE claim"):
        assert stmt.lower() not in sql.lower(), f"raw mutation present: {stmt}"


def test_migrations_are_additive_only():
    """The escape hatch is watched harder than the thing it escaped.

    Adding the `nonce` column needed an ALTER TABLE, which the guard above
    forbids inside claim_log.py — correctly, since a statement that can
    rewrite the claim table has no business in the module that owns it. It
    moved to text/migrations.py. That relocation is only honest if the new
    home is constrained MORE tightly than the old one, so: the migrations
    module may contain ADD COLUMN and nothing else that touches raw.

    ADD COLUMN is the one schema change SQLite makes without rewriting a
    single row, which is why the hash chain still verifies across it.
    """
    from tracemem.text import migrations as migrations_module
    src = Path(inspect.getfile(migrations_module)).read_text()

    forbidden = ("UPDATE claim", "DELETE FROM claim", "DROP TABLE claim",
                 "INSERT INTO claim", "ALTER TABLE claim RENAME")
    for stmt in forbidden:
        assert stmt.lower() not in src.lower(), \
            f"migrations must never rewrite raw: {stmt}"

    # every ALTER present must be an ADD COLUMN
    alters = re.findall(r"ALTER TABLE claim[^\"']*", src, re.I)
    assert alters, "the migration module has no ALTER at all — is it still used?"
    for a in alters:
        assert "add column" in a.lower(), f"non-additive migration: {a}"


def test_append_is_the_only_write_door():
    writes = [n for n in dir(ClaimLog)
              if not n.startswith("_") and n in ("append", "insert", "write",
                                                 "put", "save", "store")]
    assert writes == ["append"], f"more than one write door: {writes}"


def test_re_appending_the_same_claim_is_idempotent_not_an_update(tmp_path):
    """T2: the same claim twice must be one row — and must not have been
    'updated' into place, which is the same thing wearing a disguise."""
    from tests.helpers import make_claim
    log = ClaimLog(str(tmp_path / "idem.sqlite3"))
    c = make_claim("once", t_ms=1)
    log.append(c)
    log.append(c)
    assert log.count() == 1
