"""OWNS: code screen -> lang+symbols

SPEC: 01 s9
ORACLE: sample -> structured {lang, symbols}
"""
from __future__ import annotations

class CodeEye:
    def load(self):
        raise NotImplementedError

    def evict(self):
        raise NotImplementedError

    def read(self, frame):
        raise NotImplementedError
