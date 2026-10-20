"""OWNS: Specialist ABC: load/evict/read lifecycle

SPEC: 01 s8-9
ORACLE: base contract lifecycle
"""
from __future__ import annotations

class SpecialistBase:
    def load(self):
        raise NotImplementedError

    def evict(self):
        raise NotImplementedError

    def read(self, frame):
        raise NotImplementedError
