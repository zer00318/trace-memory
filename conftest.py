import sys
from pathlib import Path

# Path-inject src/ (the package) and the tracemem root (tests.helpers) so the
# pinned .venv needs no install step (spec 09 A8).
_root = Path(__file__).resolve().parent
sys.path.insert(0, str(_root / "src"))
sys.path.insert(0, str(_root))

# WO-080's second clause: "the whole existing suite runs against BOTH
# stores". Rather than edit 36 construction sites — a large, noisy diff whose
# risk is entirely in the places it MISSES — the backend is swapped here, at
# the one point that runs before any test module is imported.
#
#     TRACE_GRAPH_STORE=sqlite .venv/bin/python -m pytest tests -q
#
# This works precisely because conftest is imported first: test modules doing
# `from tracemem.nodes.graph_store import MemoryGraphStore` bind whatever
# this leaves behind. A test that constructs the class by any other route is
# NOT covered by the switch, which is why the conformance suite exists
# alongside it rather than instead of it.
import os  # noqa: E402

if os.environ.get("TRACE_GRAPH_STORE") == "sqlite":
    from tracemem.nodes import graph_store as _graph_store  # noqa: E402
    from tracemem.nodes.sqlite_graph_store import SqliteGraphStore  # noqa: E402

    _graph_store.MemoryGraphStore = lambda *a, **kw: SqliteGraphStore(":memory:")
