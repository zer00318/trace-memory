"""Real SQLite oracles for the install-day browser-history reader."""
from __future__ import annotations

import sqlite3

from tracemem.domain import ProvenanceClass
from tracemem.owned.history import (
    CHROME_EPOCH_MS, HISTORY_VERSION, SAFARI_EPOCH_SECONDS, broad_history,
)
from tracemem.text.claim_log import ClaimLog


def _safari(root):
    path = root / "Library" / "Safari" / "History.db"
    path.parent.mkdir(parents=True)
    db = sqlite3.connect(path)
    db.executescript(
        "create table history_items(id integer primary key,url text);"
        "create table history_visits(id integer primary key,history_item integer,"
        "visit_time real,title text);")
    db.execute("insert into history_items values (1,'https://trace.example/a')")
    db.execute("insert into history_visits values (7,1,?,'TRACE article')",
               (1_700_000_000 - SAFARI_EPOCH_SECONDS,))
    db.commit()
    db.close()
    return path


def _chrome(root):
    path = (root / "Library" / "Application Support" / "Google" / "Chrome" /
            "Default" / "History")
    path.parent.mkdir(parents=True)
    db = sqlite3.connect(path)
    db.executescript(
        "create table urls(id integer primary key,url text,title text);"
        "create table visits(id integer primary key,url integer,visit_time integer);")
    db.execute("insert into urls values (2,'https://memory.example/b','Memory')")
    db.execute("insert into visits values (11,2,?)",
               ((1_700_000_100_000 + CHROME_EPOCH_MS) * 1000,))
    db.commit()
    db.close()
    return path


def test_reads_safari_and_chromium_as_timestamped_owned_claims(tmp_path):
    _safari(tmp_path)
    _chrome(tmp_path)

    got = broad_history(str(tmp_path), "mac-1")

    assert got.sources_read == 2
    assert got.visits_seen == 2
    assert {c.coord.t_ms for c in got.claims} == {
        1_700_000_000_000, 1_700_000_100_000}
    assert all(c.provenance.klass is ProvenanceClass.OWNED for c in got.claims)
    assert all(c.bidder == "owned:browser_history" for c in got.claims)


def test_browser_cursor_resumes_after_restart_and_advances_only_when_marked(tmp_path):
    history = _safari(tmp_path)
    raw = tmp_path / "raw.sqlite3"
    first_store = ClaimLog(str(raw))
    first = broad_history(str(tmp_path), "mac-1", first_store)
    assert len(first.claims) == 1
    # Merely reading is not completion. A simulated crash before the caller's
    # append/bind/mark sequence must replay the visit.
    assert len(broad_history(str(tmp_path), "mac-1", first_store).claims) == 1
    for source, version, cursor in first.receipts:
        first_store.finish_owned_stream(source, version, cursor)
    first_store._db.close()

    restarted = ClaimLog(str(raw))
    assert broad_history(str(tmp_path), "mac-1", restarted).claims == []
    db = sqlite3.connect(history)
    db.execute("insert into history_visits values (8,1,?,'Later visit')",
               (1_700_000_200 - SAFARI_EPOCH_SECONDS,))
    db.commit()
    db.close()
    later = broad_history(str(tmp_path), "mac-1", restarted)
    assert [c.text for c in later.claims] == [
        "Later visit — https://trace.example/a"]
    assert later.receipts[0][2] == 8


def test_cursor_ledger_contains_no_plaintext_browser_path(tmp_path):
    _safari(tmp_path)
    store = ClaimLog(str(tmp_path / "raw.sqlite3"))
    got = broad_history(str(tmp_path), "mac-1", store)
    for source, version, cursor in got.receipts:
        store.finish_owned_stream(source, version, cursor)
    rows = store._db.execute(
        "select source_key,reader_version,cursor from owned_stream_cursor"
    ).fetchall()
    assert len(rows) == 1
    assert "Safari" not in repr(rows)
    assert rows[0][1] == HISTORY_VERSION
