"""One-shot Keychain port for the signed helper's inherited secret pipe.

The native TRACE executable owns macOS Keychain access. It gives the bundled
Python runtime the existing 32-byte master over an anonymous inherited file
descriptor. The key is never placed in argv, the environment, a file, or a
local socket another process could connect to.
"""
from __future__ import annotations

import os


class InheritedKeychain:
    def __init__(self, fd: int, expected: int = 32) -> None:
        chunks = []
        remaining = expected
        try:
            while remaining:
                chunk = os.read(fd, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
        master = b"".join(chunks)
        if len(master) != expected:
            raise RuntimeError(
                "signed helper supplied %d key bytes; expected %d" %
                (len(master), expected))
        self._master = master

    def get(self, slot: str):
        if slot != "tracemem.master.v1":
            return None
        return self._master

    def set(self, slot: str, value: bytes) -> None:
        raise RuntimeError(
            "the signed helper owns Keychain writes; inherited runtime is read-only")

