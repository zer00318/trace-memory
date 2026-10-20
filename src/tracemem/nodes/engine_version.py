"""OWNS: the fingerprint of the code that DERIVES — the night's own vintage.

WHY A FINGERPRINT AND NOT `__version__`. A hand-bumped version string records
that somebody remembered to bump it. The thing the night actually needs to
know is whether the machinery that turns evidence into beliefs is the same
machinery that ran last time, and that question has an exact answer available
for free: the bytes of the modules that do the deriving.

WHAT IT IS FOR. `app/night_watchman._world_mark` includes this so that a
change to the derivation engine counts as a change to the world. Without it
the night's stand-down check asks "has new evidence arrived?" and its caller
reads the answer as "is there anything left to think about?" — the same
sentence only while the engine is frozen. On the founder's real graph those
two questions disagreed for two days and a written, tested repair could not
run. See the long note at `_world_mark`.

WHAT IS AND IS NOT IN THE SET. Only modules that decide what a belief IS or
whether it may exist: the four combination kinds, the judge and its
guardrails, candidate selection, the night's own order of passes, and the
re-marination passes that rewrite or retire what is already there. Retrieval,
packing, prompts and faces are deliberately absent — changing how a belief is
SHOWN is not a reason to re-derive it, and putting them in here would make
every UI edit trigger a full re-think.

CHEAP AND ONCE. Seven small files, read at first call and cached for the life
of the process. A missing file contributes its name rather than raising: this
is a cache key, and a fingerprint that crashes the night it is meant to
schedule would be a poor trade.
"""
from __future__ import annotations

import hashlib
import os
from typing import Optional

# The modules whose bytes decide what the graph BELIEVES. Paths are relative
# to the `tracemem` package root.
_ENGINE = (
    "nodes/combination/kinds/pattern.py",
    "nodes/combination/kinds/transitive.py",
    "nodes/combination/kinds/causal.py",
    "nodes/combination/kinds/contradiction.py",
    "nodes/combination/judge.py",
    "nodes/combination/candidate_selector.py",
    "nodes/marination/nightly.py",
    "nodes/marination/mining.py",
    "nodes/marination/relabel.py",
    "nodes/marination/schemas.py",
)

_CACHED: Optional[str] = None


def engine_fingerprint() -> str:
    """A short, stable hash of the derivation engine's source. Never raises."""
    global _CACHED
    if _CACHED is not None:
        return _CACHED
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    h = hashlib.sha256()
    for rel in _ENGINE:
        h.update(rel.encode())
        path = os.path.join(here, *rel.split("/"))
        try:
            with open(path, "rb") as fh:
                h.update(fh.read())
        except OSError:
            # A module that cannot be read still changes the fingerprint when
            # it appears or disappears, which is the honest signal.
            h.update(b"<unreadable>")
    _CACHED = h.hexdigest()[:12]
    return _CACHED
