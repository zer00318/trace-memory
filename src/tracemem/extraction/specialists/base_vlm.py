"""OWNS: generic fallback read for activities without a specialist (P2.4)

SPEC: 01 s9
ORACLE: any frame -> caption claims
"""
from __future__ import annotations

class BaseVLMEye:
    def load(self):
        raise NotImplementedError

    def evict(self):
        raise NotImplementedError

    def read(self, frame):
        raise NotImplementedError
