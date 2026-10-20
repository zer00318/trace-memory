"""OWNS: dense-document read

SPEC: 01 s9
ORACLE: sample -> text blocks
"""
from __future__ import annotations

class DocumentEye:
    def load(self):
        raise NotImplementedError

    def evict(self):
        raise NotImplementedError

    def read(self, frame):
        raise NotImplementedError
