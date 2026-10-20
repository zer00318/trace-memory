"""A reader of an existing memory must never mint a key (2026-10-20).

The falsification tool asked the legacy Keychain item the key had been moved
out of; Keyring then minted a fresh random master: a wrong key planted in the
login keychain, and an experiment that read nothing.
"""
from __future__ import annotations

import sqlite3

import pytest

from tracemem.container.keyring import KeyMissing, Keyring


class Chain:
    def __init__(self):
        self.slots = {}

    def get(self, k):
        return self.slots.get(k)

    def set(self, k, v):
        self.slots[k] = v


def test_a_reader_with_no_key_fails_loudly_and_writes_nothing():
    chain = Chain()
    with pytest.raises(KeyMissing):
        Keyring(chain, create=False)
    assert chain.slots == {}, "a reader must never plant a key"


def test_first_time_setup_still_creates_one():
    chain = Chain()
    Keyring(chain)
    assert len(chain.slots) == 1


def test_the_experiment_refuses_an_encrypted_store_without_the_apps_key(tmp_path):
    from tracemem.evalx import falsify_cli
    store = tmp_path / "s.sqlite3"
    con = sqlite3.connect(str(store))
    con.execute("create table store_meta (key text, value text)")
    con.execute("insert into store_meta values ('cipher', 'SodiumCrypto')")
    con.commit(); con.close()
    falsify_cli._KEYCHAIN = None
    with pytest.raises(SystemExit) as e:
        falsify_cli._crypto_for(store)
    assert "./trace falsify" in str(e.value)
