"""Explicit, backup-first migration of a populated semantic graph.

The graph is mutable, but ambiguity is still unacceptable: opening a
plaintext graph with a key never rewrites it. This operation snapshots the
whole SQLite database, encrypts a separate copy, reopens that copy through the
normal store API, compares every semantic surface, and only then swaps files.
The plaintext original and a verified backup both remain recoverable.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from tracemem.nodes.sqlite_graph_store import (
    _NODE_ADDITIVE, _PUBLIC_META, _SCHEMA, _GraphProtector, SqliteGraphStore,
)


class GraphMigrationRefused(RuntimeError):
    pass


class GraphMigrationFailed(RuntimeError):
    pass


@dataclass
class GraphMigrationReport:
    source: str
    backup: str
    nodes: int
    tombstones: int
    episodes: int
    pending: int
    ledger: int
    metadata: int
    cipher: str = "XChaCha20-Poly1305"
    swapped: bool = True


def _backup(source: Path, dest: Path) -> None:
    if dest.exists():
        raise GraphMigrationRefused(
            "verified backup already exists at %s; nothing changed" % dest)
    src = sqlite3.connect(str(source))
    out = sqlite3.connect(str(dest))
    try:
        src.backup(out)
        out.commit()
    finally:
        out.close()
        src.close()


def _count(db, table):
    return db.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]


def _transform(path: Path, key: bytes) -> dict:
    p = _GraphProtector(key)
    db = sqlite3.connect(str(path))
    try:
        # UPDATE alone is not erasure: old text can survive in freed SQLite
        # cells and WAL frames. Secure-delete plus VACUUM makes the artifact
        # byte scan meaningful instead of checking only live rows.
        db.execute("PRAGMA secure_delete=ON")
        db.executescript(_SCHEMA)
        for table, col, ddl in _NODE_ADDITIVE:
            have = {r[1] for r in db.execute("PRAGMA table_info(%s)" % table)}
            if col not in have:
                db.execute(ddl)
        db.commit()
        db.execute("BEGIN IMMEDIATE")

        for node_id, attrs in db.execute("SELECT id,attributes FROM node").fetchall():
            db.execute("UPDATE node SET attributes=? WHERE id=?", (
                p.seal("node.attributes", node_id, attrs), node_id))
        for node_id, attrs in db.execute(
                "SELECT id,attributes FROM tombstone").fetchall():
            db.execute("UPDATE tombstone SET attributes=? WHERE id=?", (
                p.seal("node.attributes", node_id, attrs), node_id))
        for episode_id, place in db.execute(
                "SELECT id,place_key FROM episode").fetchall():
            db.execute("UPDATE episode SET place_key=? WHERE id=?", (
                p.seal("episode.place", episode_id, place or ""), episode_id))

        pending = db.execute(
            "SELECT key,episode_id,surface,kind FROM pending_mention").fetchall()
        for raw_key, episode_id, surface, raw_kind in pending:
            key_digest = p.digest("pending.key", raw_key)
            kind_digest = p.digest("pending.kind", raw_kind)
            db.execute(
                "UPDATE pending_mention SET key=?,surface=?,kind=?,key_body=?,"
                "kind_body=? WHERE key=? AND episode_id=?",
                (key_digest, p.seal("pending.surface", key_digest, surface),
                 kind_digest, p.seal("pending.key", key_digest, raw_key),
                 p.seal("pending.kind", key_digest, raw_kind),
                 raw_key, episode_id))

        ledgers = db.execute("SELECT key,outcome FROM ledger").fetchall()
        for raw_key, outcome in ledgers:
            key_digest = p.digest("ledger.key", raw_key)
            db.execute(
                "UPDATE ledger SET key=?,outcome=?,key_body=? WHERE key=?",
                (key_digest, p.seal("ledger.outcome", key_digest, outcome),
                 p.seal("ledger.key", key_digest, raw_key), raw_key))

        mining = db.execute(
            "SELECT node_id,key FROM mining_ledger").fetchall()
        for node_id, raw_key in mining:
            key_digest = p.digest("mining.key", raw_key)
            db.execute(
                "UPDATE mining_ledger SET key=?,key_body=? "
                "WHERE node_id=? AND key=?",
                (key_digest, p.seal("mining.key", node_id + ":" + key_digest,
                                    raw_key), node_id, raw_key))

        metadata = db.execute("SELECT key,value FROM graph_meta").fetchall()
        for raw_key, value in metadata:
            key_digest = p.digest("meta.key", raw_key)
            db.execute(
                "UPDATE graph_meta SET key=?,value=?,key_body=? WHERE key=?",
                (key_digest, p.seal("meta.value", key_digest, value),
                 p.seal("meta.key", key_digest, raw_key), raw_key))
            if raw_key in _PUBLIC_META:
                db.execute(
                    "INSERT OR REPLACE INTO graph_public_meta VALUES (?,?)",
                    (raw_key, value))

        db.execute("DELETE FROM graph_security")
        db.execute("INSERT INTO graph_security VALUES (1,?,?)", (
            "XChaCha20-Poly1305",
            p.seal("security", "canary", "trace-graph-key-ok")))
        db.execute("COMMIT")
        db.execute("VACUUM")
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return {table: _count(db, table) for table in (
            "node", "tombstone", "episode", "pending_mention", "ledger",
            "graph_meta")}
    except Exception:
        try:
            db.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        db.close()


def migrate_graph(path: str, key: bytes, *,
                  daemon_running: Callable[[], bool] = lambda: False
                  ) -> GraphMigrationReport:
    source = Path(path)
    if not source.exists():
        raise GraphMigrationRefused("no semantic graph at %s" % source)
    if daemon_running():
        raise GraphMigrationRefused(
            "TRACE is running; stop it before migrating its semantic graph")
    if not key:
        raise GraphMigrationRefused("no graph key supplied")

    probe = sqlite3.connect(str(source))
    try:
        encrypted = probe.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='graph_security'").fetchone()
        if encrypted and probe.execute("SELECT COUNT(*) FROM graph_security").fetchone()[0]:
            raise GraphMigrationRefused("semantic graph is already encrypted")
    finally:
        probe.close()

    # Capture exact observable semantics before writing any other file.
    plain = SqliteGraphStore(str(source))
    nodes = plain.all_nodes()
    retired = plain.retired()
    episodes = plain.all_episode_records()
    ledger = plain.all_ledger()
    meta_rows = plain._db.execute("SELECT key,value FROM graph_meta").fetchall()
    pending_rows = plain._db.execute(
        "SELECT key FROM pending_mention GROUP BY key").fetchall()
    pending = {r[0]: plain.sightings(r[0]) for r in pending_rows}
    plain._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    plain.close()

    backup = source.with_suffix(source.suffix + ".encrypted-backup")
    _backup(source, backup)
    dest = source.with_suffix(source.suffix + ".encrypting")
    if dest.exists():
        raise GraphMigrationRefused(
            "unfinished migration file exists at %s; original untouched" % dest)
    _backup(source, dest)

    try:
        counts = _transform(dest, key)
        protected = SqliteGraphStore(str(dest), key=key)
        if protected.all_nodes() != nodes:
            raise GraphMigrationFailed("node round-trip mismatch")
        if protected.retired() != retired:
            raise GraphMigrationFailed("retired-belief round-trip mismatch")
        if protected.all_episode_records() != episodes:
            raise GraphMigrationFailed("episode round-trip mismatch")
        if protected.all_ledger() != ledger:
            raise GraphMigrationFailed("ledger round-trip mismatch")
        for meta_key, value in meta_rows:
            if protected.get_meta(meta_key) != value:
                raise GraphMigrationFailed("metadata round-trip mismatch")
        for pending_key, sightings in pending.items():
            if protected.sightings(pending_key) != sightings:
                raise GraphMigrationFailed("pending-evidence round-trip mismatch")
        protected.close()
        # The verified backup is protected too. Keeping a plaintext original
        # beside an encrypted live graph would make the live graph's security
        # claim technically true and the user's memory practically exposed.
        _transform(backup, key)
        SqliteGraphStore(str(backup), key=key).close()
    except Exception:
        # Leave the failed candidate for inspection; the source and verified
        # backup are untouched.
        raise

    aside = source.with_suffix(source.suffix + ".swapping-plaintext")
    if aside.exists():
        raise GraphMigrationRefused(
            "temporary original already exists at %s; refusing to overwrite" % aside)
    os.replace(str(source), str(aside))
    try:
        os.replace(str(dest), str(source))
    except Exception:
        os.replace(str(aside), str(source))
        raise
    # Fresh-handle proof over the file that actually landed.
    SqliteGraphStore(str(source), key=key).close()
    aside.unlink()

    return GraphMigrationReport(
        source=str(source), backup=str(backup),
        nodes=counts["node"], tombstones=counts["tombstone"],
        episodes=counts["episode"], pending=counts["pending_mention"],
        ledger=counts["ledger"], metadata=counts["graph_meta"])
