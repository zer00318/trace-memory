"""Black-box probe for the sealed helper boundary.

Runs inside TRACE.app's sealed runtime, discovers only the installed helper
packages, crosses the real macOS sandbox, and validates one harmless synthetic
reading. It never opens the claim store and therefore cannot manufacture
production evidence merely to make a cockpit check green.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:] = [str(HERE), str(HERE / "site-packages")] + [
    p for p in sys.path if p and "site-packages" not in p]

from tracemem.extraction.helper_runtime import HelperRuntime  # noqa: E402


def main() -> int:
    root = Path(os.environ["TRACE_HELPERS_PATH"])
    runtime = HelperRuntime.discover(root)
    report = runtime.run(
        "class TraceSandboxProbe { func verifyBoundary() {} }",
        "Trace self-test", {"frame_id": "self-test", "t_ms": 1})
    ok = (not report.failures and not report.deferred and
          any(r.helper_id == "com.trace.reference.code-symbols"
              and "TraceSandboxProbe" in r.text for r in report.readings))
    print(json.dumps({
        "ok": ok,
        "discovered": len(runtime.manifests),
        "readings": len(report.readings),
        "failures": list(report.failures),
        "deferred": list(report.deferred),
    }, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
