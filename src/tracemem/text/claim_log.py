"""OWNS: append-only SQLite claim store (spec 02 §1-2).

T1 no update/delete; T2 idempotent append (content-id PK, INSERT OR IGNORE);
T3 index keys plaintext, semantics ciphertext (crypto injectable — NullCrypto
for tests/dev, sodium adapter in production); T4 round-trip identity.

Also OWNS (sweep #7, 2026-07-19, the claim WRITE path): the five birth
stamps — bidder, bid_id, bid_predicted_value, actual_cost (ruling dd,
campaign/STATE_BOARD.md) and scope (specs/claim_match.md A4) — are
unretrofittable: a claim written without them can never be priced or
fairly graded later. `append` is therefore the gate: it REJECTS LOUDLY
(ValueError) any Claim missing one, rather than defaulting it. They are
stored plaintext (like t_ms/place_key/device_id/grade already are) because
the calibration query (`predicted_vs_actual_by_bidder`) must aggregate over
them in SQL without decrypting every row.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from typing import Iterable, List, Optional

from tracemem.domain import (
    CaptureCoord, Claim, Confidence, CoordinateStack, Grade, Modality,
    Provenance, ProvenanceClass, SpanCoord, WorldCoord,
)
from tracemem.domain.claim import SCOPES
from tracemem.domain.ids import claim_id_version
from tracemem.text import durability
from tracemem.text.migrations import migrate_claim_table

_SCHEMA = """
CREATE TABLE IF NOT EXISTS claim (
  id TEXT PRIMARY KEY, t_ms INTEGER NOT NULL, place_key TEXT,
  device_id TEXT NOT NULL, modality TEXT NOT NULL, grade INTEGER NOT NULL,
  klass TEXT NOT NULL DEFAULT 'lived',
  bidder TEXT NOT NULL, bid_id TEXT NOT NULL,
  bid_predicted_value REAL NOT NULL, actual_cost REAL NOT NULL,
  scope TEXT NOT NULL CHECK (scope IN ('frame','memory')),
  identity_version INTEGER NOT NULL DEFAULT 1,
  ciphertext BLOB NOT NULL,
  -- THE NONCE COLUMN (added 2026-07-23). Its absence was not an omission,
  -- it was a trapdoor: append did `_, ct = crypto.encrypt(b"", wire)` and
  -- THREW THE NONCE AWAY, and every read passed b"" back in. With
  -- NullCrypto that is invisible (identity ignores both). With a real
  -- cipher the store accepted writes, reported {'cipher': 'SodiumCrypto',
  -- 'plaintext': False}, and could not read back one claim:
  --     ValueError: Nonce must be 8, 12 or 24 bytes long
  -- So the encryption that the napkin's container box requires could not be
  -- switched on by configuration at all, and anyone who tried would have
  -- produced an unreadable memory whose own self-report said it was fine.
  -- Existing stores gain it additively; see text/migrations.py.
  nonce BLOB NOT NULL DEFAULT x'');
CREATE INDEX IF NOT EXISTS ix_claim_time ON claim(t_ms);
CREATE INDEX IF NOT EXISTS ix_claim_place ON claim(place_key, t_ms);
CREATE INDEX IF NOT EXISTS ix_claim_bidder ON claim(bidder);
-- Boot asks for the newest lifecycle witness.  A bidder-only index still
-- sorts every witness accumulated over the product's lifetime; pair it with
-- event time so startup remains bounded as memory grows.
CREATE INDEX IF NOT EXISTS ix_claim_bidder_time ON claim(bidder, t_ms DESC);

-- The hash chain lives in its OWN table, deliberately (see the note below).
-- Sealing raw must not require a write path INTO raw: the claim table keeps
-- exactly one door (append) and no column that anything ever rewrites. This
-- also means a store written before the chain existed needs no migration —
-- it simply has no link rows yet, which verify_chain reports as "unchained"
-- rather than crashing or pretending.
CREATE TABLE IF NOT EXISTS claim_link (
  id TEXT PRIMARY KEY, seq INTEGER NOT NULL,
  prev_link TEXT NOT NULL, link TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_link_seq ON claim_link(seq);

-- Additive, non-semantic audit projection for identities minted before v2.
-- Raw ids and ciphertext are never rewritten: old source pixels are gone,
-- so a migration that pretended to reconstruct discarded collisions would
-- manufacture certainty.  This table records only what surviving bytes can
-- prove: their v2 candidate, exact-wire digest, and whether the legacy id
-- matches the historical algorithm.
CREATE TABLE IF NOT EXISTS claim_identity_audit (
  claim_id TEXT PRIMARY KEY,
  candidate_v2_id TEXT,
  wire_sha256 TEXT,
  status TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_identity_audit_candidate
  ON claim_identity_audit(candidate_v2_id);

-- Durable work owed by a helper that could not get the one heavy turn.
-- The witness Claim is immutable evidence that work was deferred; this
-- operational projection may advance pending -> complete, and points only
-- at immutable claim ids so no raw text is duplicated in plaintext.
CREATE TABLE IF NOT EXISTS helper_task (
  task_claim_id TEXT PRIMARY KEY,
  parent_claim_ids TEXT NOT NULL,
  helper_ids TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending','complete')),
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT);
CREATE INDEX IF NOT EXISTS ix_helper_task_status
  ON helper_task(status, task_claim_id);

-- Restart receipts for install-day OWNED ingestion.  Paths and document text
-- are semantic and therefore do not belong in plaintext operational tables.
-- source_key is a per-store salted digest of the canonical path; fingerprint
-- is the exact content digest.  A receipt is written only after append and
-- binding complete, so it can suppress work without suppressing truth.
CREATE TABLE IF NOT EXISTS owned_source_receipt (
  source_key TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  reader_version TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('complete')),
  PRIMARY KEY (source_key, fingerprint, reader_version));

CREATE TABLE IF NOT EXISTS owned_stream_cursor (
  source_key TEXT NOT NULL,
  reader_version TEXT NOT NULL,
  cursor INTEGER NOT NULL,
  PRIMARY KEY (source_key, reader_version));

-- What this store was written WITH. See the note on silent plaintext below.
CREATE TABLE IF NOT EXISTS store_meta (
  key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

# PLAINTEXT MUST NEVER BE SILENT (2026-07-23).
#
# `ClaimLog(path)` defaults to NullCrypto. That default is convenient, and
# it is how every live capture run in this project's history wrote a
# person's screen contents to disk in the clear — unnoticed until an
# outside auditor went looking, while a thousand green tests said nothing.
# The napkin names encryption as content of the container box; a silent
# fallback turns that requirement into an aspiration.
#
# So the store RECORDS WHAT IT WAS WRITTEN WITH, in its own table, readable
# without any key. Three things follow that were previously impossible:
#   · any store file can be asked "are you plaintext?" by anyone holding it
#   · a store created in the clear cannot later pretend it was encrypted
#   · opening an encrypted store with the wrong cipher — or a plaintext one
#     with a real cipher — is DETECTABLE as a mixed store rather than
#     producing a file whose early claims silently cannot be read
# The last case is the dangerous one: mixed crypto yields a store that
# looks fine and is partly unreadable, which is indistinguishable from
# memory loss.
_CIPHER_KEY = "cipher"
_PLAINTEXT = "none"

_STAMP_FIELDS = ("bidder", "bid_id", "bid_predicted_value", "actual_cost", "scope")


class ClaimIdentityCollision(RuntimeError):
    """One ID named two different immutable raw observations."""

# Hash chain (map: Text / Hash chain). Each claim seals the one before it:
#
#     link_n = SHA256( link_{n-1} || wire_bytes_n )
#
# WHAT THIS BUYS, precisely: tampering becomes VISIBLE, not impossible. An
# attacker with the key can still rewrite a claim — but they cannot rewrite
# one WITHOUT rewriting every link after it, so a quiet edit of last
# Tuesday requires forging the whole chain from Tuesday to now. It converts
# "was this store edited?" from unanswerable into a walk.
#
# It is NOT a defence against an attacker who controls the machine and
# rewrites everything (they can recompute the chain). It IS a defence
# against disk corruption, a partial write, a truncated restore, and a
# targeted edit — which are the realistic ways a memory silently stops
# being true. Stated here so nobody mistakes it for a signature.
#
# WHY THE CHAIN IS A SEPARATE TABLE: the seal is meta, and meta never earns
# a write path into raw. If the links were columns on `claim`, sealing a
# pre-chain store or re-linking anything at all would mean UPDATE against
# the raw table — and once that statement exists in this module, "raw is
# immutable" is a comment rather than a property. Kept outside, the claim
# table retains exactly one door: append.
#
# WHY THE HEAD LINKS TO A GENESIS CONSTANT rather than NULL: with NULL,
# "this is the first claim ever" and "every claim before this one was
# deleted" are the same row. The constant makes the head of the chain a
# positive assertion, so a truncated-from-the-front store is detectable.
_GENESIS = "0" * 64

# How many rows the cheap boot-time walk covers. Verification at open must
# cost the same on day 900 as on day 1, so the tail is a fixed count, not a
# fraction; the deep walk (nightly, budgeted) is what eventually sees every
# row. Chosen as a round number, not measured — labeled as the guess it is.
DEFAULT_TAIL = 256


def _link_of(prev_link: str, wire: bytes) -> str:
    """link_n = SHA256( link_{n-1} || wire_bytes_n ).

    Hashes the WIRE bytes (plaintext), not the ciphertext, so verification
    catches both a corrupted blob and a re-encrypted forgery of one row. The
    cost is that a deep verify must decrypt — which is acceptable because the
    deep walk is a nightly budgeted job, not a hot path.
    """
    return hashlib.sha256(prev_link.encode("ascii") + wire).hexdigest()


class NullCrypto:
    """Identity crypto for tests/dev. NOT production (spec 06 supplies sodium)."""

    # Plaintext writes a ZERO-length nonce, and saying so is what lets a
    # reader recognise a row it did not write (2026-07-28). Without this the
    # mixed-store guard was one-sided: opening an encrypted store in
    # plaintext mode handed the raw ciphertext to json.loads and died on
    # UnicodeDecodeError — the mirror image of the crash it was meant to fix,
    # and the one the founder's own store actually hit.
    NONCE_LEN = 0

    def encrypt(self, header: bytes, plaintext: bytes):
        return b"", plaintext

    def decrypt(self, header: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
        return ciphertext


def _wire(c: Claim) -> bytes:
    return json.dumps({
        "v": 1, "text": c.text,
        "world": {"gps": c.coord.world.gps, "pose": c.coord.world.pose,
                  "place_key": c.coord.world.place_key},
        "capture": {"frame_id": c.coord.capture.frame_id,
                    "device_id": c.coord.capture.device_id,
                    "source": c.coord.capture.source,
                    "app": c.coord.capture.app, "window": c.coord.capture.window},
        "span": {"bbox": c.coord.span.bbox}, "t_ms": c.coord.t_ms,
        "prov": {"perceiver": c.provenance.perceiver,
                 "modality": c.provenance.modality.value,
                 "source_ids": list(c.provenance.source_ids),
                 "klass": c.provenance.klass.value},
        "grade": int(c.grade), "confidence": c.confidence.value,
        "structured": c.structured, "id": c.id,
        "bidder": c.bidder, "bid_id": c.bid_id,
        "bid_predicted_value": c.bid_predicted_value,
        "actual_cost": c.actual_cost, "scope": c.scope,
    }, sort_keys=True).encode("utf-8")


def _unwire(b: bytes) -> Claim:
    d = json.loads(b.decode("utf-8"))
    if d.get("v") != 1:
        from tracemem.text.schema_versioning import SchemaVersionError
        raise SchemaVersionError("unknown claim wire version: %r" % d.get("v"))
    coord = CoordinateStack(
        world=WorldCoord(
            gps=tuple(d["world"]["gps"]) if d["world"]["gps"] else None,
            pose=tuple(d["world"]["pose"]) if d["world"]["pose"] else None,
            place_key=d["world"]["place_key"]),
        capture=CaptureCoord(
            frame_id=d["capture"]["frame_id"], device_id=d["capture"]["device_id"],
            source=d["capture"].get("source"),
            app=d["capture"]["app"], window=d["capture"]["window"]),
        span=SpanCoord(bbox=tuple(d["span"]["bbox"]) if d["span"]["bbox"] else None),
        t_ms=d["t_ms"])
    return Claim(
        text=d["text"], coord=coord,
        provenance=Provenance(perceiver=d["prov"]["perceiver"],
                              modality=Modality(d["prov"]["modality"]),
                              source_ids=tuple(d["prov"]["source_ids"]),
                              klass=ProvenanceClass(d["prov"].get("klass", "lived"))),
        grade=Grade(d["grade"]), confidence=Confidence(d["confidence"]),
        structured=d["structured"], id=d["id"],
        bidder=d.get("bidder"), bid_id=d.get("bid_id"),
        bid_predicted_value=d.get("bid_predicted_value"),
        actual_cost=d.get("actual_cost"), scope=d.get("scope"))


# RECENTLY OPENED CLAIMS, AS THEIR DECRYPTED WIRE BYTES (2026-10-09).
#
# A question about "today" decrypted ~31k claims one by one, ~10s of CPU on
# the live daemon, every time it was asked. Claims are immutable and keyed
# by id, so a plaintext seen once can never go stale: this keeps the most
# recent ones (filled on append, so the live day is warm from the moment it
# is captured, and on read). Bytes, not Claim objects, to keep it to roughly
# 700 bytes a claim. Process memory only — nothing here is ever written down.
import collections as _collections
import threading as _threading

WIRE_CACHE_MAX = 60_000
_WIRE_CACHE: "collections.OrderedDict" = _collections.OrderedDict()
_WIRE_LOCK = _threading.Lock()


def _cache_put(key, wire: bytes) -> None:
    with _WIRE_LOCK:
        _WIRE_CACHE[key] = wire
        _WIRE_CACHE.move_to_end(key)
        while len(_WIRE_CACHE) > WIRE_CACHE_MAX:
            _WIRE_CACHE.popitem(last=False)


def _cache_get(key):
    with _WIRE_LOCK:
        return _WIRE_CACHE.get(key)


class ClaimLog:
    def __init__(self, db_path: str, crypto=None) -> None:
        self._db = sqlite3.connect(db_path)
        # Durability policy atom: WAL + synchronous=NORMAL, one commit per
        # claim. Set BEFORE the schema so the very first write already obeys
        # it. See text/durability.py for the decision and its loss windows.
        durability.apply(self._db)
        self._db.executescript(_SCHEMA)
        self._migrate_nonce_column()
        self._crypto = crypto or NullCrypto()
        self._unreadable = set()   # claim ids this process cannot decrypt
        self._cipher_name = (_PLAINTEXT if isinstance(self._crypto, NullCrypto)
                             else type(self._crypto).__name__)
        self._mixed = self._stamp_cipher()
        # Scope of the shared plaintext cache: this file under this cipher
        # and key, so two stores (or two keys) can never answer for each
        # other.
        self._cache_scope = (os.path.abspath(db_path) if db_path != ":memory:"
                             else id(self), self._cipher_name,
                             hashlib.sha256(
                                 getattr(self._crypto, "_key", b"") or b""
                             ).hexdigest()[:16])

    def _migrate_nonce_column(self) -> None:
        """Bring a store written before the `nonce` column up to date.

        The statement itself lives in text/migrations.py, NOT here: this
        module's guarantee is that no schema-rewriting SQL against the claim
        table appears in it at all (test_durability greps for exactly that).
        Migration is additive-only and separately constrained — see that
        module's note.

        Safe on a plaintext store and only on one: existing rows get
        nonce = b'', which is precisely what NullCrypto wrote and expects.
        A store written by a real cipher BEFORE this column existed cannot
        be repaired, because it was never readable — there is nothing in it
        to preserve.
        """
        migrate_claim_table(self._db)

    # -- the ONE crypto seam. Six call sites used to inline `decrypt(b"",
    # -- b"", ct)`; six chances to forget the nonce, and all six had.
    def _seal(self, claim_id: str, wire: bytes):
        """-> (nonce, ciphertext). AAD binds the row to its own id, so a
        ciphertext moved onto another claim's row fails to authenticate
        instead of decrypting into a claim that was never made."""
        return self._crypto.encrypt(claim_id.encode(), wire)

    def _open(self, claim_id: str, nonce, ciphertext) -> bytes:
        return self._crypto.decrypt(claim_id.encode(), bytes(nonce or b""),
                                    ciphertext)

    def _try_open(self, claim_id: str, nonce, ciphertext) -> Optional[bytes]:
        """_open, but a claim this process cannot decrypt is SKIPPED rather
        than fatal. Returns None and counts it.

        FOUND BY USING THE PRODUCT (2026-07-28): the daemon would not start
        at all on the founder's real store. That store holds 604 claims
        written plaintext plus claims written under a real cipher, and
        `all_claims()` raised `ValueError: Nonce must be 8, 12 or 24 bytes
        long` on the first plaintext row — taking the whole process down
        before the ask socket could serve anything.

        The store ALREADY detected this and printed "STORE IS MIXED ... older
        claims CANNOT be read by this process" one line before crashing. It
        knew, said so, and died anyway. Knowing and still failing hard is
        worse than not knowing, because the honest report is right there and
        useless.

        A claim written under a key this process does not hold is simply not
        readable HERE. That is a fact to report, not a reason to lose the
        memory that IS readable. So: skip, count, and let `unreadable()`
        tell the truth upward. Raw is untouched — this is a read-path
        decision only, and nothing is rewritten or dropped from the store.

        WHAT IS *NOT* SKIPPED, and the distinction is the whole safety of
        this method: TAMPERING. `AuthFailure` means the nonce was the right
        SHAPE for this cipher and the body still failed to authenticate —
        i.e. a row of this store was altered, or a ciphertext was lifted onto
        another claim's row. That must stay as loud as it has always been, or
        "raw is immutable" is satisfied by a store whose memories can be
        quietly swapped. It propagates.

        Only a STRUCTURALLY foreign nonce is skipped: a length this cipher
        could never have produced (0 bytes from NullCrypto, or 16 vs 24
        between cipher generations) proves a DIFFERENT cipher wrote that row,
        which is a fact about key custody, not about integrity.
        """
        expected = getattr(self._crypto, "NONCE_LEN", None)
        raw_nonce = bytes(nonce or b"")
        if expected is not None and len(raw_nonce) != expected:
            self._unreadable.add(claim_id)      # foreign cipher, not tampering
            return None
        return self._open(claim_id, nonce, ciphertext)

    def unreadable(self) -> int:
        """How many claims this process could not decrypt. A UI that shows a
        memory MUST be able to say 'and N more I cannot read', or the gap is
        silent — which is the one thing this system may never be."""
        return len(self._unreadable)

    def _stamp_cipher(self) -> bool:
        """Record the cipher on first open; report disagreement thereafter.

        Returns True if this store was created with a DIFFERENT cipher than
        the one now opening it — a mixed store, whose older claims this
        process cannot read. Reported rather than raised, because refusing
        to open would leave the founder with a file and no way to inspect
        what happened to it.
        """
        row = self._db.execute("SELECT value FROM store_meta WHERE key=?",
                               (_CIPHER_KEY,)).fetchone()
        if row is None:
            self._db.execute("INSERT INTO store_meta VALUES (?,?)",
                             (_CIPHER_KEY, self._cipher_name))
            self._db.commit()
            return False
        return row[0] != self._cipher_name

    def encryption(self) -> dict:
        """What this store is, cryptographically. Answerable without a key.

        `plaintext` is the field that matters: True means every claim in
        this file is readable by anyone holding it.
        """
        row = self._db.execute("SELECT value FROM store_meta WHERE key=?",
                               (_CIPHER_KEY,)).fetchone()
        created_with = row[0] if row else self._cipher_name
        return {
            "cipher": self._cipher_name,
            "created_with": created_with,
            "plaintext": self._cipher_name == _PLAINTEXT,
            "mixed": self._mixed,
        }

    def durability(self) -> dict:
        """The policy this connection actually got (read back, not assumed)."""
        return durability.describe(self._db)

    def append(self, claim: Claim) -> None:
        """Rejects LOUDLY (ValueError) if any of the five birth stamps is
        missing. Never defaults them — an un-stamped claim written now can
        never be priced or fairly graded later (ruling dd; unretrofittable)."""
        missing = [f for f in _STAMP_FIELDS if getattr(claim, f) is None]
        if missing:
            raise ValueError(
                "claim %r missing birth stamp(s) %r — every claim must be "
                "stamped with bidder/bid_id/bid_predicted_value/actual_cost "
                "(ruling dd) and scope (claim_match.md A4) AT BIRTH; the "
                "store never silently defaults these" % (claim.id, missing))
        if claim.scope not in SCOPES:
            raise ValueError("claim %r has invalid scope %r — must be one "
                             "of %r" % (claim.id, claim.scope, SCOPES))
        # Idempotence (T2) BEFORE the chain: a re-append of a claim already
        # stored must not advance the chain. If it did, every retry would
        # write a link over a claim that is not there, and the next verify
        # would report a break in a store that is perfectly intact — the
        # integrity check would become a generator of false alarms.
        existing = self._db.execute(
            "SELECT ciphertext, nonce FROM claim WHERE id=?", (claim.id,)
        ).fetchone()
        if existing:
            wire = self._try_open(claim.id, existing[1], existing[0])
            if wire is None:
                raise ClaimIdentityCollision(
                    "claim id %s already exists but cannot be opened; refusing "
                    "to call an unverifiable row an idempotent replay" % claim.id)
            stored = _unwire(wire)
            if _wire(stored) != _wire(claim):
                raise ClaimIdentityCollision(
                    "claim id %s names different raw observations; neither "
                    "was overwritten or discarded" % claim.id)
            return

        wire = _wire(claim)
        identity_version = claim_id_version(
            claim.id, claim.text, claim.coord, claim.provenance,
            structured=claim.structured, grade=claim.grade,
            confidence=claim.confidence)
        seq, prev_link = self._chain_tip()
        link = _link_of(prev_link, wire)
        nonce, ct = self._seal(claim.id, wire)
        _cache_put((self._cache_scope, claim.id), wire)
        # Columns named explicitly. The positional form silently binds by
        # ORDER, so adding `nonce` to the schema made every insert in this
        # module wrong in a way only an arity error caught — and had the new
        # column been added anywhere but last, it would have bound the wrong
        # value into the wrong column with no error at all.
        self._db.execute(
            "INSERT OR IGNORE INTO claim (id, t_ms, place_key, device_id, "
            "modality, grade, klass, bidder, bid_id, bid_predicted_value, "
            "actual_cost, scope, identity_version, ciphertext, nonce) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (claim.id, claim.coord.t_ms, claim.coord.world.place_key,
             claim.coord.capture.device_id, claim.provenance.modality.value,
             int(claim.grade), claim.provenance.klass.value, claim.bidder,
             self._indexed_bid_id(claim), claim.bid_predicted_value,
             claim.actual_cost,
             claim.scope, identity_version, ct, nonce))
        self._db.execute(
            "INSERT OR IGNORE INTO claim_link VALUES (?,?,?,?)",
            (claim.id, seq + 1, prev_link, link))
        structured = claim.structured or {}
        if structured.get("witness") == "helper-deferred":
            parents = structured.get("parent_claim_ids") or []
            helpers = structured.get("helpers") or []
            if (not isinstance(parents, list) or not all(
                    isinstance(x, str) for x in parents) or
                    not isinstance(helpers, list) or not all(
                    isinstance(x, str) for x in helpers)):
                raise ValueError("helper-deferred witness has invalid task ids")
            self._db.execute(
                "INSERT OR IGNORE INTO helper_task "
                "(task_claim_id,parent_claim_ids,helper_ids,status) "
                "VALUES (?,?,?,'pending')",
                (claim.id, json.dumps(parents, separators=(",", ":")),
                 json.dumps(helpers, separators=(",", ":"))))
        # One commit covers the claim and its seal: a crash between them would
        # leave a stored claim that verify_chain calls unchained forever.
        self._db.commit()

    def pending_helper_tasks(self, limit: int = 8) -> List[dict]:
        rows = self._db.execute(
            "SELECT task_claim_id,parent_claim_ids,helper_ids,attempts,last_error "
            "FROM helper_task WHERE status='pending' ORDER BY task_claim_id LIMIT ?",
            (max(1, int(limit)),)).fetchall()
        return [{"task_claim_id": row[0],
                 "parent_claim_ids": json.loads(row[1]),
                 "helper_ids": json.loads(row[2]),
                 "attempts": int(row[3]), "last_error": row[4]}
                for row in rows]

    def finish_helper_task(self, task_claim_id: str) -> None:
        self._db.execute(
            "UPDATE helper_task SET status='complete',attempts=attempts+1,"
            "last_error=NULL WHERE task_claim_id=?", (task_claim_id,))
        self._db.commit()

    def _indexed_bid_id(self, claim) -> str:
        """What the plaintext bid_id COLUMN holds. Nothing queries it; the
        claim's real bid_id is sealed in the wire. An OWNED claim's bid id
        names the user's file ("owned:/Users/.../biopsy.pdf:t"), and the
        column put every cold-start path in the clear beside a sealed body
        (review, 2026-10-15). It keeps the same salted digest the receipt
        ledger already uses, so re-reading a file still lands identically."""
        source = getattr(getattr(claim.coord, "capture", None), "source", None)
        bid = claim.bid_id
        if source and bid and source in bid:
            return "owned:" + self._owned_source_key(source)[:32] + bid[
                bid.index(source) + len(source):]
        return bid

    def _owned_source_key(self, source: str) -> str:
        row = self._db.execute(
            "SELECT value FROM store_meta WHERE key='owned_source_salt'"
        ).fetchone()
        if row is None:
            salt = os.urandom(32).hex()
            self._db.execute(
                "INSERT OR IGNORE INTO store_meta VALUES "
                "('owned_source_salt',?)", (salt,))
            self._db.commit()
        else:
            salt = row[0]
        canonical = os.path.realpath(os.path.abspath(source))
        return hashlib.sha256(
            bytes.fromhex(salt) + canonical.encode("utf-8", "surrogateescape")
        ).hexdigest()

    def owned_source_complete(self, source: str, fingerprint: str,
                              reader_version: str) -> bool:
        key = self._owned_source_key(source)
        row = self._db.execute(
            "SELECT 1 FROM owned_source_receipt WHERE source_key=? AND "
            "fingerprint=? AND reader_version=? AND status='complete'",
            (key, fingerprint, reader_version)).fetchone()
        return row is not None

    def finish_owned_source(self, source: str, fingerprint: str,
                            reader_version: str) -> None:
        self._db.execute(
            "INSERT OR IGNORE INTO owned_source_receipt VALUES (?,?,?,'complete')",
            (self._owned_source_key(source), str(fingerprint),
             str(reader_version)))
        self._db.commit()

    def owned_source_receipts(self) -> int:
        return int(self._db.execute(
            "SELECT COUNT(*) FROM owned_source_receipt WHERE status='complete'"
        ).fetchone()[0])

    def owned_stream_cursor(self, source: str, reader_version: str) -> int:
        row = self._db.execute(
            "SELECT cursor FROM owned_stream_cursor WHERE source_key=? AND "
            "reader_version=?",
            (self._owned_source_key(source), str(reader_version))).fetchone()
        return int(row[0]) if row else 0

    def finish_owned_stream(self, source: str, reader_version: str,
                            cursor: int) -> None:
        self._db.execute(
            "INSERT INTO owned_stream_cursor VALUES (?,?,?) "
            "ON CONFLICT(source_key,reader_version) DO UPDATE SET cursor="
            "max(cursor,excluded.cursor)",
            (self._owned_source_key(source), str(reader_version), int(cursor)))
        self._db.commit()

    def fail_helper_task(self, task_claim_id: str, error: str) -> None:
        self._db.execute(
            "UPDATE helper_task SET attempts=attempts+1,last_error=? "
            "WHERE task_claim_id=? AND status='pending'",
            (str(error)[:240], task_claim_id))
        self._db.commit()

    def _chain_tip(self):
        """(seq, link) of the last sealed claim, or (0, genesis) if none."""
        row = self._db.execute(
            "SELECT seq, link FROM claim_link ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return (row[0], row[1]) if row else (0, _GENESIS)

    def chain_head(self) -> str:
        """The link of the most recently appended claim — read from disk, not
        cached, so a reopened store continues the chain instead of starting a
        second one that seals nothing."""
        return self._chain_tip()[1]

    def verify_chain(self, tail: Optional[int] = None) -> dict:
        """Walk the chain and NAME the first claim that does not hold up.

        `tail=N` walks only the last N rows — the cheap check for boot. No
        argument walks everything — the deep check for the night. The report
        carries `complete` so a caller can never mistake a tail pass for a
        clean bill of health over the whole store.

        Returns: {ok, broken_at, reason, checked, complete}
          reason "link"      — this claim's prev_link is not its predecessor's
                               link: a row between them is gone or reordered.
          reason "content"   — this claim's bytes no longer hash to its own
                               link: the row itself was altered or rotted.
          reason "unchained" — pre-chain row (NULL link) from a store written
                               before this atom existed. Not corruption; not
                               a guarantee either. Said out loud.
        """
        base = ("SELECT c.id, c.ciphertext, l.prev_link, l.link, c.nonce "
                "FROM claim c LEFT JOIN claim_link l ON l.id = c.id ")
        if tail is not None:
            rows = list(self._db.execute(
                base + "ORDER BY c.rowid DESC LIMIT ?", (tail,)))[::-1]
        else:
            rows = list(self._db.execute(base + "ORDER BY c.rowid"))

        checked = 0
        expected_prev = None  # unknown for the first row of a tail window
        for claim_id, ct, prev_link, link, nonce in rows:
            if link is None or prev_link is None:
                return {"ok": False, "broken_at": claim_id,
                        "reason": "unchained", "checked": checked,
                        "complete": tail is None}
            # Order matters: the severed-link case must be reported against
            # the claim whose parent vanished, and it must be found BEFORE
            # the content check, whose hash would also differ.
            if expected_prev is not None and prev_link != expected_prev:
                return {"ok": False, "broken_at": claim_id, "reason": "link",
                        "checked": checked, "complete": tail is None}
            if _link_of(prev_link, self._open(claim_id, nonce, ct)) != link:
                return {"ok": False, "broken_at": claim_id,
                        "reason": "content", "checked": checked,
                        "complete": tail is None}
            expected_prev = link
            checked += 1

        return {"ok": True, "broken_at": None, "reason": None,
                "checked": checked, "complete": tail is None}

    def verify_chain_window(self, offset: int, limit: int, stop=None) -> dict:
        """Verify exactly one bounded, row-ordered window of the chain.

        ``offset`` is a ROWID KEY: the window is the next ``limit`` rows whose
        rowid is greater than it.  The claim table is append-only and rows
        are never deleted, so rowids are dense and this is exactly the old
        positional meaning (rows before the window = ``offset``).  What
        changed is the cost of getting there (2026-10-14): ``LIMIT ? OFFSET
        ?`` on the rowid-ordered join stepped through every earlier row,
        ciphertext pages included, before the first row of the window.  At
        cursor 320,000 on the live store that alone outlasted the night's
        slice, the first deadline poll stopped the audit at row zero, and
        the cursor never moved again.  A seek is O(log n) wherever it lands.

        The row immediately before the window contributes only its stored
        link, so the first row's parent is still checked without decrypting
        history outside the window.  ``last_rowid`` is the rowid of the last
        row verified (the cursor to resume after); ``eof`` says the window
        ran out of rows before ``limit``.
        """
        offset = max(0, int(offset))
        limit = max(0, int(limit))
        done = {"ok": True, "broken_at": None, "reason": None, "checked": 0,
                "complete": False, "expired": False, "last_rowid": offset,
                "eof": False}
        if limit == 0:
            return done

        predecessor = None
        if offset > 0:
            predecessor = self._db.execute(
                "SELECT l.link FROM claim c LEFT JOIN claim_link l "
                "ON l.id = c.id WHERE c.rowid <= ? "
                "ORDER BY c.rowid DESC LIMIT 1", (offset,)).fetchone()
        expected_prev = predecessor[0] if predecessor is not None else _GENESIS

        rows = self._db.execute(
            "SELECT c.rowid, c.id, c.ciphertext, l.prev_link, l.link, c.nonce "
            "FROM claim c LEFT JOIN claim_link l ON l.id = c.id "
            "WHERE c.rowid > ? ORDER BY c.rowid LIMIT ?", (offset, limit))
        checked, last = 0, offset

        def result(**fields):
            out = dict(done, checked=checked, last_rowid=last)
            out.update(fields)
            return out

        for rowid, claim_id, ct, prev_link, link, nonce in rows:
            if stop is not None and stop():
                return result(expired=True)
            if link is None or prev_link is None:
                return result(ok=False, broken_at=claim_id, reason="unchained")
            if expected_prev is None or prev_link != expected_prev:
                return result(ok=False, broken_at=claim_id, reason="link")
            if _link_of(prev_link, self._open(claim_id, nonce, ct)) != link:
                return result(ok=False, broken_at=claim_id, reason="content")
            expected_prev = link
            checked += 1
            last = int(rowid)

        return result(eof=checked < limit)

    def rebuild_chain(self) -> int:
        """Recompute every link from the rows as they currently stand.

        DESTROYS tamper evidence. This is not a repair tool — a chain that
        disagrees with its contents is telling you something true, and this
        method's only effect is to make it stop saying it. Two legitimate
        uses: sealing a store written before the chain existed, and the test
        that proves the chain is not a signature.

        Returns the number of rows re-linked.
        """
        rows = list(self._db.execute(
            "SELECT id, ciphertext, nonce FROM claim ORDER BY rowid"))
        prev = _GENESIS
        for seq, (claim_id, ct, nonce) in enumerate(rows, start=1):
            link = _link_of(prev, self._open(claim_id, nonce, ct))
            self._db.execute(
                "INSERT OR REPLACE INTO claim_link VALUES (?,?,?,?)",
                (claim_id, seq, prev, link))
            prev = link
        self._db.commit()
        return len(rows)

    def get(self, claim_id: str) -> Optional[Claim]:
        row = self._db.execute(
            "SELECT ciphertext, nonce FROM claim WHERE id=?", (claim_id,)).fetchone()
        if not row:
            return None
        wire = self._try_open(claim_id, row[1], row[0])
        return _unwire(wire) if wire is not None else None

    def predicted_vs_actual_by_bidder(self) -> List[dict]:
        """ruling (dd): the calibration table expected-value estimation reads.
        Bidders whose predictions run hot (avg_predicted > avg_actual) are
        the ones the auction should discount mechanically — this is the
        table that discounting reads from."""
        rows = self._db.execute(
            "SELECT bidder, AVG(bid_predicted_value), AVG(actual_cost), COUNT(*) "
            "FROM claim GROUP BY bidder ORDER BY bidder").fetchall()
        return [{"bidder": r[0], "avg_predicted_value": r[1],
                 "avg_actual_cost": r[2], "n": r[3]} for r in rows]

    def read_range(self, t0_ms: int, t1_ms: int,
                   place_key: Optional[str] = None) -> List[Claim]:
        if place_key is None:
            rows = self._db.execute(
                "SELECT id, ciphertext, nonce FROM claim WHERE t_ms BETWEEN ? AND ? ORDER BY t_ms",
                (t0_ms, t1_ms))
        else:
            rows = self._db.execute(
                "SELECT id, ciphertext, nonce FROM claim WHERE place_key=? AND t_ms BETWEEN ? AND ?"
                " ORDER BY t_ms", (place_key, t0_ms, t1_ms))
        out = []
        for r in rows:
            key = (self._cache_scope, r[0])
            wire = _cache_get(key)
            if wire is None:
                wire = self._try_open(r[0], r[2], r[1])
                if wire is not None:
                    _cache_put(key, wire)
            if wire is not None:
                out.append(_unwire(wire))
        return out

    def count_range(self, t0_ms: int, t1_ms: int) -> int:
        """How many claims lie in [t0, t1]: the time index, no decryption."""
        return int(self._db.execute(
            "SELECT COUNT(*) FROM claim WHERE t_ms BETWEEN ? AND ?",
            (t0_ms, t1_ms)).fetchone()[0])

    def read_range_by_bidder(self, t0_ms: int, t1_ms: int,
                             bidder: str) -> List[Claim]:
        """One source's claims in a range, through ix_claim_bidder_time."""
        rows = self._db.execute(
            "SELECT id, ciphertext, nonce FROM claim WHERE bidder=? AND "
            "t_ms BETWEEN ? AND ? ORDER BY t_ms", (bidder, t0_ms, t1_ms))
        out = []
        for r in rows:
            wire = self._try_open(r[0], r[2], r[1])
            if wire is not None:
                out.append(_unwire(wire))
        return out

    def positions(self, after_seq: int = 0,
                  through_seq: Optional[int] = None,
                  limit: Optional[int] = None) -> List[tuple]:
        """Immutable append positions, without decrypting raw observations.

        Event time is part of what was observed and can arrive late.  It is
        therefore not a processing cursor.  ``claim_link.seq`` is assigned in
        the same transaction as the append and is the one total order this
        local log can honestly use for restart catch-up.
        """
        where = "seq>?"
        args = [int(after_seq)]
        if through_seq is not None:
            where += " AND seq<=?"
            args.append(int(through_seq))
        sql = "SELECT id, seq FROM claim_link WHERE %s ORDER BY seq" % where
        if limit is not None:
            sql += " LIMIT ?"
            args.append(max(0, int(limit)))
        rows = self._db.execute(sql, args)
        return [(str(r[0]), int(r[1])) for r in rows]

    def projection_rows(self):
        """Stream only the immutable coordinate index needed by Binder."""
        rows = self._db.execute(
            "SELECT id, t_ms, place_key FROM claim ORDER BY t_ms, id")
        for claim_id, t_ms, place_key in rows:
            yield (str(claim_id), int(t_ms), place_key)

    def max_event_t_ms(self) -> int:
        row = self._db.execute("SELECT MAX(t_ms) FROM claim").fetchone()
        return int(row[0] or 0)

    def latest_event_t_ms(self) -> Optional[int]:
        """Newest event timestamp, preserving empty versus timestamp zero."""
        row = self._db.execute("SELECT MAX(t_ms) FROM claim").fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def latest_by_bidder(self, bidder: str) -> Optional[Claim]:
        """Newest event from one producer, decrypting at most one row.

        Lifecycle boot uses this for ``system:witness``.  It must not call
        ``all_claims``: doing so decrypted and materialised 633k observations
        before the supervisor could start, making startup time grow without
        bound and leaving the health socket present but unable to answer.
        Event time is used deliberately here (rather than append sequence):
        gap detection compares the latest observed event with the latest
        lifecycle fact on that same timeline.  Append sequence only breaks a
        same-millisecond tie.
        """
        row = self._db.execute(
            "SELECT c.id, c.ciphertext, c.nonce "
            "FROM claim c LEFT JOIN claim_link l ON l.id=c.id "
            "WHERE c.bidder=? ORDER BY c.t_ms DESC, l.seq DESC LIMIT 1",
            (str(bidder),)).fetchone()
        if row is None:
            return None
        wire = self._try_open(row[0], row[2], row[1])
        return _unwire(wire) if wire is not None else None

    def max_append_seq(self) -> int:
        """Highest durable append position (zero for an empty log)."""
        row = self._db.execute("SELECT MAX(seq) FROM claim_link").fetchone()
        return int(row[0] or 0)

    def audit_legacy_identities(self, limit: int = 5000) -> dict:
        """Append audit facts for pre-v2 rows without changing raw history.

        The missing observation problem is intrinsically asymmetric: the
        surviving row can be mapped to the id v2 would give it, but a second
        observation silently discarded by v1 can no longer be recovered once
        its pixels are gone.  Completion therefore means every surviving row
        is accounted for, never that history was magically made unambiguous.
        """
        rows = list(self._db.execute(
            "SELECT c.id, c.ciphertext, c.nonce FROM claim c "
            "LEFT JOIN claim_identity_audit a ON a.claim_id=c.id "
            "WHERE c.identity_version<2 AND a.claim_id IS NULL "
            "ORDER BY c.rowid LIMIT ?", (max(1, int(limit)),)))
        audited = []
        expected_nonce = getattr(self._crypto, "NONCE_LEN", None)
        for claim_id, ciphertext, nonce in rows:
            raw_nonce = bytes(nonce or b"")
            if expected_nonce is not None and len(raw_nonce) != expected_nonce:
                audited.append((claim_id, None, None,
                                "unreadable_foreign_cipher"))
                continue
            try:
                wire = self._open(claim_id, nonce, ciphertext)
                claim = _unwire(wire)
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError,
                    TypeError, ValueError):
                # Auth failures from a real cipher are not among these JSON
                # shape errors and still propagate loudly as tamper evidence.
                audited.append((claim_id, None, None, "invalid_wire"))
                continue
            candidate = claim_id_version(
                claim.id, claim.text, claim.coord, claim.provenance,
                structured=claim.structured, grade=claim.grade,
                confidence=claim.confidence)
            from tracemem.domain.ids import claim_id as current_claim_id
            v2 = current_claim_id(
                claim.text, claim.coord, claim.provenance,
                structured=claim.structured, grade=claim.grade,
                confidence=claim.confidence)
            status = "valid_v1" if candidate == 1 else "explicit_unknown_id"
            audited.append((claim_id, v2, hashlib.sha256(wire).hexdigest(),
                            status))
        self._db.executemany(
            "INSERT OR IGNORE INTO claim_identity_audit "
            "(claim_id, candidate_v2_id, wire_sha256, status) VALUES (?,?,?,?)",
            audited)
        self._db.commit()
        report = self.identity_audit_report()
        report["added"] = len(audited)
        return report

    def identity_audit_report(self) -> dict:
        """Coverage and conflicts detectable from surviving legacy rows."""
        legacy = int(self._db.execute(
            "SELECT COUNT(*) FROM claim WHERE identity_version<2").fetchone()[0])
        audited = int(self._db.execute(
            "SELECT COUNT(*) FROM claim_identity_audit").fetchone()[0])
        unreadable = int(self._db.execute(
            "SELECT COUNT(*) FROM claim_identity_audit WHERE status IN "
            "('unreadable_foreign_cipher','invalid_wire')").fetchone()[0])
        invalid = int(self._db.execute(
            "SELECT COUNT(*) FROM claim_identity_audit "
            "WHERE status='explicit_unknown_id'").fetchone()[0])
        collision_rows = int(self._db.execute(
            "SELECT COALESCE(SUM(n),0) FROM (SELECT COUNT(*) n "
            "FROM claim_identity_audit WHERE candidate_v2_id IS NOT NULL "
            "GROUP BY candidate_v2_id HAVING COUNT(*)>1)").fetchone()[0])
        current_aliases = int(self._db.execute(
            "SELECT COUNT(*) FROM claim_identity_audit a JOIN claim c "
            "ON c.id=a.candidate_v2_id WHERE c.identity_version>=2").fetchone()[0])
        return {
            "legacy_rows": legacy,
            "audited_rows": audited,
            "remaining": max(0, legacy - audited),
            "unreadable": unreadable,
            "invalid_legacy_ids": invalid,
            "candidate_collision_rows": collision_rows,
            "current_aliases": current_aliases,
            # This is not a failure count. It names the limit of the evidence:
            # v1 collisions discarded before storage leave no row to count.
            "historic_discarded_observations": "unknowable",
        }

    def read_ids(self, claim_ids) -> List[Claim]:
        """Read selected claims in append order; foreign-cipher rows stay loud.

        SQLite builds have different parameter limits, so the selection is
        chunked.  This is used by boot catch-up to decrypt only claims without
        a completed binding receipt rather than the lifetime store.
        """
        wanted = list(dict.fromkeys(str(cid) for cid in claim_ids))
        if not wanted:
            return []
        out = []
        for start in range(0, len(wanted), 500):
            chunk = wanted[start:start + 500]
            marks = ",".join("?" for _ in chunk)
            rows = self._db.execute(
                "SELECT c.id, c.ciphertext, c.nonce, l.seq "
                "FROM claim c JOIN claim_link l ON l.id=c.id "
                "WHERE c.id IN (%s) ORDER BY l.seq" % marks, chunk)
            for claim_id, ciphertext, nonce, seq in rows:
                wire = self._try_open(claim_id, nonce, ciphertext)
                if wire is not None:
                    out.append((int(seq), _unwire(wire)))
        out.sort(key=lambda item: item[0])
        return [claim for _seq, claim in out]

    def ids_in_range(self, t0_ms: int, t1_ms: int,
                     place_key: Optional[str] = None) -> List[str]:
        """Claim IDS in a time range — no decryption (map: Supplier / Time index).

        Deliberately separate from read_range: that one decrypts every row to
        rebuild whole Claims, which is the right thing when you need the text
        and completely wrong when you only need to know WHICH claims fall in a
        window. Counting, coverage math, and range scans all want ids only,
        and at life scale the difference is the difference between a scan and
        a stall. Rides the existing ix_claim_time index.
        """
        if place_key is None:
            rows = self._db.execute(
                "SELECT id FROM claim WHERE t_ms BETWEEN ? AND ? ORDER BY t_ms",
                (t0_ms, t1_ms))
        else:
            rows = self._db.execute(
                "SELECT id FROM claim WHERE place_key=? AND t_ms BETWEEN ? AND ?"
                " ORDER BY t_ms", (place_key, t0_ms, t1_ms))
        return [r[0] for r in rows]

    def count_in_range(self, t0_ms: int, t1_ms: int) -> int:
        """How many claims fall in a window, without materialising any."""
        return self._db.execute(
            "SELECT COUNT(*) FROM claim WHERE t_ms BETWEEN ? AND ?",
            (t0_ms, t1_ms)).fetchone()[0]

    def by_bidder(self, bidder: str) -> List[Claim]:
        """Every claim from one producer, oldest first, decrypting ONLY those.

        Added 2026-10-07: every question ran `find_corrections`, which looked
        for the founder's handful of corrections by decrypting all 635k
        claims via `all_claims()` — on the thread that answers, so every ask
        on the founder's store timed out at 120s. `bidder` is a plaintext,
        indexed column (ix_claim_bidder), so a producer's claims are found
        without opening anyone else's.
        """
        rows = self._db.execute(
            "SELECT c.id, c.ciphertext, c.nonce FROM claim c "
            "LEFT JOIN claim_link l ON l.id=c.id "
            "WHERE c.bidder=? ORDER BY c.t_ms, l.seq", (str(bidder),))
        out = []
        for r in rows:
            wire = self._try_open(r[0], r[2], r[1])
            if wire is not None:
                out.append(_unwire(wire))
        return out

    def all_claims(self) -> Iterable[Claim]:
        rows = self._db.execute("SELECT id, ciphertext, nonce FROM claim ORDER BY t_ms")
        out = []
        for r in rows:
            wire = self._try_open(r[0], r[2], r[1])
            if wire is not None:
                out.append(_unwire(wire))
        return out

    def count(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM claim").fetchone()[0]
