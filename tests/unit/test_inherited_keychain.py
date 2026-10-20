import os

import pytest

from tracemem.container.inherited_keychain import InheritedKeychain


def _pipe(payload):
    read_fd, write_fd = os.pipe()
    os.write(write_fd, payload)
    os.close(write_fd)
    return read_fd


def test_signed_helper_pipe_supplies_only_the_master_slot():
    master = bytes(range(32))
    keychain = InheritedKeychain(_pipe(master))
    assert keychain.get("tracemem.master.v1") == master
    assert keychain.get("some.other.slot") is None


def test_short_inherited_key_is_refused_and_fd_is_closed():
    fd = _pipe(b"short")
    with pytest.raises(RuntimeError, match="5 key bytes"):
        InheritedKeychain(fd)
    with pytest.raises(OSError):
        os.read(fd, 1)


def test_python_runtime_cannot_replace_the_scoped_key():
    keychain = InheritedKeychain(_pipe(b"x" * 32))
    with pytest.raises(RuntimeError, match="helper owns"):
        keychain.set("tracemem.master.v1", b"y" * 32)

