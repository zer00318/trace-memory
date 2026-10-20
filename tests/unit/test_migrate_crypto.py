"""ENCRYPTING A STORE THAT ALREADY EXISTS (priority MASTER #5).

`store_meta.cipher = 'none'` and 20,000+ claims of the founder's screen sit on
disk in the clear. It is not a flag: `wiring._crypto_for` deliberately opens a
pre-existing plaintext store IN THE CLEAR, and that is correct — applying the
encryption default to a clear store does not encrypt it, it makes it
unreadable. So the only honest route is to re-write every claim under a key.

THE PROPERTIES THESE TESTS HOLD, each of which is a way a migration can ruin
somebody's memory while reporting success:

  1. **the original is never written to** — the new store is built beside it
     and only swapped in after full verification;
  2. **the swap cannot lose writes** — the store is WAL-mode, and the first
     version of this moved only the main file. It reported 20,609 claims (read
     through its own still-open handle, which sees its own WAL) while the file
     that landed held 20,591 and no quarantine table. Eighteen claims gone
     under a green report;
  3. **rows this key cannot read are preserved, not dropped** — raw is
     retained forever, and a migration is the last place to make an exception;
  4. **and they stay OUT of the hash chain** — nobody can hash bytes they
     cannot decrypt, and a permanently-red integrity signal is one everybody
     learns to ignore.
"""
import sqlite3

import pytest

from tests.helpers import make_claim
from tracemem.container.crypto import SodiumCrypto
from tracemem.text.claim_log import ClaimLog
from tracemem.text.migrate_crypto import (MigrationFailed, MigrationRefused,
                                          migrate, plan)

KEY = b"\x22" * 32


def _store(path, n=6):
    log = ClaimLog(str(path))
    for i in range(n):
        log.append(make_claim(text="claim number %d" % i, t_ms=1000 + i,
                              place="dev:mac-1", frame="f%d" % i,
                              app="Brave", perceiver="ocr"))
    return log


# ---- it actually encrypts -------------------------------------------------

def test_the_claims_come_back_only_with_the_key(tmp_path):
    """THE ORACLE. Before: anyone holding the file can read the screen."""
    p = tmp_path / "s.sqlite3"
    _store(p)

    migrate(str(p), SodiumCrypto(KEY), backup_dir=str(tmp_path / "b"))

    assert len(list(ClaimLog(str(p)).all_claims())) == 0, "readable with no key"
    keyed = ClaimLog(str(p), crypto=SodiumCrypto(KEY))
    assert len(list(keyed.all_claims())) == 6
    assert keyed.encryption()["plaintext"] is False


def test_the_text_is_no_longer_sitting_in_the_file(tmp_path):
    """Not 'the metadata says encrypted' — the bytes themselves."""
    p = tmp_path / "s.sqlite3"
    _store(p)
    before = p.read_bytes().count(b"claim number 3")
    assert before >= 1, "the fixture must actually contain the text"

    migrate(str(p), SodiumCrypto(KEY), backup_dir=str(tmp_path / "b"))

    assert p.read_bytes().count(b"claim number 3") == 0


def test_the_hash_chain_survives_encryption(tmp_path):
    """The chain hashes the WIRE bytes, not the ciphertext, so an encrypted
    store must reproduce it exactly. A chain that changed would mean the
    CONTENT changed."""
    p = tmp_path / "s.sqlite3"
    _store(p)

    rep = migrate(str(p), SodiumCrypto(KEY), backup_dir=str(tmp_path / "b"))

    assert rep.chain_ok and rep.chain_complete
    assert ClaimLog(str(p), crypto=SodiumCrypto(KEY)).verify_chain()["ok"]


def test_what_lands_on_disk_is_what_was_verified(tmp_path):
    """PROPERTY 2, and it cost a silent loss of 18 real claims to find. Every
    check inside migrate() runs against an open handle; this reads the file
    the way a NEW process will see it tomorrow."""
    p = tmp_path / "s.sqlite3"
    _store(p, n=40)

    rep = migrate(str(p), SodiumCrypto(KEY), backup_dir=str(tmp_path / "b"))

    fresh = ClaimLog(str(p), crypto=SodiumCrypto(KEY))
    assert len(list(fresh.all_claims())) == rep.claims_in == 40


# ---- nothing is ever lost -------------------------------------------------

def test_the_plaintext_original_is_kept_not_removed(tmp_path):
    p = tmp_path / "s.sqlite3"
    _store(p)

    migrate(str(p), SodiumCrypto(KEY), backup_dir=str(tmp_path / "b"))

    aside = tmp_path / "s.sqlite3.plaintext"
    assert aside.exists()
    assert len(list(ClaimLog(str(aside)).all_claims())) == 6


def test_a_verified_backup_is_taken_before_anything_is_written(tmp_path):
    p = tmp_path / "s.sqlite3"
    _store(p)

    rep = migrate(str(p), SodiumCrypto(KEY), backup_dir=str(tmp_path / "b"))

    assert len(list(ClaimLog(rep.backup).all_claims())) == 6


def test_a_row_this_key_cannot_read_is_quarantined_not_dropped(tmp_path):
    """The founder's real store has exactly one — a claim written under a
    different cipher during an earlier experiment. Refusing to migrate over it
    would leave 20,000 claims in the clear forever; dropping it would break
    the law the whole codebase rests on."""
    p = tmp_path / "s.sqlite3"
    _store(p)
    # An orphan: a row plaintext cannot open.
    foreign = ClaimLog(str(tmp_path / "other.sqlite3"), crypto=SodiumCrypto(b"\x99" * 32))
    foreign.append(make_claim(text="unreadable one", t_ms=999, place="dev:mac-1",
                              frame="x", app="Brave", perceiver="ocr"))
    con = sqlite3.connect(str(p))
    src = sqlite3.connect(str(tmp_path / "other.sqlite3"))
    cols = [r[1] for r in src.execute("PRAGMA table_info(claim)")]
    row = src.execute("SELECT %s FROM claim" % ", ".join(cols)).fetchone()
    con.execute("INSERT INTO claim (%s) VALUES (%s)"
                % (", ".join(cols), ", ".join("?" * len(cols))), row)
    con.commit()
    con.close()

    rep = migrate(str(p), SodiumCrypto(KEY), backup_dir=str(tmp_path / "b"))

    kept = sqlite3.connect(str(p)).execute(
        "SELECT COUNT(*) FROM quarantined_claim").fetchone()[0]
    assert kept == 1, "an unreadable row must be preserved, never dropped"
    assert rep.chain_ok, "and it must not poison the chain"
    assert any("quarantined" in n for n in rep.notes), "and it must be SAID"


# ---- it refuses rather than guessing --------------------------------------

def test_it_refuses_while_a_daemon_is_running(tmp_path):
    """The store has ONE writer. Migrating underneath live capture would race
    the hash chain."""
    p = tmp_path / "s.sqlite3"
    _store(p)

    with pytest.raises(MigrationRefused) as e:
        migrate(str(p), SodiumCrypto(KEY), backup_dir=str(tmp_path / "b"),
                daemon_running=lambda: True)

    assert "daemon" in str(e.value).lower()
    assert ClaimLog(str(p)).count() == 6, "and nothing was touched"


def test_it_refuses_to_re_encrypt_an_encrypted_store(tmp_path):
    p = tmp_path / "s.sqlite3"
    _store(p)
    migrate(str(p), SodiumCrypto(KEY), backup_dir=str(tmp_path / "b"))

    with pytest.raises(MigrationRefused):
        migrate(str(p), SodiumCrypto(KEY), backup_dir=str(tmp_path / "b2"))


def test_it_refuses_a_null_cipher(tmp_path):
    """Rewriting the whole store to achieve nothing is not a migration."""
    p = tmp_path / "s.sqlite3"
    _store(p)

    with pytest.raises(MigrationRefused):
        migrate(str(p), None, backup_dir=str(tmp_path / "b"))


def test_plan_changes_nothing_and_says_what_would_happen(tmp_path):
    p = tmp_path / "s.sqlite3"
    _store(p)

    got = plan(str(p))

    assert got["ok"] is True and got["claims"] == 6
    assert "PLAINTEXT" in got["why"]
    assert ClaimLog(str(p)).encryption()["plaintext"] is True


def test_this_module_never_reaches_for_a_keychain():
    """The cipher is INJECTED. Exactly one construction site for the real
    keychain exists — the CLI, where a human invoked the program on purpose."""
    import inspect

    from tracemem.text import migrate_crypto

    src = inspect.getsource(migrate_crypto)
    assert "MacKeychain" not in src
    assert "Keyring(" not in src
