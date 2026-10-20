"""Read Safari and Chromium visit history as OWNED evidence.

History databases are platform-owned mutable stores, not ordinary documents.
They therefore use a monotonically increasing row cursor rather than a whole
file digest: a new visit must not force every old visit through the binder
again.  The cursor advances only after the caller has appended and bound the
claims, making a crash replay safe.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple
from urllib.parse import quote

from tracemem.owned.reader import _owned_claim


CHROME_EPOCH_MS = 11_644_473_600_000
SAFARI_EPOCH_SECONDS = 978_307_200
HISTORY_VERSION = "owned-browser-history-v1"
MAX_VISITS_PER_PASS = 20_000


@dataclass
class HistoryResult:
    claims: List = field(default_factory=list)
    visits_seen: int = 0
    sources_read: int = 0
    sources_unreadable: int = 0
    # path, reader version, highest fully represented row id
    receipts: List[Tuple[str, str, int]] = field(default_factory=list)


def discover(root: str) -> List[Tuple[str, Path]]:
    """Return known browser databases inside a user-selected home corpus."""
    base = Path(root).expanduser().resolve()
    library = base / "Library"
    if base.name == "Library":
        library = base
    out: List[Tuple[str, Path]] = []
    safari = library / "Safari" / "History.db"
    if safari.is_file():
        out.append(("safari", safari))
    chromium_roots = (
        library / "Application Support" / "Google" / "Chrome",
        library / "Application Support" / "Chromium",
        library / "Application Support" / "BraveSoftware" / "Brave-Browser",
        library / "Application Support" / "Microsoft Edge",
    )
    for browser_root in chromium_roots:
        if not browser_root.is_dir():
            continue
        for profile in sorted(browser_root.iterdir()):
            if profile.name != "Default" and not profile.name.startswith("Profile "):
                continue
            history = profile / "History"
            if history.is_file():
                out.append(("chromium", history))
    return out


def _open_readonly(path: Path) -> sqlite3.Connection:
    # mode=ro is the invariant that matters.  The browser may have a WAL open;
    # SQLite's normal read-only connection sees a consistent snapshot without
    # copying, checkpointing, or changing the user's database.
    uri = "file:%s?mode=ro" % quote(str(path), safe="/")
    connection = sqlite3.connect(uri, uri=True, timeout=1.0)
    connection.execute("pragma query_only=on")
    return connection


def _rows(kind: str, db: sqlite3.Connection, cursor: int):
    if kind == "safari":
        query = (
            "select v.id,coalesce(v.title,''),i.url,v.visit_time "
            "from history_visits v join history_items i on i.id=v.history_item "
            "where v.id>? order by v.id limit ?")
        for row_id, title, url, stamp in db.execute(
                query, (int(cursor), MAX_VISITS_PER_PASS)):
            t_ms = int((float(stamp) + SAFARI_EPOCH_SECONDS) * 1000)
            yield int(row_id), str(title or ""), str(url or ""), t_ms
    else:
        query = (
            "select v.id,coalesce(u.title,''),u.url,v.visit_time "
            "from visits v join urls u on u.id=v.url "
            "where v.id>? order by v.id limit ?")
        for row_id, title, url, stamp in db.execute(
                query, (int(cursor), MAX_VISITS_PER_PASS)):
            t_ms = int(int(stamp) / 1000 - CHROME_EPOCH_MS)
            yield int(row_id), str(title or ""), str(url or ""), t_ms


def broad_history(root: str, device_id: str, resume_store=None) -> HistoryResult:
    result = HistoryResult()
    cursor_get = getattr(resume_store, "owned_stream_cursor", None)
    for kind, path in discover(root):
        cursor = int(cursor_get(str(path), HISTORY_VERSION)
                     if cursor_get is not None else 0)
        highest = cursor
        try:
            with closing(_open_readonly(path)) as db:
                for row_id, title, url, t_ms in _rows(kind, db, cursor):
                    result.visits_seen += 1
                    highest = max(highest, row_id)
                    if not url:
                        continue
                    text = (title.strip() + " — " + url
                            if title.strip() else url)[:1000]
                    logical_source = "%s#visit:%d" % (str(path), row_id)
                    result.claims.append(_owned_claim(
                        text, logical_source, device_id, t_ms,
                        perceiver="owned:browser_history"))
            result.sources_read += 1
            if highest > cursor:
                result.receipts.append((str(path), HISTORY_VERSION, highest))
        except (OSError, sqlite3.Error, ValueError):
            # A protected/locked/migrated store is an explicit incomplete
            # source, never an empty history.  The caller surfaces the count.
            result.sources_unreadable += 1
    return result
