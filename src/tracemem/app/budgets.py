"""OWNS: persistent crash-safe ledgers (spec 08 §5, O4).

Every spend is its own committed sqlite row — a kill -9 mid-night loses at
most the in-flight call, never the history. Storage is reported, not
enforced: the budget guards tokens (the scarce thing that costs battery and
heat), not disk.
"""
from __future__ import annotations

import sqlite3

MS_DAY = 86_400_000


class TokenLedger:
    def __init__(self, db_path: str, cap: int) -> None:
        self._db = sqlite3.connect(db_path)
        self._db.execute("CREATE TABLE IF NOT EXISTS spend "
                         "(id INTEGER PRIMARY KEY, n INTEGER NOT NULL, "
                         "period TEXT NOT NULL)")
        self._db.commit()
        self.cap = cap
        self._period = "current"

    def spend(self, n: int) -> None:
        self._db.execute("INSERT INTO spend (n, period) VALUES (?, ?)",
                         (n, self._period))
        self._db.commit()                       # commit-per-spend: crash-safe by construction

    def spent(self) -> int:
        row = self._db.execute("SELECT COALESCE(SUM(n),0) FROM spend "
                               "WHERE period=?", (self._period,)).fetchone()
        return row[0]

    def may_spend(self, n: int) -> bool:
        return self.spent() + n <= self.cap

    def roll_period(self, new_period: str) -> None:
        """A new night starts a fresh window; history rows are kept for audit."""
        self._period = new_period


class InterruptionLedger:
    """Questions to the user are the scarcest resource in the system."""

    def __init__(self, db_path: str, cap_per_day: int,
                 stale_after_ms: int = 3 * MS_DAY) -> None:
        self._db = sqlite3.connect(db_path)
        self._db.execute("CREATE TABLE IF NOT EXISTS ask "
                         "(id INTEGER PRIMARY KEY, t_ms INTEGER NOT NULL)")
        self._db.execute("CREATE TABLE IF NOT EXISTS queued "
                         "(qid TEXT PRIMARY KEY, t_ms INTEGER NOT NULL)")
        self._db.commit()
        self.cap_per_day = cap_per_day
        self.stale_after_ms = stale_after_ms

    def asks_today(self, t_ms: int) -> int:
        day = t_ms // MS_DAY
        return self._db.execute(
            "SELECT COUNT(*) FROM ask WHERE t_ms/? = ?",
            (MS_DAY, day)).fetchone()[0]

    def may_ask(self, t_ms: int) -> bool:
        return self.asks_today(t_ms) < self.cap_per_day

    def record_ask(self, t_ms: int) -> None:
        self._db.execute("INSERT INTO ask (t_ms) VALUES (?)", (t_ms,))
        self._db.commit()

    def enqueue(self, question_id: str, t_ms: int) -> None:
        self._db.execute("INSERT OR IGNORE INTO queued VALUES (?, ?)",
                         (question_id, t_ms))
        self._db.commit()

    def drain_queue(self, t_ms: int):
        """Fresh queued questions, oldest first; stale ones expire silently —
        an answer to last week's ambiguity is noise, not signal."""
        self._db.execute("DELETE FROM queued WHERE ? - t_ms > ?",
                         (t_ms, self.stale_after_ms))
        self._db.commit()
        return [r[0] for r in self._db.execute(
            "SELECT qid FROM queued ORDER BY t_ms")]

    def dequeue(self, question_id: str) -> None:
        self._db.execute("DELETE FROM queued WHERE qid=?", (question_id,))
        self._db.commit()
