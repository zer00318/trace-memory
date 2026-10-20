"""THE ENCRYPTION ROUND-TRIP — can the store actually BE encrypted?

Written 2026-07-23. `test_sodium_crypto_round_trips` has been green since
2026-07-19 and proves that SodiumCrypto encrypts a byte string. No test
ever put a real cipher THROUGH ClaimLog, and the seam between them was
broken the whole time:

    append():  _, ct = self._crypto.encrypt(b"", wire)   # nonce DISCARDED
    reads():   self._crypto.decrypt(b"", b"", ct)        # nonce b"" back in

With NullCrypto both are invisible — identity ignores the nonce. With any
real cipher the store accepted writes, reported

    {'cipher': 'SodiumCrypto', 'created_with': 'SodiumCrypto',
     'plaintext': False, 'mixed': False}

and could not read back a single claim:

    ValueError: Nonce must be 8, 12 or 24 bytes long

So the encryption the napkin's container box requires ("Encryption, i.e.
people using our app can decipher") could not be turned on by configuration
at all, and the failure mode was the worst available: a store that swears
it is encrypted, is unreadable, and says so only when someone finally asks
it a question — which for a memory product is months later, by which time
the frames it came from are long deleted.

Two tests here, and the second is what makes the first mean anything.
"""
from __future__ import annotations

import glob
import os
from pathlib import Path

import pytest

from tests.helpers import make_claim
from tracemem.container.crypto import AuthFailure, SodiumCrypto, StdlibAead
from tracemem.text.claim_log import ClaimLog, NullCrypto

NEEDLE = "capacitor plans for the reactor"

CIPHERS = [
    ("SodiumCrypto", lambda: SodiumCrypto(b"k" * 32)),
    ("StdlibAead", lambda: StdlibAead(b"k" * 32)),
]


def _fill(path, crypto, n=3):
    log = ClaimLog(path, crypto=crypto)
    for i in range(n):
        log.append(make_claim(text="%s %d" % (NEEDLE, i), t_ms=1000 + i,
                              frame="f%d" % i))
    return log


def _files_containing(path, needle: str):
    """Every file the store writes, not just the .db — WAL mode parks recent
    rows in `-wal` for a long time, so a check that reads only the main file
    reports a clean bill of health over data sitting in the clear beside it.
    (Witnessed: the first version of this check did exactly that and passed
    for NullCrypto.)"""
    hits = []
    for f in sorted(glob.glob(path + "*")):
        with open(f, "rb") as fh:
            if needle.encode() in fh.read():
                hits.append(os.path.basename(f))
    return hits


@pytest.mark.parametrize("name,make", CIPHERS)
def test_a_real_cipher_round_trips_through_the_store(name, make, tmp_path):
    path = str(tmp_path / "c.db")
    log = _fill(path, make())

    assert log.encryption()["plaintext"] is False
    assert [c.text for c in log.read_range(0, 10 ** 15)] == \
        ["%s %d" % (NEEDLE, i) for i in range(3)], \
        "%s wrote claims it could not read back" % name
    assert len(log.all_claims()) == 3
    assert log.get(log.all_claims()[0].id) is not None
    assert log.verify_chain()["ok"], (
        "the hash chain must verify through real crypto — it hashes WIRE "
        "bytes, so a broken decrypt shows up here as content corruption")
    assert not _files_containing(path, NEEDLE), (
        "%s left the founder's screen text in the clear on disk" % name)


def test_the_leak_check_can_actually_see_a_leak(tmp_path):
    """THE CONTROL, and it is the point of this file.

    A "no plaintext found" result is worth nothing unless the search would
    have found plaintext had it been there. NullCrypto is the known-leaky
    case: it must trip the same check that the real ciphers pass. Without
    this test, a typo in the needle or a glob that matches no files would
    turn every assertion above into a tautology.
    """
    path = str(tmp_path / "c.db")
    _fill(path, None)                       # NullCrypto by default
    leaked = _files_containing(path, NEEDLE)
    assert leaked, (
        "the leak check found nothing even in a PLAINTEXT store — the check "
        "itself is broken, and every encryption assertion in this file is "
        "meaningless until it is fixed")


def test_only_the_cli_may_reach_the_real_keychain():
    """A REGRESSION GUARD, and it exists because this was broken once.

    The first draft of wiring._crypto_for fell back to MacKeychain()
    whenever the store lived on disk. Every entrypoint test uses an on-disk
    tmp_path store, so the suite immediately began reading the founder's
    real login keychain — the precise side effect keychain_mac.py's
    docstring forbids. It was caught only because the encrypted rows then
    failed to decode in tests that reopen the store plainly; had those
    tests not existed, the suite would have been quietly touching a
    person's keychain on every run.

    The rule: exactly one construction site, in the CLI, where a human
    invoked the program on purpose. Everything below it takes an injected
    keychain or gets NullCrypto and says so.
    """
    import ast

    # AST, not grep: the first version of this test grepped for the string
    # and matched its OWN source, and would equally have matched a mention
    # inside a docstring explaining the rule. Only real call sites count.
    root = Path(__file__).resolve().parents[2] / "src"
    sites = []
    for path in root.rglob("*.py"):
        if path.name == "keychain_mac.py":
            continue                                   # where it is defined
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "MacKeychain"):
                sites.append("%s:%d" % (path.name, node.lineno))

    assert sites == [s for s in sites if s.startswith("__main__.py")] and \
        len(sites) == 1, (
        "MacKeychain must be constructed in exactly ONE place — the CLI, "
        "where a human invoked the program on purpose. Found: %s" % sites)


def test_ciphertext_is_bound_to_its_own_row(tmp_path):
    """AAD = the claim id. A ciphertext lifted onto another claim's row must
    fail to authenticate rather than decrypt into a claim that was never
    made — otherwise 'raw is immutable' is satisfied by a store in which any
    two memories can be swapped."""
    path = str(tmp_path / "c.db")
    log = _fill(path, SodiumCrypto(b"k" * 32), n=2)
    ids = [c.id for c in log.all_claims()]

    rows = dict(log._db.execute("SELECT id, ciphertext FROM claim"))
    log._db.execute("UPDATE claim SET ciphertext=? WHERE id=?",
                    (rows[ids[1]], ids[0]))          # swap row 1's body onto row 0
    log._db.commit()
    with pytest.raises(AuthFailure):
        log.get(ids[0])


def test_a_plaintext_store_gains_the_nonce_column_without_touching_a_claim(tmp_path):
    """The nonce column arrived after stores existed. Migration must be
    ALTER TABLE ADD COLUMN — the one schema change SQLite makes without
    rewriting rows — so the hash chain stays valid across it. If migration
    ever rewrote claims, 'raw is immutable' would be false at exactly the
    moment nobody was looking."""
    path = str(tmp_path / "c.db")
    log = _fill(path, None)
    before = log.verify_chain()
    log._db.execute("ALTER TABLE claim RENAME TO claim_old")
    log._db.execute(
        "CREATE TABLE claim (id TEXT PRIMARY KEY, t_ms INTEGER NOT NULL, "
        "place_key TEXT, device_id TEXT NOT NULL, modality TEXT NOT NULL, "
        "grade INTEGER NOT NULL, bidder TEXT NOT NULL, bid_id TEXT NOT NULL, "
        "bid_predicted_value REAL NOT NULL, actual_cost REAL NOT NULL, "
        "scope TEXT NOT NULL CHECK (scope IN ('frame','memory')), "
        "ciphertext BLOB NOT NULL)")              # a pre-nonce store
    log._db.execute(
        "INSERT INTO claim SELECT id, t_ms, place_key, device_id, modality, "
        "grade, bidder, bid_id, bid_predicted_value, actual_cost, scope, "
        "ciphertext FROM claim_old")
    log._db.execute("DROP TABLE claim_old")
    log._db.commit()
    log._db.close()

    reopened = ClaimLog(path)                     # migration runs on open
    cols = {r[1] for r in reopened._db.execute("PRAGMA table_info(claim)")}
    assert "nonce" in cols
    assert len(reopened.all_claims()) == 3
    assert reopened.verify_chain()["ok"] is before["ok"] is True, \
        "the nonce migration disturbed the hash chain"


# -- FOUND BY USING THE PRODUCT (2026-07-28) ------------------------------
def test_a_mixed_store_opens_instead_of_killing_the_daemon(tmp_path):
    """THE DAEMON WOULD NOT START ON THE FOUNDER'S REAL STORE.

    data/trace_store.sqlite3 held 604 claims written plaintext plus claims
    written under a real cipher. `--ask` printed "STORE IS MIXED ... older
    claims CANNOT be read by this process" and then died one line later on
    ValueError: Nonce must be 8, 12 or 24 bytes long, because last_state()
    walks all_claims(). It knew, said so, and crashed anyway.

    A claim written under a key this process does not hold is not readable
    HERE. That is a fact to report, not a reason to lose the memory that IS
    readable."""
    path = str(tmp_path / "mixed.db")
    plain = _fill(path, NullCrypto(), n=3)          # 3 plaintext claims
    assert len(plain.all_claims()) == 3

    reopened = ClaimLog(path, crypto=SodiumCrypto(b"k" * 32))
    readable = reopened.all_claims()                # must not raise
    assert readable == []
    assert reopened.unreadable() == 3, "the gap must be COUNTED, not silent"


def test_a_mixed_store_still_serves_the_claims_it_can_read(tmp_path):
    """The readable half of a mixed store is the half a user still has."""
    path = str(tmp_path / "mixed2.db")
    key = b"k" * 32
    _fill(path, NullCrypto(), n=2)                  # 2 unreadable-under-key
    enc = ClaimLog(path, crypto=SodiumCrypto(key))
    enc.append(make_claim("readable one", t_ms=99_000, frame="f-enc"))

    fresh = ClaimLog(path, crypto=SodiumCrypto(key))
    got = fresh.all_claims()
    assert [c.text for c in got] == ["readable one"]
    assert fresh.unreadable() == 2


def test_tampering_is_still_loud_in_a_mixed_store(tmp_path):
    """The skip must NEVER swallow a tampered row. A wrong-SHAPED nonce
    proves a different cipher wrote it; a right-shaped nonce that fails to
    authenticate proves the row was altered, and that stays fatal."""
    path = str(tmp_path / "mixed3.db")
    key = b"k" * 32
    _fill(path, NullCrypto(), n=1)                  # a foreign-cipher row
    log = ClaimLog(path, crypto=SodiumCrypto(key))
    log.append(make_claim("mine", t_ms=5_000, frame="f-mine"))

    fresh = ClaimLog(path, crypto=SodiumCrypto(key))
    mine = [c for c in fresh.all_claims()][0]
    fresh._db.execute("UPDATE claim SET ciphertext=? WHERE id=?",
                      (b"\x00" * 64, mine.id))
    fresh._db.commit()
    with pytest.raises(AuthFailure):
        ClaimLog(path, crypto=SodiumCrypto(key)).get(mine.id)


def test_opening_an_encrypted_store_in_plaintext_mode_skips_instead_of_garbling(tmp_path):
    """The MIRROR of the mixed-store crash, and the one the founder's real
    store hit second. Reading an encrypted row with NullCrypto returns the
    raw ciphertext, which json.loads then dies on. The guard has to be
    two-sided: plaintext declares NONCE_LEN = 0, so a 24-byte nonce is
    recognised as somebody else's row rather than decoded as garbage."""
    path = str(tmp_path / "enc_then_plain.db")
    _fill(path, SodiumCrypto(b"k" * 32), n=2)

    plain = ClaimLog(path, crypto=NullCrypto())
    assert plain.all_claims() == []            # must not raise
    assert plain.unreadable() == 2


def test_a_plaintext_store_with_one_encrypted_row_still_reads_the_rest(tmp_path):
    """Exactly the founder's store: 604 plaintext claims plus one encrypted
    row left behind by a daemon that crashed mid-boot."""
    path = str(tmp_path / "mostly_plain.db")
    _fill(path, NullCrypto(), n=3)
    ClaimLog(path, crypto=SodiumCrypto(b"k" * 32)).append(
        make_claim("the intruder", t_ms=90_000, frame="f-x"))

    plain = ClaimLog(path, crypto=NullCrypto())
    texts = [c.text for c in plain.all_claims()]
    assert len(texts) == 3 and "the intruder" not in texts
    assert plain.unreadable() == 1


def test_an_existing_plaintext_store_is_opened_the_way_it_was_written(tmp_path):
    """Applying the encrypt-by-default rule to a store that ALREADY exists in
    the clear does not encrypt it — it locks the user out of it. The
    founder's real store held 604 plaintext claims and the daemon opened it
    with SodiumCrypto, announced "older claims CANNOT be read", and crashed
    on the first one."""
    from tracemem.app.wiring import _crypto_for
    path = str(tmp_path / "plain.db")
    _fill(path, NullCrypto(), n=2)

    chosen = _crypto_for({"keychain": object()}, path)
    assert chosen is None, "a plaintext store was reopened under a cipher"


def test_an_existing_encrypted_store_is_never_downgraded(tmp_path):
    """The rule reads the store's OWN record, so it can only ever decline to
    lock someone out — never open an encrypted store in the clear."""
    from tracemem.app.wiring import _created_with
    path = str(tmp_path / "enc.db")
    _fill(path, SodiumCrypto(b"k" * 32), n=1)
    assert _created_with(path) == "SodiumCrypto"


def test_a_brand_new_store_still_gets_the_encrypting_default(tmp_path):
    """`created_with` only exists once something has been written, so a store
    that does not exist yet cannot be silently downgraded."""
    from tracemem.app.wiring import _created_with
    assert _created_with(str(tmp_path / "not_created_yet.db")) is None
