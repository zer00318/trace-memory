"""Auditing the whole store, a slice a night.

Boot verifies a TAIL — the rows a crash would have damaged. That leaves
everything older unaudited for ever, and everything older is where a memory's
value lives: claims from months ago are the ones nobody would notice going
wrong.

Walking all six hundred thousand rows nightly is minutes of CPU that grows
without bound, so it would be skipped whenever the night was busy and then
never run at all. Round-robin turns "how paranoid should this be" into "how
long a cycle do you accept", which is a question with an answer.
"""
from __future__ import annotations

from tracemem.domain import (CaptureCoord, CoordinateStack, SpanCoord,
                             WorldCoord)
from tracemem.extraction.claim_builder import build_claim
from tracemem.text.chain_audit import (audit_slice, nights_per_cycle,
                                       read_cursor, total_rows, write_cursor)
from tracemem.text.claim_log import ClaimLog


def _claim(i):
    coord = CoordinateStack(world=WorldCoord(place_key="p"),
                            capture=CaptureCoord(frame_id="f", device_id="d"),
                            span=SpanCoord(), t_ms=1000 + i)
    return build_claim({"text": "claim %d" % i}, coord, "ocr", bidder="b",
                       bid_id="i", bid_predicted_value=0.0, actual_cost=0.0,
                       scope="frame")


def _log(tmp_path, n=10):
    log = ClaimLog(str(tmp_path / "s.sqlite3"))
    for i in range(n):
        log.append(_claim(i))
    return log


def test_an_empty_store_audits_cleanly(tmp_path):
    log = ClaimLog(str(tmp_path / "s.sqlite3"))
    r = audit_slice(log)
    assert r["ok"] is True and r["checked"] == 0
    assert r["cycle_complete"] is False


def test_a_slice_walks_forward(tmp_path):
    log = _log(tmp_path, 10)
    first = audit_slice(log, rows=4)
    assert (first["from"], first["to"]) == (0, 4)
    second = audit_slice(log, rows=4)
    assert (second["from"], second["to"]) == (4, 8)


def test_a_cycle_completes_and_wraps(tmp_path):
    """`cycle_complete` is the only moment anyone may say the WHOLE store has
    been checked. A partial sweep reported as a whole-store guarantee is the
    same lie `complete: False` prevents at boot."""
    log = _log(tmp_path, 10)
    assert audit_slice(log, rows=4)["cycle_complete"] is False
    assert audit_slice(log, rows=4)["cycle_complete"] is False
    last = audit_slice(log, rows=4)
    assert last["cycle_complete"] is True
    # And it starts over rather than running off the end.
    assert audit_slice(log, rows=4)["from"] == 0


def test_the_cursor_survives_a_restart(tmp_path):
    """A position kept in memory restarts at zero every time the daemon does,
    and a daemon restarting more often than the cycle length would re-audit
    the same first slice for ever while claiming to sweep the store."""
    path = str(tmp_path / "s.sqlite3")
    log = ClaimLog(path)
    for i in range(10):
        log.append(_claim(i))
    audit_slice(log, rows=4)
    log._db.commit()

    reopened = ClaimLog(path)
    assert read_cursor(reopened) == 4
    assert audit_slice(reopened, rows=4)["from"] == 4


def test_planted_corruption_inside_the_slice_is_caught(tmp_path):
    log = _log(tmp_path, 10)
    victim = list(log._db.execute(
        "SELECT id FROM claim ORDER BY rowid LIMIT 1 OFFSET 6"))[0][0]
    log._db.execute("UPDATE claim SET ciphertext = ? WHERE id = ?",
                    (b"tampered", victim))
    log._db.commit()

    write_cursor(log, 5)
    r = audit_slice(log, rows=4)          # covers rows 5..9
    assert r["ok"] is False
    assert r["broken_at"] == victim


def test_corruption_outside_the_slice_is_not_this_slices_verdict(tmp_path):
    """Otherwise every slice for the rest of the cycle reports the same
    break, and nobody can tell whether anything ELSE is also wrong."""
    log = _log(tmp_path, 10)
    victim = list(log._db.execute(
        "SELECT id FROM claim ORDER BY rowid LIMIT 1 OFFSET 8"))[0][0]
    log._db.execute("UPDATE claim SET ciphertext = ? WHERE id = ?",
                    (b"tampered", victim))
    log._db.commit()

    write_cursor(log, 0)
    r = audit_slice(log, rows=3)          # covers rows 0..2 only
    assert r["ok"] is True
    assert r["broken_at"] is None


def test_a_deep_slice_decrypts_only_its_declared_rows(tmp_path, monkeypatch):
    """Production regression: OFFSET 40, LIMIT 3 must cost three decryptions,
    not every row from 40 to the end (and cursor zero must not mean all)."""
    log = _log(tmp_path, 50)
    opened = []
    original = log._open

    def counted(*args, **kwargs):
        opened.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(log, "_open", counted)
    write_cursor(log, 40)
    r = audit_slice(log, rows=3)

    assert r["ok"] is True and r["checked"] == 3
    assert len(opened) == 3


def test_an_audit_yields_at_its_deadline_and_resumes_from_that_row(tmp_path):
    log = _log(tmp_path, 20)
    polls = [0]

    def stop():
        polls[0] += 1
        return polls[0] > 4

    first = audit_slice(log, rows=20, stop=stop)
    assert first["ok"] is True and first["expired"] is True
    assert first["checked"] == 4 and read_cursor(log) == 4
    second = audit_slice(log, rows=20)
    assert second["from"] == 4
    assert second["cycle_complete"] is True


def test_it_says_how_long_a_full_audit_takes(tmp_path):
    """The number a person needs to decide whether the cadence is paranoid
    enough — not a setting they have to guess at."""
    log = _log(tmp_path, 10)
    assert total_rows(log) == 10
    assert nights_per_cycle(log, rows=4) == 3
    assert nights_per_cycle(log, rows=100) == 1


def test_an_empty_store_has_no_cycle_length(tmp_path):
    assert nights_per_cycle(ClaimLog(str(tmp_path / "s.sqlite3"))) is None


# -- the night runs it ----------------------------------------------------

def test_the_night_audits_a_slice_and_reports_it():
    """A check nobody can see the result of is a check nobody trusts."""
    from tracemem.app.night_watchman import NightWatchman

    w = NightWatchman.__new__(NightWatchman)
    w._log = lambda m: None
    w._brain = None
    assert w._audit_slice() is None, "no store must not raise"


def test_an_audit_failure_never_stops_the_night():
    """A memory that stops THINKING because its integrity check stumbled has
    traded the thing it is for a check on the thing it is."""
    import inspect

    from tracemem.app import night_watchman

    src = inspect.getsource(night_watchman)
    i = src.index("self.last_audit = self._audit_slice(")
    assert "except Exception" in src[i:i + 400]


def test_a_clean_cycle_is_announced_once_not_every_night():
    """"Every claim in the store has been verified" is a strong statement and
    it is only true the night the last slice lands."""
    import inspect

    from tracemem.app import night_watchman

    src = inspect.getsource(night_watchman.NightWatchman._audit_slice)
    assert 'cycle_complete' in src


def _steps_to_audit(log, cursor, rows=3):
    """SQLite VM steps spent reaching and verifying one window."""
    steps = [0]

    def tick():
        steps[0] += 1
        return 0

    write_cursor(log, cursor)
    log._db.set_progress_handler(tick, 100)
    try:
        r = audit_slice(log, rows=rows)
    finally:
        log._db.set_progress_handler(None, 100)
    assert r["ok"] is True and r["checked"] == rows
    return steps[0]


def test_reaching_a_deep_window_does_not_walk_the_rows_before_it(tmp_path):
    """WITNESSED 2026-10-14 on the live store: the audit reported `checked 0,
    from 320000, to 320000, expired` on every night slice, and sampling the
    daemon put three times more owner-thread time in the audit than in the
    night itself.  `LIMIT ? OFFSET 320000` on a rowid-ordered join steps
    through every earlier row (ciphertext pages included), twice, before the
    first row of the window; by then the deadline had passed, the first poll
    stopped it, and the cursor never moved.  The docstring promised a cost
    proportional to the window "never to the age of the store or the cursor
    position"; that has to be true of the query, not only of the decrypts.
    """
    log = _log(tmp_path, 600)
    shallow = _steps_to_audit(log, 10)
    deep = _steps_to_audit(log, 580)
    assert deep <= shallow * 2 + 20, (
        "window at row 580 cost %d steps, at row 10 cost %d" % (deep, shallow))


def test_a_window_ending_exactly_on_the_last_row_completes_the_cycle(tmp_path):
    log = _log(tmp_path, 8)
    assert audit_slice(log, rows=4)["cycle_complete"] is False
    assert audit_slice(log, rows=4)["cycle_complete"] is True
    assert audit_slice(log, rows=4)["from"] == 0
