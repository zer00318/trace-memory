#!/usr/bin/env python3
"""Move the existing master into TRACE's team-scoped Keychain item.

The old item is deleted only after the signed helper imports the exact bytes
and then reopens both live stores through its inherited-key runtime. Key bytes
travel only over the helper's stdin pipe, never argv, environment, or disk.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tracemem.container.keychain_mac import MacKeychain  # noqa: E402

SLOT = "tracemem.master.v1"
HELPER = Path("/Applications/TRACE.app/Contents/MacOS/TRACE")


def migrate(helper: Path = HELPER, legacy=None) -> dict:
    legacy = legacy or MacKeychain()
    master = legacy.get(SLOT)
    if master is None:
        verified = subprocess.run(
            [str(helper), "--verify-stores"], capture_output=True,
            text=True, timeout=120)
        if verified.returncode != 0:
            raise RuntimeError(
                "legacy master is absent and signed store verification failed; "
                "refusing to invent a replacement: %s" % verified.stderr.strip())
        return {"imported": False, "stores": verified.stdout.strip(),
                "legacy_removed": True}
    imported = subprocess.run(
        [str(helper), "--import-key"], input=master,
        capture_output=True, timeout=300)
    if imported.returncode != 0:
        raise RuntimeError("signed helper could not import key: %s" %
                           imported.stderr.decode("utf-8", "replace").strip())
    verified = subprocess.run(
        [str(helper), "--verify-stores"], capture_output=True,
        text=True, timeout=120)
    if verified.returncode != 0:
        raise RuntimeError(
            "signed runtime could not reopen both stores; legacy key retained: %s"
            % verified.stderr.strip())
    if not legacy.delete(SLOT):
        raise RuntimeError(
            "verification passed but legacy item disappeared before deletion")
    return {"imported": True, "stores": verified.stdout.strip(),
            "legacy_removed": True}


def main() -> int:
    print("Authorizing the existing TRACE Keychain item may open a macOS "
          "password prompt. Approve it to preserve the current memory key.",
          flush=True)
    try:
        report = migrate()
    except Exception as exc:
        print("KEY MIGRATION FAILED: %s" % exc, file=sys.stderr)
        return 2
    print("team-scoped key ready")
    print("  stores: %s" % report["stores"])
    print("  legacy unrestricted item removed after verification")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
