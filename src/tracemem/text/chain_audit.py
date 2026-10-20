"""OWNS: auditing the whole store's hash chain, a slice at a time.

Boot verifies a TAIL — the 500 rows a crash or bad shutdown would have
damaged. That leaves everything older unaudited for ever, and "everything
older" is where a memory's value actually lives: the claims from months ago
are the ones nobody would notice going wrong.

WHY NOT JUST WALK IT ALL NIGHTLY. On the founder's store that is six hundred
thousand rows, every one decrypted to re-hash. It is minutes of CPU and it
grows without bound, so it would either be skipped when the night is busy
(and then never run) or crowd out the thinking the night exists for.

SO IT WALKS ROUND-ROBIN, and the position is remembered. A slice of N rows a
night audits the whole store over a cycle whose length is honest arithmetic:
600k rows at 20k a night is a full audit every month. Nobody has to decide
how paranoid to be; they only have to decide how long a cycle they accept.

THE CURSOR PERSISTS OR THE AUDIT NEVER FINISHES. A position kept in memory
restarts at zero every time the daemon does, and a daemon that restarts more
often than the cycle length would re-audit the same first slice for ever
while claiming to sweep the store. It lives in the store beside the claims.
"""
from __future__ import annotations

from typing import Dict, Optional

# Rows per night. Chosen so a large store completes a cycle in weeks, not
# years, without the pass ever being the reason a night ran long.
SLICE_ROWS = 20_000
_CURSOR_KEY = "chain_audit_cursor"


def _table(db) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS audit_state "
               "(k TEXT PRIMARY KEY, v INTEGER NOT NULL)")


def read_cursor(claim_log) -> int:
    _table(claim_log._db)
    row = list(claim_log._db.execute(
        "SELECT v FROM audit_state WHERE k = ?", (_CURSOR_KEY,)))
    return int(row[0][0]) if row else 0


def write_cursor(claim_log, value: int) -> None:
    _table(claim_log._db)
    claim_log._db.execute(
        "INSERT INTO audit_state (k, v) VALUES (?, ?) "
        "ON CONFLICT(k) DO UPDATE SET v = excluded.v",
        (_CURSOR_KEY, int(value)))
    claim_log._db.commit()


def total_rows(claim_log) -> int:
    return int(list(claim_log._db.execute(
        "SELECT COUNT(*) FROM claim"))[0][0])


def audit_slice(claim_log, rows: int = SLICE_ROWS, stop=None) -> Dict[str, object]:
    """Verify the next `rows` claims. Returns what it found and where it got.

    The report distinguishes three things a caller must not conflate: the
    slice was clean, the slice was broken (and where), and a CYCLE finished —
    the last being the only moment anyone may say the whole store has been
    checked.
    """
    total = total_rows(claim_log)
    if total == 0:
        return {"ok": True, "checked": 0, "from": 0, "to": 0,
                "cycle_complete": False, "total": 0, "broken_at": None,
                "reason": None}

    # The cursor is the rowid of the last row verified (rows are never
    # deleted, so on every real store that is also the count of rows behind
    # it).  Past the end means the previous cycle finished: start over.
    start = read_cursor(claim_log)
    top = int(list(claim_log._db.execute(
        "SELECT COALESCE(MAX(rowid), 0) FROM claim"))[0][0])
    if start >= top:
        start = 0
    # Exactly the declared window is decrypted.  The verifier reads the
    # predecessor's stored link (without opening its content) so the first
    # row's chain edge remains checked.  The old implementation asked
    # verify_chain for `total - start` rows: at cursor zero a "20k slice" of
    # the founder's 634k-claim store was a full-store decrypt lasting minutes.
    report = claim_log.verify_chain_window(start, rows, stop=stop)
    end = int(report.get("last_rowid", start))

    wrapped = (bool(report.get("ok")) and not report.get("expired")
               and (bool(report.get("eof")) or end >= top))
    write_cursor(claim_log, 0 if wrapped else end)
    return {
        "ok": bool(report.get("ok")),
        "checked": int(report.get("checked", 0)),
        "from": start,
        "to": end,
        "total": total,
        # ONLY TRUE THE NIGHT THE LAST SLICE LANDS. Anything else would let a
        # partial sweep be reported as a whole-store guarantee, which is the
        # same lie `complete: False` exists to prevent at boot.
        "cycle_complete": bool(wrapped),
        "broken_at": report.get("broken_at"),
        "reason": report.get("reason"),
        "expired": bool(report.get("expired")),
    }


def nights_per_cycle(claim_log, rows: int = SLICE_ROWS) -> Optional[int]:
    """How long a full audit takes at this rate — the number a person needs
    to decide whether the cadence is paranoid enough."""
    total = total_rows(claim_log)
    if not total or rows <= 0:
        return None
    return max(1, (total + rows - 1) // rows)
