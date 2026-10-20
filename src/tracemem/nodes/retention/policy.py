"""OWNS: the tunable forgetting knobs — eager default, decide-from-data (P5.6)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetentionPolicy:
    cold_floor: float = 0.05        # below this current activation = cold
    eager: bool = True              # eager: prune re-derivable cold meta freely
