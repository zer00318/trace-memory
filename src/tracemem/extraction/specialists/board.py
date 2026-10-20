"""OWNS: chess board -> FEN (pretrained board recognizer)

SPEC: 01 s9
ORACLE: golden board image -> expected FEN; grade == SPECIALIST
"""
from __future__ import annotations

class BoardEye:
    def load(self):
        raise NotImplementedError

    def evict(self):
        raise NotImplementedError

    def read(self, frame):
        raise NotImplementedError
