#!/usr/bin/env python3
"""Build the non-destructive v1 -> v2 identity audit projection."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tracemem.app.daemon import SingleInstanceLock  # noqa: E402
from tracemem.app.wiring import _crypto_for  # noqa: E402
from tracemem.text.claim_log import ClaimLog  # noqa: E402


def audit(log: ClaimLog, batch: int = 5000, progress=None) -> dict:
    """Run resumably until every surviving legacy row has an audit record."""
    while True:
        report = log.audit_legacy_identities(limit=batch)
        if progress is not None:
            progress(report)
        if report["remaining"] == 0 or report["added"] == 0:
            return report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Audit legacy claim identities without rewriting raw rows")
    p.add_argument("--store", default=str(ROOT / "data" / "trace_store.sqlite3"))
    p.add_argument("--lock", default=str(ROOT / "data" / "trace.lock"))
    p.add_argument("--batch", type=int, default=5000)
    p.add_argument("--plaintext", action="store_true",
                   help="open with no key (only for a store recorded plaintext)")
    args = p.parse_args(argv)

    lock = SingleInstanceLock(args.lock)
    if not lock.acquire():
        print("refusing: TRACE is running; stop it before writing audit metadata",
              file=sys.stderr)
        return 2
    try:
        keychain = None
        if not args.plaintext:
            from tracemem.container.keychain_mac import MacKeychain
            keychain = MacKeychain()
        crypto = _crypto_for(
            {} if keychain is None else {"keychain": keychain}, args.store)
        log = ClaimLog(args.store, crypto=crypto)

        last_printed = -1

        def show(report):
            nonlocal last_printed
            audited = report["audited_rows"]
            if (audited == report["legacy_rows"]
                    or audited - last_printed >= 25_000):
                print("audited %d / %d legacy rows" %
                      (audited, report["legacy_rows"]), flush=True)
                last_printed = audited

        report = audit(log, batch=max(1, args.batch), progress=show)
        print(json.dumps(report, indent=2, sort_keys=True))
        bad = (report["remaining"] + report["unreadable"]
               + report["invalid_legacy_ids"]
               + report["candidate_collision_rows"]
               + report["current_aliases"])
        return 0 if bad == 0 else 1
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
