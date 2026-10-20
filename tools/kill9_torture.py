#!/usr/bin/env python3
"""The kill -9 torture (map: The machine it lives on / Store engineering).

    tools/kill9_torture.py [--cycles 100] [--seed 7]

100 cycles. Each one: spawn a writer that appends claims as fast as it can
and prints a running count AFTER each commit returns; kill it with SIGKILL
at a random offset; reopen the store and check two things —

    1. PRAGMA integrity_check == "ok"
    2. every ACKNOWLEDGED claim is present

"Acknowledged" is doing the real work in that sentence. The child prints N
only after `append()` returned, which is after the commit; that print is the
store's promise. A claim in flight when the kill lands was never promised
and its absence is not a loss. Counting anything looser would either invent
failures or hide them.

WHY 100 RATHER THAN ONE. The existing kill9 drill kills once, at a fixed-ish
moment, and has passed all along. One cycle proves the happy path survives
one interruption; it says nothing about the WAL checkpoint boundary, the
moment a page splits, or the instant between commit and the next statement.
Those windows are narrow, and narrow windows are exactly what a hundred
random offsets are for. A durability claim backed by one kill is a durability
hope.

THE RANDOM OFFSETS ARE SEEDED. A torture that cannot be re-run identically
is a torture that cannot be bisected when it eventually fails — and when it
does fail, the first question will be "which cycle, and can you do it again".

Exits non-zero on the first loss, printing the cycle, the promised count and
the surviving count. Loss is a durability BUG, to be fixed before real weeks
begin — not a statistic to average.
"""
import argparse
import os
import random
import signal
import subprocess
import sqlite3
import sys
import tempfile
import textwrap
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))

_WRITER = textwrap.dedent("""
    import sys
    sys.path.insert(0, {src!r})
    from tracemem.text.claim_log import ClaimLog
    from tracemem.domain import (CaptureCoord, Claim, Confidence,
        CoordinateStack, Grade, Modality, Provenance, SpanCoord, WorldCoord)

    log = ClaimLog({db!r})
    i = 0
    while True:
        log.append(Claim(
            text="torture %d" % i,
            coord=CoordinateStack(world=WorldCoord(place_key="p"),
                capture=CaptureCoord(frame_id="f%d" % i, device_id="mac-1"),
                span=SpanCoord(bbox=(0.0, 0.0, 1.0, 1.0)), t_ms=i),
            provenance=Provenance(perceiver="ocr", modality=Modality.SCREEN),
            grade=Grade.VERBATIM, confidence=Confidence(1.0), structured={{}},
            bidder="torture", bid_id="b%d" % i, bid_predicted_value=0.0,
            actual_cost=0.0, scope="frame"))
        i += 1
        # Printed only AFTER the commit returned. This line is the store's
        # promise, and it is what the parent holds it to.
        print(i, flush=True)
""")


def one_cycle(tmpdir, rng, index):
    """Returns (ok, promised, survived, integrity, detail)."""
    db = os.path.join(tmpdir, "torture-%d.sqlite3" % index)
    script = os.path.join(tmpdir, "writer-%d.py" % index)
    Path(script).write_text(_WRITER.format(src=str(REPO / "src"), db=db))

    proc = subprocess.Popen([sys.executable, script],
                            stdout=subprocess.PIPE, text=True)
    promised = 0
    # A random offset in wall-clock: the point is to land the kill at an
    # unpredictable place in the write cycle, including inside a checkpoint.
    deadline = time.time() + rng.uniform(0.15, 0.9)
    try:
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            promised = int(line.strip())
    finally:
        try:
            os.kill(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=15)

    integrity = "missing"
    survived = 0
    if os.path.exists(db):
        raw = sqlite3.connect(db)
        try:
            integrity = raw.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            raw.close()
        from tracemem.text.claim_log import ClaimLog
        log = ClaimLog(db)
        survived = log.count()
        chain = log.verify_chain()
        if not chain["ok"]:
            return (False, promised, survived, integrity,
                    "chain broken at %s (%s)" % (chain["broken_at"],
                                                 chain["reason"]))

    if integrity != "ok":
        return False, promised, survived, integrity, "integrity_check failed"
    if survived < promised:
        return (False, promised, survived, integrity,
                "lost %d acknowledged claim(s)" % (promised - survived))
    return True, promised, survived, integrity, ""


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=100)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)

    rng = random.Random(args.seed)
    clean = 0
    total_promised = 0
    started = time.time()

    with tempfile.TemporaryDirectory() as tmp:
        for i in range(1, args.cycles + 1):
            ok, promised, survived, integrity, detail = one_cycle(tmp, rng, i)
            total_promised += promised
            if not ok:
                print("CYCLE %d FAILED: promised=%d survived=%d "
                      "integrity=%s %s" % (i, promised, survived,
                                           integrity, detail))
                print("\n%d/%d clean before the failure. Loss is a "
                      "durability BUG, not a statistic — fix it before real "
                      "weeks begin." % (clean, args.cycles))
                return 1
            clean += 1
            if i % 10 == 0:
                print("  %3d/%d clean (last cycle promised %d)"
                      % (i, args.cycles, promised))

    print("\n%d/%d clean cycles. %d acknowledged claims across all cycles, "
          "zero lost. %.0fs." % (clean, args.cycles, total_promised,
                                 time.time() - started))
    return 0


if __name__ == "__main__":
    sys.exit(main())
