"""ORACLE for the Hash chain atom (03_text > Immutability enforcement).

PROOF, as the map states it: "Flip one byte in a closed segment;
verification names the exact claim."

The word that matters there is NAMES. A checker that reports "this store is
corrupt" has told you almost nothing — you cannot tell whether one row rotted
or the whole thing is worthless, and the honest product answer ("what did I
lose?") stays unanswerable. So every test here asserts the IDENTITY of the
broken claim, not merely that something broke.

The chain protects against disk corruption, partial writes, truncated
restores, and targeted edits. It does NOT protect against an attacker who
holds the key and rewrites every link forward — see the note in claim_log.py.
The last test in this file pins that limitation in place so no future reader
mistakes the chain for a signature.
"""
import sqlite3

import pytest

from tests.helpers import make_claim
from tracemem.text.claim_log import ClaimLog


def _log(tmp_path, n=5):
    log = ClaimLog(str(tmp_path / "chain.sqlite3"))
    for i in range(n):
        log.append(make_claim(text="claim %d" % i, t_ms=1000 + i,
                              frame="f-%d" % i))
    return log


def test_a_fresh_chain_verifies(tmp_path):
    log = _log(tmp_path)
    report = log.verify_chain()
    assert report["ok"] is True
    assert report["broken_at"] is None
    assert report["checked"] == 5


def test_an_empty_store_verifies(tmp_path):
    """Nothing to walk is not the same as broken."""
    log = ClaimLog(str(tmp_path / "empty.sqlite3"))
    report = log.verify_chain()
    assert report["ok"] is True
    assert report["checked"] == 0


def test_the_first_claim_links_to_genesis(tmp_path):
    """The head of the chain has a defined predecessor, not NULL — otherwise
    'the first claim was deleted' and 'this is the first claim' look alike."""
    from tracemem.text.claim_log import _GENESIS
    log = _log(tmp_path, n=1)
    row = log._db.execute("SELECT prev_link, link FROM claim_link").fetchone()
    assert row[0] == _GENESIS
    assert row[1] and row[1] != _GENESIS


def test_the_seal_lives_outside_the_raw_table(tmp_path):
    """Structural, not stylistic: if the links were columns on `claim`, then
    sealing anything would require an UPDATE against raw, and the one-door
    property that makes raw immutable would be gone. The claim table must
    carry no chain columns at all."""
    log = _log(tmp_path, n=1)
    cols = {r[1] for r in log._db.execute("PRAGMA table_info(claim)")}
    assert "link" not in cols and "prev_link" not in cols
    link_cols = {r[1] for r in log._db.execute("PRAGMA table_info(claim_link)")}
    assert {"id", "seq", "prev_link", "link"} <= link_cols


def test_a_pre_chain_store_is_reported_unchained_not_crashed(tmp_path):
    """A store written before this atom has claims and no links. That is not
    corruption and it is not a guarantee — it must be named as its own thing,
    and it must not take the process down."""
    log = _log(tmp_path, n=3)
    ids = log.ids_in_range(0, 10 ** 12)
    log._db.execute("DELETE FROM claim_link")   # simulate a pre-chain store
    log._db.commit()

    report = log.verify_chain()
    assert report["ok"] is False
    assert report["reason"] == "unchained"
    assert report["broken_at"] == ids[0]

    assert log.rebuild_chain() == 3             # the deliberate, loud repair
    assert log.verify_chain()["ok"] is True


def test_flipping_one_byte_names_the_exact_claim(tmp_path):
    """THE ORACLE. Tamper with claim #2 of 5; verification must name #2 —
    not #0, not 'somewhere', not just False."""
    log = _log(tmp_path)
    ids = log.ids_in_range(0, 10 ** 12)
    victim = ids[2]

    # Flip one byte of the stored ciphertext, exactly as disk rot would.
    row = log._db.execute(
        "SELECT ciphertext FROM claim WHERE id=?", (victim,)).fetchone()
    blob = bytearray(row[0])
    blob[0] ^= 0x01
    log._db.execute("UPDATE claim SET ciphertext=? WHERE id=?",
                    (bytes(blob), victim))
    log._db.commit()

    report = log.verify_chain()
    assert report["ok"] is False
    assert report["broken_at"] == victim
    assert report["reason"] == "content"


def test_a_severed_link_names_the_claim_whose_parent_vanished(tmp_path):
    """Deleting a row is the other realistic corruption: a truncated restore.
    The claim AFTER the hole is the one that can prove something is missing."""
    log = _log(tmp_path)
    ids = log.ids_in_range(0, 10 ** 12)
    log._db.execute("DELETE FROM claim WHERE id=?", (ids[1],))
    log._db.commit()

    report = log.verify_chain()
    assert report["ok"] is False
    assert report["broken_at"] == ids[2]
    assert report["reason"] == "link"


def test_verification_stops_at_the_first_break_and_says_how_far_it_got(tmp_path):
    """Everything before the break is still trustworthy, and the report says
    so — a corrupt Tuesday must not cost you Monday."""
    log = _log(tmp_path, n=6)
    ids = log.ids_in_range(0, 10 ** 12)
    log._db.execute("UPDATE claim_link SET link='deadbeef' WHERE id=?", (ids[4],))
    log._db.commit()

    report = log.verify_chain()
    assert report["ok"] is False
    assert report["broken_at"] == ids[4]
    assert report["checked"] == 4  # ids[0..3] verified before the break


def test_the_tail_check_is_cheap_and_catches_a_fresh_break(tmp_path):
    """Verify-on-open walks the TAIL (bounded work at every boot); the deep
    walk is the nightly job. A break inside the tail window must be caught by
    the cheap path, or boot-time verification is decorative."""
    log = _log(tmp_path, n=50)
    ids = log.ids_in_range(0, 10 ** 12)
    log._db.execute("UPDATE claim_link SET link='deadbeef' WHERE id=?", (ids[47],))
    log._db.commit()

    report = log.verify_chain(tail=5)
    assert report["ok"] is False
    assert report["broken_at"] == ids[47]
    assert report["checked"] <= 5


def test_the_tail_check_does_not_claim_to_have_checked_the_whole_store(tmp_path):
    """The cheap path must never report a clean bill of health for rows it
    never read — that is exactly the silent lie the chain exists to prevent."""
    log = _log(tmp_path, n=50)
    report = log.verify_chain(tail=5)
    assert report["ok"] is True
    assert report["checked"] == 5
    assert report["complete"] is False

    deep = log.verify_chain()
    assert deep["complete"] is True
    assert deep["checked"] == 50


def test_re_appending_the_same_claim_does_not_extend_the_chain(tmp_path):
    """Append is idempotent (T2). If a duplicate append advanced the chain,
    every retry would forge a break in a store that is perfectly intact."""
    log = ClaimLog(str(tmp_path / "idem.sqlite3"))
    c = make_claim(text="once", t_ms=1000)
    log.append(c)
    head_after_first = log.chain_head()
    log.append(c)
    log.append(c)

    assert log.count() == 1
    assert log.chain_head() == head_after_first
    assert log.verify_chain()["ok"] is True


def test_the_chain_survives_close_and_reopen(tmp_path):
    """The link of the last row is read back from disk, not held in memory —
    otherwise the chain restarts at genesis on every boot and seals nothing."""
    path = str(tmp_path / "reopen.sqlite3")
    first = ClaimLog(path)
    for i in range(3):
        first.append(make_claim(text="a%d" % i, t_ms=1000 + i, frame="f%d" % i))
    head = first.chain_head()

    second = ClaimLog(path)
    second.append(make_claim(text="after reopen", t_ms=2000, frame="f-later"))
    report = second.verify_chain()
    assert report["ok"] is True
    assert report["checked"] == 4
    assert second.chain_head() != head


def test_the_chain_is_not_a_signature(tmp_path):
    """The documented limitation, pinned as a test so it cannot quietly rot
    into a false security claim: an attacker who rewrites a claim AND every
    link after it produces a store that verifies. The chain makes tampering
    expensive and visible-if-partial; it does not make it impossible."""
    log = _log(tmp_path, n=4)
    ids = log.ids_in_range(0, 10 ** 12)

    # Forge: rewrite claim #1's content, then recompute every link forward.
    row = log._db.execute("SELECT ciphertext FROM claim WHERE id=?",
                          (ids[1],)).fetchone()
    blob = bytearray(row[0])
    blob[0] ^= 0x01
    log._db.execute("UPDATE claim SET ciphertext=? WHERE id=?",
                    (bytes(blob), ids[1]))
    log._db.commit()
    assert log.verify_chain()["ok"] is False  # partial tamper is caught

    log.rebuild_chain()  # what a full-control attacker can do
    assert log.verify_chain()["ok"] is True   # ...and it verifies. Honestly.


def test_rebuild_is_not_reachable_by_accident(tmp_path):
    """rebuild_chain exists for migration of pre-chain stores and for the
    honesty test above. It erases tamper evidence, so it must be loud."""
    import inspect
    from tracemem.text.claim_log import ClaimLog as CL
    doc = inspect.getdoc(CL.rebuild_chain) or ""
    assert "DESTROYS" in doc or "destroys" in doc


def test_the_store_still_refuses_updates_to_raw(tmp_path):
    """Adding chain columns must not have opened a write path to raw content.
    The public API surface stays free of mutating verbs (T1)."""
    public = [n for n in dir(ClaimLog) if not n.startswith("_")]
    forbidden = ("update", "delete", "edit", "set_text", "overwrite")
    offenders = [n for n in public
                 if any(v in n.lower() for v in forbidden)]
    assert offenders == []


# -- plaintext must never be silent (2026-07-23) --------------------------
def test_a_store_records_that_it_is_plaintext(tmp_path):
    """The default is NullCrypto, and that default is how every live capture
    run in this project's history wrote a person's screen to disk in the
    clear — unnoticed, while a thousand green tests said nothing."""
    log = ClaimLog(str(tmp_path / "clear.sqlite3"))
    got = log.encryption()
    assert got["plaintext"] is True
    assert got["cipher"] == "none"
    assert got["mixed"] is False


def test_the_cipher_is_readable_without_any_key(tmp_path):
    """Anyone holding the file can ask whether it is readable. That question
    must not itself require the key."""
    path = str(tmp_path / "c.sqlite3")
    ClaimLog(path).append(make_claim(text="secret", t_ms=1))

    import sqlite3
    raw = sqlite3.connect(path)
    row = raw.execute("SELECT value FROM store_meta WHERE key='cipher'").fetchone()
    assert row[0] == "none"


def test_a_real_cipher_is_named_rather_than_assumed(tmp_path):
    class PretendCipher:
        def encrypt(self, header, plaintext):
            return b"", bytes(b ^ 0x2A for b in plaintext)

        def decrypt(self, header, nonce, ciphertext):
            return bytes(b ^ 0x2A for b in ciphertext)

    log = ClaimLog(str(tmp_path / "enc.sqlite3"), crypto=PretendCipher())
    got = log.encryption()
    assert got["plaintext"] is False
    assert got["cipher"] == "PretendCipher"


def test_a_store_created_in_the_clear_cannot_later_claim_encryption(tmp_path):
    """MIXED CRYPTO IS THE DANGEROUS CASE: a store that looks fine and is
    partly unreadable is indistinguishable from memory loss."""
    class PretendCipher:
        def encrypt(self, header, plaintext):
            return b"", plaintext

        def decrypt(self, header, nonce, ciphertext):
            return ciphertext

    path = str(tmp_path / "mixed.sqlite3")
    ClaimLog(path).append(make_claim(text="written in the clear", t_ms=1))

    reopened = ClaimLog(path, crypto=PretendCipher())
    got = reopened.encryption()
    assert got["mixed"] is True
    assert got["created_with"] == "none"
    assert got["cipher"] == "PretendCipher"


def test_reopening_with_the_same_cipher_is_not_mixed(tmp_path):
    path = str(tmp_path / "same.sqlite3")
    ClaimLog(path).append(make_claim(text="a", t_ms=1))
    assert ClaimLog(path).encryption()["mixed"] is False
