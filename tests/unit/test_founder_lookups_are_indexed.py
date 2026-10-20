"""Founder lookups must not decrypt the whole store.

WITNESSED 2026-10-07 on the founder's 635k-claim store: every question ran
`find_corrections`, which scanned `all_claims()` — decrypting every claim on
the one thread that answers — so every ask timed out at 120s. Only founder
claims can be corrections or past asks, and `bidder` is an indexed
plaintext column.
"""
from tracemem.app.founder import ask_claim, grade_claim
from tracemem.supplier.corrections import find_corrections
from tracemem.supplier.history import find_asks
from tracemem.text.claim_log import ClaimLog
from tests.helpers import make_claim


class _Line:
    id = "line-1"
    node_id = "node-1"
    text = "you were watching chess"


def _store(tmp_path):
    log = ClaimLog(str(tmp_path / "s.sqlite3"))
    for i in range(500):
        log.append(make_claim(text="screen text %d about chess" % i,
                              t_ms=1000 + i, frame="f-%d" % i))
    log.append(grade_claim(_Line(), "wrong", "I was reviewing a chess game",
                           t_ms=9000))
    log.append(ask_claim("what did I do about chess?", t_ms=9500))
    opened = {"n": 0}
    real = log._try_open

    def counting(*a, **k):
        opened["n"] += 1
        return real(*a, **k)

    log._try_open = counting
    return log, opened


def test_corrections_open_only_founder_claims(tmp_path):
    log, opened = _store(tmp_path)
    got = find_corrections(log, "what was I doing with chess?")
    assert [c.text for c in got] == [
        "I was reviewing a chess game (about: you were watching chess)"]
    assert opened["n"] <= 5, "opened %d claims for 2 founder rows" % opened["n"]


def test_history_opens_only_founder_claims(tmp_path):
    log, opened = _store(tmp_path)
    got = find_asks(log, "chess")
    assert len(got) == 1
    assert opened["n"] <= 5


def test_a_range_read_decrypts_each_claim_once_and_never_across_keys(tmp_path):
    """2026-10-09: "today" decrypted ~31k claims on every ask (~10s CPU)."""
    import os
    from tracemem.container.crypto import SodiumCrypto
    from tracemem.text.claim_log import ClaimLog
    from tests.helpers import make_claim

    path = str(tmp_path / "s.sqlite3")
    key = os.urandom(32)
    log = ClaimLog(path, crypto=SodiumCrypto(key))
    for i in range(5):
        log.append(make_claim(text="t%d" % i, t_ms=1000 + i, frame="f%d" % i))

    reader = ClaimLog(path, crypto=SodiumCrypto(key))
    opened = []
    real = reader._try_open
    reader._try_open = lambda *a: opened.append(a[0]) or real(*a)
    assert [c.text for c in reader.read_range(0, 10**9)] == \
        ["t%d" % i for i in range(5)]
    assert opened == [], "claims appended in this process are already warm"

    # a reader with a different key gets no plaintext from the cache: it
    # reaches the cipher, which refuses (wrong key reads as tampering)
    import pytest
    from tracemem.container.crypto import AuthFailure
    stranger = ClaimLog(path, crypto=SodiumCrypto(os.urandom(32)))
    with pytest.raises(AuthFailure):
        stranger.read_range(0, 10**9)
