#!/usr/bin/env python3
"""Apply quarantined late boundaries as one durable projection version."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tracemem.app.daemon import SingleInstanceLock  # noqa: E402
from tracemem.app.wiring import _crypto_for, _graph_key_for  # noqa: E402
from tracemem.binder.reconcile import reconcile_pending  # noqa: E402
from tracemem.nodes.sqlite_graph_store import SqliteGraphStore  # noqa: E402
from tracemem.text.claim_log import ClaimLog  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Version late episode boundaries without rewriting raw")
    p.add_argument("--store", default=str(ROOT / "data" / "trace_store.sqlite3"))
    p.add_argument("--graph", default=str(ROOT / "data" / "trace_graph.sqlite3"))
    p.add_argument("--lock", default=str(ROOT / "data" / "trace.lock"))
    p.add_argument("--plaintext", action="store_true")
    args = p.parse_args(argv)

    lock = SingleInstanceLock(args.lock)
    if not lock.acquire():
        print("refusing: TRACE is running; stop it before reconciling", file=sys.stderr)
        return 2
    try:
        keychain = None
        if not args.plaintext:
            from tracemem.container.keychain_mac import MacKeychain
            keychain = MacKeychain()
        crypto = _crypto_for(
            {} if keychain is None else {"keychain": keychain}, args.store)
        raw = ClaimLog(args.store, crypto=crypto)
        graph = SqliteGraphStore(
            args.graph, key=_graph_key_for(
                {} if keychain is None else {"keychain": keychain}, args.graph))
        report = reconcile_pending(graph, raw)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
