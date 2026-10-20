"""OWNS: the durable node/edge/ledger store (WO-080, map: Nodes / Real store).

Implements MemoryGraphStore's exact interface on SQLite. Every heart
algorithm is written against the protocol, so this swap must not change one
observable behaviour — `tests/unit/test_graph_store_conformance.py` runs the
same assertions against both and is the thing that makes that claim
checkable rather than hopeful.

THE DECAY FUNCTION IS REGISTERED, NOT REIMPLEMENTED. `nodes_with_activation_ge`
needs the activation curve inside a query. The obvious move is to write the
exponential in SQL; the correct move is to register
`domain.activation.activation_now` as a SQLite function and call THAT. Two
implementations of the same curve would agree on the day they were written
and drift the first time the half-life is tuned — and the drift would be
silent, because both would keep returning plausible numbers. There is one
curve, in one file, and SQL borrows it.

The pre-filter is the part that keeps this honest AND fast: decay never
increases activation, so `stored >= floor` is a sound necessary condition
and can ride an index. The exact function then runs over that candidate set
only. A wrong pre-filter here would silently drop nodes from recall, which
is the invisible-failure shape this codebase cares most about, so
`test_the_prefilter_never_hides_a_qualifying_node` exists specifically to
attack it.

DURABILITY: the same policy as the claim log (text/durability.py) — WAL +
synchronous=NORMAL. One place decides that for the whole product; a second
store quietly choosing different pragmas is how two halves of one machine
end up with two different crash stories.
"""
from __future__ import annotations

import json
import base64
import hashlib
import hmac
import sqlite3
from types import MappingProxyType
from typing import Dict, List, Mapping, Optional, Tuple

from tracemem.domain import Edge, EdgeType, Episode, Node
from tracemem.domain.time import TimeRange
from tracemem.domain.activation import HALF_LIFE_MS, activation_now
from tracemem.domain.confidence import Confidence
from tracemem.domain.grade import Grade
from tracemem.domain.node import Attribute, NodeKind
from tracemem.nodes.graph_store import pair_key  # noqa: F401  (re-export)
from tracemem.text import durability
from tracemem.container.crypto import AuthFailure, SodiumCrypto


class GraphKeyRequired(RuntimeError):
    """An encrypted graph was opened without its per-user key."""


class GraphMigrationRequired(RuntimeError):
    """A populated plaintext graph must be migrated explicitly."""


class _GraphProtector:
    """Randomized AEAD payloads plus keyed digests for equality lookups.

    SQLite still indexes opaque digests, never semantic strings. The digest
    key and encryption key are independently derived from the graph purpose
    key, so an index disclosure does not expose the AEAD key.
    """

    PREFIX = "g1:"

    def __init__(self, key: bytes) -> None:
        from tracemem.container.crypto import hkdf
        self._aead = SodiumCrypto(hkdf(key, b"graph-payload"))
        self._index = hkdf(key, b"graph-index")

    @staticmethod
    def _aad(scope: str, row: str) -> bytes:
        return ("trace-graph-v1\0%s\0%s" % (scope, row)).encode("utf-8")

    def seal(self, scope: str, row: str, value) -> str:
        raw = str(value).encode("utf-8")
        nonce, ciphertext = self._aead.encrypt(self._aad(scope, row), raw)
        return self.PREFIX + base64.b64encode(nonce + ciphertext).decode("ascii")

    def open(self, scope: str, row: str, value) -> str:
        text = str(value)
        if not text.startswith(self.PREFIX):
            raise GraphKeyRequired(
                "encrypted graph contains an unsealed semantic field")
        blob = base64.b64decode(text[len(self.PREFIX):])
        n = self._aead.NONCE_LEN
        return self._aead.decrypt(
            self._aad(scope, row), blob[:n], blob[n:]).decode("utf-8")

    def digest(self, scope: str, value) -> str:
        raw = (scope + "\0" + str(value)).encode("utf-8")
        return hmac.new(self._index, raw, hashlib.sha256).hexdigest()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS node (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  activation REAL NOT NULL,
  last_active_t_ms INTEGER NOT NULL,
  salience_ema REAL NOT NULL,
  derivation_depth INTEGER NOT NULL,
  parent_ids TEXT NOT NULL,
  attributes TEXT NOT NULL,
  builder_version TEXT NOT NULL DEFAULT '');

-- The pre-filter's index (see module docstring): decay only ever reduces
-- activation, so stored activation bounds the decayed value from above.
CREATE INDEX IF NOT EXISTS ix_node_activation ON node(activation);
CREATE INDEX IF NOT EXISTS ix_node_depth ON node(derivation_depth);

CREATE TABLE IF NOT EXISTS edge (
  src TEXT NOT NULL, dst TEXT NOT NULL, type TEXT NOT NULL,
  weight REAL NOT NULL, evidence_count INTEGER NOT NULL,
  last_reinforced_t_ms INTEGER NOT NULL,
  PRIMARY KEY (src, dst, type));
CREATE INDEX IF NOT EXISTS ix_edge_src ON edge(src);
CREATE INDEX IF NOT EXISTS ix_edge_dst ON edge(dst);

CREATE TABLE IF NOT EXISTS ledger (
  key TEXT PRIMARY KEY, outcome TEXT NOT NULL, fingerprint TEXT NOT NULL,
  key_body TEXT NOT NULL DEFAULT '');

-- The graveyard of retired beliefs (the moat: decaying cited history). Same
-- shape as node + when it died. Additive via IF NOT EXISTS, so an existing
-- store gains it on open with no migration.
CREATE TABLE IF NOT EXISTS tombstone (
  id TEXT PRIMARY KEY, retired_t_ms INTEGER NOT NULL,
  kind TEXT NOT NULL, activation REAL NOT NULL, last_active_t_ms INTEGER NOT NULL,
  salience_ema REAL NOT NULL, derivation_depth INTEGER NOT NULL,
  parent_ids TEXT NOT NULL, attributes TEXT NOT NULL,
  builder_version TEXT NOT NULL DEFAULT '');

-- THE PROCESSED-WORK LEDGERS (2026-07-29, priority P1). See the long note in
-- nodes/graph_store.py: on an EPHEMERAL graph these could live in a dict,
-- because the graph they guarded died with them. On a durable graph they must
-- be durable too, or every boot re-runs upkeep over the whole store and
-- `Edge.reinforce` — which is not idempotent — compounds co-occurrence weight
-- and evidence_count on every restart. A graph that grows more confident the
-- more often you reboot, silently, is exactly the failure class this codebase
-- ranks as most dangerous.
CREATE TABLE IF NOT EXISTS upkeep_seen (
  episode_id TEXT NOT NULL, claim_id TEXT NOT NULL,
  PRIMARY KEY (episode_id, claim_id));

-- Per-claim completion receipt.  Episode membership says where a claim
-- belongs; this says the entire bind/upkeep transaction returned.  Keeping
-- the two facts separate closes the crash window between persisting an
-- episode and deriving its graph consequences.
CREATE TABLE IF NOT EXISTS binding_receipt (
  claim_id TEXT PRIMARY KEY);

CREATE TABLE IF NOT EXISTS mining_ledger (
  node_id TEXT NOT NULL, key TEXT NOT NULL, fingerprint TEXT NOT NULL,
  key_body TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (node_id, key));

-- THE CORROBORATION WAITING ROOM (2026-07-29, N2). Mentions seen in only one
-- episode wait here instead of becoming durable nodes. PRIMARY KEY (key,
-- episode_id) is the load-bearing part: upkeep is delta-driven and replayable,
-- and corroboration that a replay can manufacture is not corroboration.
CREATE TABLE IF NOT EXISTS pending_mention (
  key TEXT NOT NULL, episode_id TEXT NOT NULL, claim_id TEXT NOT NULL,
  t_ms INTEGER NOT NULL, grade INTEGER NOT NULL, confidence REAL NOT NULL,
  surface TEXT NOT NULL, kind TEXT NOT NULL,
  key_body TEXT NOT NULL DEFAULT '', kind_body TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (key, episode_id));
CREATE INDEX IF NOT EXISTS ix_pending_key ON pending_mention(key);

-- THE BIND WATERMARK, and anything else the graph must remember ABOUT
-- ITSELF. Boot used to re-bind every claim on disk, which is O(store) on a
-- store that grows forever: 471,503 claims took roughly two hours, during
-- which the supervision thread is busy and THE EYE CANNOT WRITE. A memory
-- whose startup cost rises with its own size eventually cannot start.
CREATE TABLE IF NOT EXISTS graph_meta (
  key TEXT PRIMARY KEY, value TEXT NOT NULL,
  key_body TEXT NOT NULL DEFAULT '');

-- Public, non-semantic health facts for a cockpit that deliberately has no
-- decryption key. Only this fixed allowlist is ever copied here.
CREATE TABLE IF NOT EXISTS graph_public_meta (
  key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- Readable without a key so tooling can distinguish protected, plaintext and
-- wrong-key states. The canary is AEAD ciphertext, never key material.
CREATE TABLE IF NOT EXISTS graph_security (
  version INTEGER PRIMARY KEY, cipher TEXT NOT NULL, canary TEXT NOT NULL);

-- Durable Binder projection.  Graph attributes cite episode IDs; those IDs
-- must still resolve after the Hub process dies.
CREATE TABLE IF NOT EXISTS boundary (
  t_ms INTEGER NOT NULL, kind TEXT NOT NULL,
  PRIMARY KEY (t_ms, kind));
CREATE INDEX IF NOT EXISTS ix_boundary_time ON boundary(t_ms);

CREATE TABLE IF NOT EXISTS episode (
  id TEXT PRIMARY KEY, place_key TEXT, epoch_index INTEGER,
  start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
  boundary_version INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_episode_epoch ON episode(place_key, epoch_index);
CREATE INDEX IF NOT EXISTS ix_episode_time ON episode(start_ms, end_ms);

CREATE TABLE IF NOT EXISTS episode_claim (
  episode_id TEXT NOT NULL, claim_id TEXT NOT NULL,
  PRIMARY KEY (episode_id, claim_id));
CREATE INDEX IF NOT EXISTS ix_episode_claim_claim ON episode_claim(claim_id);

-- Versioned late-boundary reconciliation.  Old episodes and memberships stay
-- intact for historical citation; retirement and successor rows say which
-- current episode(s) now own their claims.
CREATE TABLE IF NOT EXISTS episode_retired (
  episode_id TEXT PRIMARY KEY, retired_version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS episode_successor (
  old_episode_id TEXT NOT NULL, new_episode_id TEXT NOT NULL,
  boundary_version INTEGER NOT NULL,
  PRIMARY KEY (old_episode_id, new_episode_id, boundary_version));
CREATE INDEX IF NOT EXISTS ix_episode_successor_old
  ON episode_successor(old_episode_id);
CREATE TABLE IF NOT EXISTS boundary_resolution (
  t_ms INTEGER NOT NULL, kind TEXT NOT NULL, boundary_version INTEGER NOT NULL,
  PRIMARY KEY (t_ms, kind));
"""

# The node column list, in one place so the eight read/write sites can never
# drift out of positional agreement (the fragility a new column invites). Any
# SELECT feeding _node_from_row uses THIS order; builder_version is last.
_NODE_COLS = ("id, kind, activation, last_active_t_ms, salience_ema, "
              "derivation_depth, parent_ids, attributes, builder_version")

# Additive node/tombstone columns for a store written before they existed
# (mirrors text/migrations.py — ADD COLUMN edits only the header, so every
# stored row and any hash-independent invariant is untouched).
_NODE_ADDITIVE = [
    ("node", "builder_version",
     "ALTER TABLE node ADD COLUMN builder_version TEXT NOT NULL DEFAULT ''"),
    ("tombstone", "builder_version",
     "ALTER TABLE tombstone ADD COLUMN builder_version TEXT NOT NULL DEFAULT ''"),
    ("ledger", "key_body",
     "ALTER TABLE ledger ADD COLUMN key_body TEXT NOT NULL DEFAULT ''"),
    ("mining_ledger", "key_body",
     "ALTER TABLE mining_ledger ADD COLUMN key_body TEXT NOT NULL DEFAULT ''"),
    ("pending_mention", "key_body",
     "ALTER TABLE pending_mention ADD COLUMN key_body TEXT NOT NULL DEFAULT ''"),
    ("pending_mention", "kind_body",
     "ALTER TABLE pending_mention ADD COLUMN kind_body TEXT NOT NULL DEFAULT ''"),
    ("graph_meta", "key_body",
     "ALTER TABLE graph_meta ADD COLUMN key_body TEXT NOT NULL DEFAULT ''"),
]

_PUBLIC_META = frozenset(("bind_watermark_seq", "boundary_version",
                          "binding_receipt_migrated", "clock_model",
                          "night_hunger_grammar", "night_hunger_pending",
                          "night_hunger_unresolved", "night_hunger_examined"))


def _attrs_to_json(attributes) -> str:
    return json.dumps([{
        "key": a.key, "value": a.value, "grade": int(a.grade),
        "confidence": a.confidence.value,
        "source_episode_ids": list(a.source_episode_ids), "t_ms": a.t_ms,
        "first_t_ms": a.first_t_ms,
    } for a in attributes])


def _attrs_from_json(text: str) -> List[Attribute]:
    return [Attribute(key=d["key"], value=d["value"], grade=Grade(d["grade"]),
                      confidence=Confidence(d["confidence"]),
                      source_episode_ids=tuple(d["source_episode_ids"]),
                      t_ms=d["t_ms"],
                      # ATTRIBUTES WRITTEN BEFORE THIS FIELD EXISTED get
                      # first == last, which is exactly what was known about
                      # them: one sighting time and no evidence about any
                      # other. Defaulting to 0 would say "unknown"; defaulting
                      # to epoch would claim it had been true for ever.
                      first_t_ms=d.get("first_t_ms", d["t_ms"]))
            for d in json.loads(text)]


class SqliteGraphStore:
    """Durable GraphStore. `":memory:"` gives an ephemeral one for tests."""

    def __init__(self, db_path: str = ":memory:",
                 half_life_ms: int = HALF_LIFE_MS,
                 key: Optional[bytes] = None) -> None:
        self._db = sqlite3.connect(db_path)
        self._half_life_ms = half_life_ms
        if db_path != ":memory:":
            durability.apply(self._db)
        self._db.executescript(_SCHEMA)
        for table, col, ddl in _NODE_ADDITIVE:       # additive-only, header edit
            have = {r[1] for r in self._db.execute("PRAGMA table_info(%s)" % table)}
            if col not in have:
                self._db.execute(ddl)

        security = self._db.execute(
            "SELECT cipher,canary FROM graph_security WHERE version=1"
        ).fetchone()
        self._protector = _GraphProtector(key) if key is not None else None
        if security is not None:
            if self._protector is None:
                self._db.close()
                raise GraphKeyRequired(
                    "semantic graph is encrypted; supply the per-user graph key")
            if security[0] != "XChaCha20-Poly1305":
                self._db.close()
                raise GraphKeyRequired("unsupported graph cipher %r" % security[0])
            # Fails authenticated, before any graph value is trusted.
            try:
                canary = self._protector.open("security", "canary", security[1])
            except (AuthFailure, ValueError, UnicodeError) as exc:
                self._db.close()
                raise GraphKeyRequired(
                    "wrong semantic graph key or damaged key canary") from exc
            if canary != "trace-graph-key-ok":
                self._db.close()
                raise GraphKeyRequired("semantic graph key canary mismatch")
        elif self._protector is not None:
            semantic_rows = sum(self._db.execute(
                "SELECT COUNT(*) FROM %s" % table).fetchone()[0]
                for table in ("node", "tombstone", "episode", "pending_mention",
                              "ledger", "graph_meta"))
            if semantic_rows:
                self._db.close()
                raise GraphMigrationRequired(
                    "populated semantic graph is plaintext; run the explicit "
                    "graph encryption migration first")
            self._db.execute(
                "INSERT INTO graph_security VALUES (1,?,?)",
                ("XChaCha20-Poly1305", self._protector.seal(
                    "security", "canary", "trace-graph-key-ok")))
        # `upkeep_seen` already persisted historic episode membership before
        # the Binder tables existed.  Copy those references without touching
        # raw or pretending the missing historic ranges are known.
        self._db.execute(
            "INSERT OR IGNORE INTO episode_claim (episode_id, claim_id) "
            "SELECT episode_id, claim_id FROM upkeep_seen")
        # upkeep_seen is the strongest receipt older graphs possess.  Adopt
        # it exactly ONCE, and record that historical inference.  Repeating
        # this copy on every open would turn a crash between upkeep's early
        # ledger write and completion into a false completion receipt.
        migrated = self.get_meta("binding_receipt_migrated")
        if migrated is None:
            self._db.execute(
                "INSERT OR IGNORE INTO binding_receipt (claim_id) "
                "SELECT DISTINCT claim_id FROM upkeep_seen")
            self._write_meta("binding_receipt_migrated", "historic-upkeep-seen")
        # THE point of this class's activation query: one curve, borrowed.
        self._db.create_function(
            "activation_now", 3,
            lambda stored, last, now: activation_now(stored, last, now,
                                                     half_life_ms))
        self._db.commit()

    @property
    def encrypted(self) -> bool:
        return self._protector is not None

    def _index(self, scope: str, value) -> str:
        return (self._protector.digest(scope, value)
                if self._protector is not None else str(value))

    def _seal(self, scope: str, row: str, value) -> str:
        return (self._protector.seal(scope, row, value)
                if self._protector is not None else str(value))

    def _open(self, scope: str, row: str, value) -> str:
        return (self._protector.open(scope, row, value)
                if self._protector is not None else str(value))

    def _body(self, scope: str, row: str, value) -> str:
        return (self._protector.seal(scope, row, value)
                if self._protector is not None else "")

    def _read_body(self, scope: str, row: str, body, fallback) -> str:
        return (self._protector.open(scope, row, body)
                if self._protector is not None else str(fallback))

    def _write_meta(self, key: str, value) -> None:
        stored = self._index("meta.key", key)
        self._db.execute(
            "INSERT OR REPLACE INTO graph_meta (key,value,key_body) VALUES (?,?,?)",
            (stored, self._seal("meta.value", stored, value),
             self._body("meta.key", stored, key)))
        if key in _PUBLIC_META:
            self._db.execute(
                "INSERT OR REPLACE INTO graph_public_meta VALUES (?,?)",
                (key, str(value)))

    # -- durable Binder projection ---------------------------------------
    def add_boundary(self, t_ms: int, kind: str = "scene") -> None:
        self._db.execute("INSERT OR IGNORE INTO boundary VALUES (?,?)",
                         (int(t_ms), str(kind)))
        self._db.commit()

    def load_boundaries(self, kind: Optional[str] = None) -> List[int]:
        if kind is None:
            rows = self._db.execute(
                "SELECT DISTINCT t_ms FROM boundary ORDER BY t_ms")
        else:
            rows = self._db.execute(
                "SELECT t_ms FROM boundary WHERE kind=? ORDER BY t_ms", (kind,))
        return [int(r[0]) for r in rows]

    def persist_episode(self, epoch_index: int, episode: Episode,
                        claim_ids=(), boundary_version: int = 0) -> None:
        self._db.execute(
            "INSERT INTO episode (id, place_key, epoch_index, start_ms, end_ms, "
            "boundary_version) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "start_ms=MIN(start_ms, excluded.start_ms), "
            "end_ms=MAX(end_ms, excluded.end_ms)",
            (episode.id, self._seal("episode.place", episode.id,
                                    episode.place_key or ""), int(epoch_index),
             int(episode.t_range.start_ms), int(episode.t_range.end_ms),
             int(boundary_version)))
        self._db.executemany(
            "INSERT OR IGNORE INTO episode_claim VALUES (?,?)",
            [(episode.id, str(cid)) for cid in claim_ids])
        self._db.commit()

    def load_episodes(self) -> Dict[Tuple[Optional[str], int], Episode]:
        out = {}
        rows = self._db.execute(
            "SELECT e.id, e.place_key, e.epoch_index, e.start_ms, e.end_ms "
            "FROM episode e LEFT JOIN episode_retired r ON r.episode_id=e.id "
            "WHERE e.epoch_index IS NOT NULL AND r.episode_id IS NULL "
            "ORDER BY e.start_ms, e.id")
        for eid, place, idx, start, end in rows:
            place = self._open("episode.place", eid, place) or None
            out[(place, int(idx))] = Episode(
                id=eid, place_key=place, t_range=TimeRange(int(start), int(end)),
                claim_ids=())
        return out

    def episode_ids(self) -> List[str]:
        rows = self._db.execute(
            "SELECT id FROM episode UNION SELECT DISTINCT episode_id "
            "FROM episode_claim ORDER BY 1")
        return [r[0] for r in rows]

    def membership_version(self):
        """An O(1) token that changes whenever episode membership changes.

        Both tables are insert-only (no code path deletes from either), so
        their highest rowids move exactly when a claim joins an episode or a
        reconciliation redirects one.  Mining keys its evidence memo on it.
        """
        row = self._db.execute(
            "SELECT (SELECT max(rowid) FROM episode_claim), "
            "(SELECT max(rowid) FROM episode_successor)").fetchone()
        return (row[0] or 0, row[1] or 0)

    def claim_ids_for_episode(self, episode_id: str) -> List[str]:
        rows = self._db.execute(
            "SELECT claim_id FROM episode_claim WHERE episode_id=? "
            "ORDER BY claim_id", (episode_id,))
        return [r[0] for r in rows]

    def evidence_claim_ids(self, episode_ids, limit: int = 41) -> List[str]:
        """Return at most ``limit`` raw ids without walking full membership.

        A common app node can cite an episode containing hundreds of
        thousands of claims.  The graph face displays forty; returning every
        id first made that harmless drill pin the daemon's only owner thread
        at 100% CPU for minutes while it decrypted evidence it would throw
        away.  SQLite applies LIMIT before Python sees the rows.
        """
        cap = max(0, int(limit))
        ids = sorted({str(eid) for eid in episode_ids})
        if not cap or not ids:
            return []
        out, seen = [], set()
        # Stay below SQLite's conservative host-parameter ceiling.  Each
        # chunk is itself bounded, so even a node spanning years of episodes
        # cannot turn this into an unbounded Python result.
        for offset in range(0, len(ids), 800):
            chunk = ids[offset:offset + 800]
            marks = ",".join("?" for _ in chunk)
            rows = self._db.execute(
                "SELECT claim_id FROM episode_claim WHERE episode_id IN (%s) "
                "LIMIT ?" % marks, tuple(chunk) + (cap,))
            for (claim_id,) in rows:
                if claim_id in seen:
                    continue
                seen.add(claim_id)
                out.append(claim_id)
                if len(out) >= cap:
                    return out
        return out

    def boundary_records(self):
        return [(int(r[0]), r[1]) for r in self._db.execute(
            "SELECT t_ms,kind FROM boundary ORDER BY t_ms,kind")]

    def all_episode_records(self):
        rows = self._db.execute(
            "SELECT e.id,e.place_key,e.epoch_index,e.start_ms,e.end_ms,"
            "e.boundary_version,r.retired_version FROM episode e "
            "LEFT JOIN episode_retired r ON r.episode_id=e.id "
            "ORDER BY e.start_ms,e.id")
        return [(int(idx), Episode(id=eid, place_key=(
                                      self._open("episode.place", eid, place) or None),
                                  t_range=TimeRange(int(start), int(end)),
                                  claim_ids=()), int(version),
                 int(retired) if retired is not None else None,
                 self.claim_ids_for_episode(eid))
                for eid, place, idx, start, end, version, retired in rows
                if idx is not None]

    def episode_successor_records(self):
        return [(r[0], r[1], int(r[2])) for r in self._db.execute(
            "SELECT old_episode_id,new_episode_id,boundary_version "
            "FROM episode_successor ORDER BY boundary_version,old_episode_id,new_episode_id")]

    def boundary_resolution_records(self):
        return [(int(r[0]), r[1], int(r[2])) for r in self._db.execute(
            "SELECT t_ms,kind,boundary_version FROM boundary_resolution "
            "ORDER BY boundary_version,t_ms")]

    def unresolved_boundaries(self) -> List[int]:
        rows = self._db.execute(
            "SELECT b.t_ms FROM boundary b LEFT JOIN boundary_resolution r "
            "ON r.t_ms=b.t_ms AND r.kind=b.kind "
            "WHERE b.kind='late_scene_pending' AND r.t_ms IS NULL "
            "ORDER BY b.t_ms")
        return [int(r[0]) for r in rows]

    def boundary_version(self) -> int:
        try:
            return int(self.get_meta("boundary_version", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def resolve_episode_ids(self, episode_id: str) -> List[str]:
        """Current successor leaves for a historical episode citation."""
        out, frontier, seen = [], [str(episode_id)], set()
        while frontier:
            current = frontier.pop(0)
            if current in seen:
                continue
            seen.add(current)
            successors = [r[0] for r in self._db.execute(
                "SELECT new_episode_id FROM episode_successor "
                "WHERE old_episode_id=? ORDER BY boundary_version, new_episode_id",
                (current,))]
            if successors:
                frontier.extend(successors)
            else:
                out.append(current)
        return list(dict.fromkeys(out))

    def commit_boundary_reconciliation(self, version: int, episodes,
                                       resolved_boundaries, successors) -> None:
        """Atomically install one new projection while retaining the old."""
        old_active = [r[0] for r in self._db.execute(
            "SELECT e.id FROM episode e LEFT JOIN episode_retired r "
            "ON r.episode_id=e.id WHERE r.episode_id IS NULL")]
        self._db.commit()
        try:
            self._db.execute("BEGIN IMMEDIATE")
            for (place, idx), ep in episodes.items():
                self._db.execute(
                    "INSERT OR IGNORE INTO episode "
                    "(id,place_key,epoch_index,start_ms,end_ms,boundary_version) "
                    "VALUES (?,?,?,?,?,?)",
                    (ep.id, self._seal("episode.place", ep.id, place or ""),
                     int(idx), int(ep.t_range.start_ms),
                     int(ep.t_range.end_ms), int(version)))
                self._db.executemany(
                    "INSERT OR IGNORE INTO episode_claim VALUES (?,?)",
                    [(ep.id, str(cid)) for cid in ep.claim_ids])
                # Reconciliation changes grouping, not whether raw completed
                # upkeep. Seed only claims with a genuine completion receipt.
                self._db.execute(
                    "INSERT OR IGNORE INTO upkeep_seen (episode_id,claim_id) "
                    "SELECT ?, ec.claim_id FROM episode_claim ec "
                    "JOIN binding_receipt br ON br.claim_id=ec.claim_id "
                    "WHERE ec.episode_id=?", (ep.id, ep.id))
            self._db.executemany(
                "INSERT OR IGNORE INTO episode_retired VALUES (?,?)",
                [(eid, int(version)) for eid in old_active])
            self._db.executemany(
                "INSERT OR IGNORE INTO episode_successor VALUES (?,?,?)",
                [(old, new, int(version)) for old, news in successors.items()
                 for new in news])
            for t_ms in resolved_boundaries:
                self._db.execute("INSERT OR IGNORE INTO boundary VALUES (?,?)",
                                 (int(t_ms), "scene"))
                self._db.execute(
                    "INSERT OR IGNORE INTO boundary_resolution VALUES (?,?,?)",
                    (int(t_ms), "late_scene_pending", int(version)))
            self._write_meta("boundary_version", str(int(version)))
            self._db.execute("COMMIT")
        except Exception:
            self._db.execute("ROLLBACK")
            raise

    def close(self) -> None:
        self._db.close()

    def get_meta(self, key: str, default=None):
        stored = self._index("meta.key", key)
        row = self._db.execute("SELECT value FROM graph_meta WHERE key=?",
                               (stored,)).fetchone()
        return self._open("meta.value", stored, row[0]) if row else default

    def set_meta(self, key: str, value) -> None:
        self._write_meta(key, value)
        self._db.commit()

    def unbirth(self, node_id: str) -> None:
        """Remove an entity-tier node the CURRENT birth rules would not create.

        `delete_meta`/`retire_meta` refuse depth-0 on purpose: that tier is a
        projection of raw and must not be hand-edited. This is not an edit.
        It removes a projection that today's rules would never produce — the
        same thing a full re-derivation from raw would do, done incrementally
        so it does not cost a re-bind of the whole store. Raw is untouched and
        the claims that produced this node are unchanged on disk, so it comes
        back the moment the rules would produce it again.

        No tombstone, deliberately: a tombstone means "believed on evidence,
        then corrected", and that record's whole value is that it is true.
        This node was never believed on evidence — it was proposed by a regex
        that cannot tell Marta from Springfield.
        """
        self._db.execute("DELETE FROM edge WHERE src=? OR dst=?",
                         (node_id, node_id))
        self._db.execute("DELETE FROM node WHERE id=?", (node_id,))
        self._db.commit()

    def pending_by_kind(self, kind: str, min_episodes: int = 1):
        """[(key, surface)] for mentions of `kind` seen in >= min_episodes.

        The waiting room already holds everything needed to judge a mention
        before it becomes a durable node; nothing could ENUMERATE it, so the
        only consumer was the corroboration count.
        """
        stored_kind = self._index("pending.kind", kind)
        rows = self._db.execute(
            "SELECT key, MIN(key_body), MIN(surface), COUNT(DISTINCT episode_id) c "
            "FROM pending_mention WHERE kind=? GROUP BY key "
            "HAVING c >= ? ORDER BY c DESC", (stored_kind, min_episodes)).fetchall()
        return [(self._read_body("pending.key", r[0], r[1], r[0]),
                 self._open("pending.surface", r[0], r[2])) for r in rows]

    def max_node_t_ms(self) -> int:
        """The newest thing this graph knows about.

        Used ONCE, to seed the bind watermark for a graph written before the
        watermark existed. Derived from the claims that built these nodes, so
        it is a real observation rather than a guess — but it is a floor, not
        a proof, and is recorded as such the first time it is used.
        """
        row = self._db.execute("SELECT MAX(last_active_t_ms) FROM node").fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    # -- nodes -------------------------------------------------------------
    def get_node(self, node_id: str) -> Optional[Node]:
        row = self._db.execute(
            "SELECT " + _NODE_COLS + " FROM node WHERE id=?",
            (node_id,)).fetchone()
        return self._node_from_row(row) if row else None

    # PLAINTEXT REUSE FOR UNCHANGED ROWS (2026-10-15). Whole-graph reads (the
    # live map, stats, mining, ubiquity) decrypted every node's attributes on
    # every call although almost none had changed: `map` took 3.4-4.8s on the
    # owner thread while the founder was active. A sealed value's ciphertext
    # is unique per write (fresh nonce), so while the stored bytes are
    # identical the plaintext is too; any rewrite changes the bytes and
    # misses. Bounded, and dropped wholesale rather than evicted piecemeal.
    _ATTR_CACHE_MAX = 20_000

    def _attributes_text(self, node_id, sealed) -> str:
        cache = self.__dict__.setdefault("_attr_plain", {})
        hit = cache.get(node_id)
        if hit is not None and hit[0] == sealed:
            return hit[1]
        text = self._open("node.attributes", node_id, sealed)
        if len(cache) >= self._ATTR_CACHE_MAX:
            cache.clear()
        cache[node_id] = (sealed, text)
        return text

    def _node_from_row(self, row) -> Node:
        return Node(id=row[0], kind=NodeKind(row[1]), activation=row[2],
                    last_active_t_ms=row[3], salience_ema=row[4],
                    derivation_depth=row[5],
                    parent_ids=tuple(json.loads(row[6])),
                    attributes=_attrs_from_json(
                        self._attributes_text(row[0], row[7])),
                    builder_version=row[8])

    def upsert_node(self, node: Node) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO node (" + _NODE_COLS + ") "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (node.id, node.kind.value, node.activation, node.last_active_t_ms,
             node.salience_ema, node.derivation_depth,
             json.dumps(list(node.parent_ids)), self._seal(
                 "node.attributes", node.id, _attrs_to_json(node.attributes)),
             node.builder_version))
        self._db.commit()

    def delete_meta(self, node_id: str) -> None:
        row = self._db.execute(
            "SELECT derivation_depth FROM node WHERE id=?", (node_id,)).fetchone()
        if row is None:
            return
        if row[0] == 0:
            raise ValueError("delete_meta on depth-0 node — the entity tier "
                             "is not prunable here")
        self._db.execute("DELETE FROM node WHERE id=?", (node_id,))
        self._db.execute("DELETE FROM edge WHERE src=? OR dst=?",
                         (node_id, node_id))
        self._db.commit()

    def retire_meta(self, node_id: str, t_ms: int) -> None:
        """Moat-preserving forget: copy the node into the tombstone graveyard,
        then remove it from the live graph. See MemoryGraphStore.retire_meta —
        both stores must behave identically (conformance oracle)."""
        row = self._db.execute(
            "SELECT derivation_depth FROM node WHERE id=?", (node_id,)).fetchone()
        if row is None:
            return
        if row[0] == 0:
            raise ValueError("retire_meta on depth-0 node — the entity tier "
                             "is not retired here")
        self._db.execute(
            "INSERT OR REPLACE INTO tombstone (id, retired_t_ms, kind, "
            "activation, last_active_t_ms, salience_ema, derivation_depth, "
            "parent_ids, attributes, builder_version) SELECT id, ?, kind, "
            "activation, last_active_t_ms, salience_ema, derivation_depth, "
            "parent_ids, attributes, builder_version FROM node WHERE id=?",
            (t_ms, node_id))
        self._db.execute("DELETE FROM node WHERE id=?", (node_id,))
        self._db.execute("DELETE FROM edge WHERE src=? OR dst=?",
                         (node_id, node_id))
        self._db.commit()

    def retired(self) -> List[Tuple[Node, int]]:
        rows = self._db.execute(
            "SELECT " + _NODE_COLS + ", retired_t_ms FROM tombstone")
        return [(self._node_from_row(r), r[9]) for r in rows]

    def retired_node(self, node_id: str):
        """(node, retired_t_ms) for one tombstone, or None. One row, one
        decrypt; `retired()` opens the whole graveyard."""
        row = self._db.execute(
            "SELECT " + _NODE_COLS + ", retired_t_ms FROM tombstone WHERE id=?",
            (node_id,)).fetchone()
        return (self._node_from_row(row), row[9]) if row else None

    def merge_into(self, keep_id: str, absorb_id: str, t_ms: int) -> None:
        """See MemoryGraphStore.merge_into — both stores must behave
        identically (conformance oracle)."""
        if (self.get_node(absorb_id) is None or self.get_node(keep_id) is None
                or keep_id == absorb_id):
            return
        for e in self.edges_of(absorb_id):
            other = e.dst if e.src == absorb_id else e.src
            if other != keep_id:
                ns, nd = ((keep_id, other) if e.src == absorb_id
                          else (other, keep_id))
                ex = self.edge_between(ns, nd, e.type)
                if ex is None:
                    self.upsert_edge(Edge(src=ns, dst=nd, type=e.type,
                                          weight=e.weight,
                                          evidence_count=e.evidence_count,
                                          last_reinforced_t_ms=t_ms))
                else:
                    ex.reinforce(t_ms, delta=e.weight)
                    self.upsert_edge(ex)
        # tombstone absorb (audit), then remove it and its edges
        self._db.execute(
            "INSERT OR REPLACE INTO tombstone (id, retired_t_ms, kind, "
            "activation, last_active_t_ms, salience_ema, derivation_depth, "
            "parent_ids, attributes, builder_version) SELECT id, ?, kind, "
            "activation, last_active_t_ms, salience_ema, derivation_depth, "
            "parent_ids, attributes, builder_version FROM node WHERE id=?",
            (t_ms, absorb_id))
        self._db.execute("DELETE FROM node WHERE id=?", (absorb_id,))
        self._db.execute("DELETE FROM edge WHERE src=? OR dst=?",
                         (absorb_id, absorb_id))
        self._db.commit()

    # -- edges -------------------------------------------------------------
    def upsert_edge(self, edge: Edge) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO edge VALUES (?,?,?,?,?,?)",
            (edge.src, edge.dst, edge.type.value, edge.weight,
             edge.evidence_count, edge.last_reinforced_t_ms))
        self._db.commit()

    def _edge_from_row(self, row) -> Edge:
        return Edge(src=row[0], dst=row[1], type=EdgeType(row[2]),
                    weight=row[3], evidence_count=row[4],
                    last_reinforced_t_ms=row[5])

    def edges_of(self, node_id: str) -> List[Edge]:
        rows = self._db.execute(
            "SELECT * FROM edge WHERE src=? OR dst=?", (node_id, node_id))
        return [self._edge_from_row(r) for r in rows]

    def edge_between(self, a: str, b: str,
                     type: Optional[EdgeType] = None) -> Optional[Edge]:
        sql = ("SELECT * FROM edge WHERE ((src=? AND dst=?) OR (src=? AND dst=?))")
        params: Tuple = (a, b, b, a)
        if type is not None:
            sql += " AND type=?"
            params = params + (type.value,)
        row = self._db.execute(sql, params).fetchone()
        return self._edge_from_row(row) if row else None

    def neighbor_ids(self, node_id: str, hops: int = 1) -> set:
        """The ids `neighbors` would return, without decrypting any node."""
        frontier, seen = {node_id}, {node_id}
        for _ in range(hops):
            nxt = set()
            for nid in frontier:
                for e in self.edges_of(nid):
                    other = e.dst if e.src == nid else e.src
                    if other not in seen:
                        nxt.add(other)
                        seen.add(other)
            frontier = nxt
        return seen - {node_id}

    def neighbors(self, node_id: str, hops: int = 1) -> List[Node]:
        out = []
        for nid in self.neighbor_ids(node_id, hops):
            node = self.get_node(nid)
            if node is not None:
                out.append(node)
        return out

    # -- activation-indexed reads -----------------------------------------
    def nodes_with_activation_ge(self, floor: float, t_ms: int) -> List[Node]:
        """Decayed activation at `t_ms` >= floor.

        The `activation >= ?` term is a PRE-FILTER, not the answer: decay
        never increases a value, so a node whose stored activation is already
        below the floor cannot rise above it. The registered function then
        decides exactly, on the candidates only.
        """
        rows = self._db.execute(
            "SELECT " + _NODE_COLS + " FROM node "
            "WHERE activation >= ? "
            "AND activation_now(activation, last_active_t_ms, ?) >= ?",
            (floor, t_ms, floor))
        return [self._node_from_row(r) for r in rows]

    # -- processed-work ledgers (durable; see the schema note) -------------
    def upkeep_seen(self, episode_id: str) -> set:
        return {r[0] for r in self._db.execute(
            "SELECT claim_id FROM upkeep_seen WHERE episode_id=?",
            (episode_id,))}

    def upkeep_mark(self, episode_id: str, claim_ids) -> None:
        self._db.executemany(
            "INSERT OR IGNORE INTO upkeep_seen VALUES (?,?)",
            [(episode_id, cid) for cid in claim_ids])
        self._db.commit()

    def binding_seen(self, claim_ids=None) -> set:
        if claim_ids is None:
            return {r[0] for r in self._db.execute(
                "SELECT claim_id FROM binding_receipt")}
        wanted = list(dict.fromkeys(str(cid) for cid in claim_ids))
        out = set()
        for start in range(0, len(wanted), 500):
            chunk = wanted[start:start + 500]
            if not chunk:
                continue
            marks = ",".join("?" for _ in chunk)
            out.update(r[0] for r in self._db.execute(
                "SELECT claim_id FROM binding_receipt WHERE claim_id IN (%s)"
                % marks, chunk))
        return out

    def binding_mark(self, claim_ids) -> None:
        self._db.executemany(
            "INSERT OR IGNORE INTO binding_receipt VALUES (?)",
            [(str(cid),) for cid in claim_ids])
        self._db.commit()

    # -- the corroboration waiting room (durable; see the schema note) ------
    def record_sighting(self, key: str, episode_id: str, sighting: dict) -> None:
        # INSERT OR IGNORE, not REPLACE: the FIRST sighting in an episode is
        # the one the node should later be able to cite.
        stored_key = self._index("pending.key", key)
        raw_kind = sighting.get("kind", "")
        stored_kind = self._index("pending.kind", raw_kind)
        self._db.execute(
            "INSERT OR IGNORE INTO pending_mention "
            "(key, episode_id, claim_id, t_ms, grade, confidence, surface, kind,"
            " key_body, kind_body) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (stored_key, episode_id, sighting.get("claim_id", ""),
             int(sighting.get("t_ms", 0)), int(sighting.get("grade", 0)),
             float(sighting.get("confidence", 0.0)),
             self._seal("pending.surface", stored_key,
                        sighting.get("surface", "")), stored_kind,
             self._body("pending.key", stored_key, key),
             self._body("pending.kind", stored_key, raw_kind)))
        self._db.commit()

    def sightings(self, key: str) -> List[dict]:
        stored_key = self._index("pending.key", key)
        return [
            {"episode_id": r[0], "claim_id": r[1], "t_ms": r[2],
             "grade": r[3], "confidence": r[4],
             "surface": self._open("pending.surface", stored_key, r[5]),
             "kind": self._read_body("pending.kind", stored_key, r[7], r[6])}
            for r in self._db.execute(
                "SELECT episode_id, claim_id, t_ms, grade, confidence, "
                "surface, kind, kind_body FROM pending_mention WHERE key=? "
                "ORDER BY t_ms", (stored_key,))]

    def mining_fingerprint(self, node_id: str, key: str) -> Optional[str]:
        stored_key = self._index("mining.key", key)
        row = self._db.execute(
            "SELECT fingerprint FROM mining_ledger WHERE node_id=? AND key=?",
            (node_id, stored_key)).fetchone()
        return row[0] if row else None

    def set_mining_fingerprint(self, node_id: str, key: str,
                               fingerprint: str) -> None:
        stored_key = self._index("mining.key", key)
        self._db.execute(
            "INSERT OR REPLACE INTO mining_ledger "
            "(node_id,key,fingerprint,key_body) VALUES (?,?,?,?)",
            (node_id, stored_key, fingerprint,
             self._body("mining.key", node_id + ":" + stored_key, key)))
        self._db.commit()

    # -- combination ledger (I4 idempotence) ------------------------------
    def ledger_fresh(self, key: str, fingerprint: str = "") -> bool:
        stored_key = self._index("ledger.key", key)
        row = self._db.execute(
            "SELECT fingerprint FROM ledger WHERE key=?", (stored_key,)).fetchone()
        return row is not None and row[0] == fingerprint

    def record_ledger(self, key: str, outcome: str, fingerprint: str) -> None:
        stored_key = self._index("ledger.key", key)
        self._db.execute(
            "INSERT OR REPLACE INTO ledger (key,outcome,fingerprint,key_body) "
            "VALUES (?,?,?,?)",
            (stored_key, self._seal("ledger.outcome", stored_key, outcome),
             fingerprint, self._body("ledger.key", stored_key, key)))
        self._db.commit()

    # -- COMPATIBILITY SHIM, and the finding that came with it -------------
    #
    # `graph_store.nodes` is a plain dict on MemoryGraphStore, and eight
    # production modules reach into it directly:
    #
    #   marination/succession.py   marination/remap.py
    #   marination/schemas.py      marination/mining.py
    #   birth/resolver.py          retention/pruner.py
    #   supplier/vector_index.py   (and graph_store's own activation read)
    #
    # So the real protocol is wider than the method list — and every one of
    # those call sites is a FULL SWEEP over every node in memory.
    #
    # THE CONTRADICTION THIS EXPOSES, recorded rather than quietly papered
    # over: domain/activation.py opens with "The economy's core property: no
    # O(N) sweep exists. Decay is computed on read; updates happen only on
    # events." That is true of the activation math and false of the night
    # job that uses it. On a dict the sweep is invisible; against a real
    # store it is the thing that decides whether a night on a year of
    # memory finishes before morning.
    #
    # These properties exist so the swap can be proven behaviour-identical
    # TODAY. They are the wrong shape for the product and should be replaced
    # by streaming reads (`for node in store.iter_nodes()`) at the eight
    # sites above, which is its own atom, not a silent rewrite inside this
    # one. Materialising the whole graph is exactly what a durable store is
    # supposed to stop doing.
    # They are READ-ONLY on purpose. Each returns a freshly built dict, so
    # `store.nodes[x] = y` would mutate a temporary and vanish without a
    # trace — a write that appears to succeed and does nothing. Grep finds
    # no such write today (checked 2026-07-22); the proxy is here so the
    # first one ever written raises instead of corrupting a night's work.
    @property
    def nodes(self) -> Mapping[str, Node]:
        return MappingProxyType({n.id: n for n in self.all_nodes()})

    @property
    def edges(self) -> Mapping[Tuple[str, str, EdgeType], Edge]:
        return MappingProxyType({(e.src, e.dst, e.type): e
                                 for e in self.all_edges()})

    @property
    def ledger(self) -> Mapping[str, Tuple[str, str]]:
        return MappingProxyType(self.all_ledger())

    def iter_nodes(self):
        """The shape the eight call sites above should eventually use: rows
        streamed from SQLite, never the whole graph in RAM at once."""
        cursor = self._db.execute(
            "SELECT " + _NODE_COLS + " FROM node ORDER BY id")
        for row in cursor:
            yield self._node_from_row(row)

    # -- convenience for migration / inspection ---------------------------
    def all_nodes(self) -> List[Node]:
        rows = self._db.execute(
            "SELECT " + _NODE_COLS + " FROM node ORDER BY id")
        return [self._node_from_row(r) for r in rows]

    def all_edges(self) -> List[Edge]:
        return [self._edge_from_row(r)
                for r in self._db.execute("SELECT * FROM edge "
                                          "ORDER BY src, dst, type")]

    def all_ledger(self) -> Dict[str, Tuple[str, str]]:
        return {self._read_body("ledger.key", r[0], r[3], r[0]):
                (self._open("ledger.outcome", r[0], r[1]), r[2])
                for r in self._db.execute("SELECT key, outcome, fingerprint, key_body "
                                          "FROM ledger")}


def migrate(source, target: SqliteGraphStore) -> dict:
    """Copy a MemoryGraphStore into a SqliteGraphStore.

    Returns the counts, so a caller can assert the migration moved what it
    thought it moved rather than trusting it ran. Nodes before edges: an
    edge whose endpoints are absent is a dangling reference, and it costs
    nothing to avoid creating one even transiently.
    """
    for node in source.nodes.values():
        target.upsert_node(node)
    for edge in source.edges.values():
        target.upsert_edge(edge)
    for key, (outcome, fingerprint) in source.ledger.items():
        target.record_ledger(key, outcome, fingerprint)
    if hasattr(source, "boundary_records"):
        for t_ms, kind in source.boundary_records():
            target.add_boundary(t_ms, kind)
    elif hasattr(source, "load_boundaries"):
        for t_ms in source.load_boundaries():
            target.add_boundary(t_ms)
    if hasattr(source, "all_episode_records"):
        for idx, episode, version, retired, claim_ids in source.all_episode_records():
            target.persist_episode(idx, episode, claim_ids, version)
            if retired is not None:
                target._db.execute(
                    "INSERT OR IGNORE INTO episode_retired VALUES (?,?)",
                    (episode.id, int(retired)))
    elif hasattr(source, "load_episodes"):
        for (_place, idx), episode in source.load_episodes().items():
            claim_ids = (source.claim_ids_for_episode(episode.id)
                         if hasattr(source, "claim_ids_for_episode") else ())
            target.persist_episode(idx, episode, claim_ids)
    if hasattr(source, "episode_successor_records"):
        target._db.executemany(
            "INSERT OR IGNORE INTO episode_successor VALUES (?,?,?)",
            source.episode_successor_records())
    if hasattr(source, "boundary_resolution_records"):
        target._db.executemany(
            "INSERT OR IGNORE INTO boundary_resolution VALUES (?,?,?)",
            source.boundary_resolution_records())
    if hasattr(source, "episode_ids") and hasattr(source, "upkeep_seen"):
        for episode_id in source.episode_ids():
            seen = source.upkeep_seen(episode_id)
            if seen:
                target.upkeep_mark(episode_id, seen)
    if hasattr(source, "binding_seen"):
        target.binding_mark(source.binding_seen())
    if hasattr(source, "get_meta"):
        for meta_key in ("bind_watermark_seq", "boundary_version"):
            value = source.get_meta(meta_key)
            if value is not None:
                target.set_meta(meta_key, value)
    # Read via the retired() method, not a .tombstones attribute: the source
    # may itself be a SqliteGraphStore (the conformance suite swaps backends),
    # whose graveyard lives in a table, not a dict.
    graveyard = list(source.retired())
    for node, retired_t_ms in graveyard:
        target._db.execute(
            "INSERT OR REPLACE INTO tombstone (id, retired_t_ms, kind, "
            "activation, last_active_t_ms, salience_ema, derivation_depth, "
            "parent_ids, attributes) VALUES (?,?,?,?,?,?,?,?,?)",
            (node.id, retired_t_ms, node.kind.value, node.activation,
             node.last_active_t_ms, node.salience_ema, node.derivation_depth,
             json.dumps(list(node.parent_ids)), target._seal(
                 "node.attributes", node.id, _attrs_to_json(node.attributes))))
    target._db.commit()
    return {"nodes": len(source.nodes), "edges": len(source.edges),
            "ledger": len(source.ledger), "tombstones": len(graveyard)}
