#!/usr/bin/env python3
"""The growth meter (map: Text / Storage math / Growth meter).

    tools/growth_meter.py [--store data/trace_store.sqlite3]

Measures how much disk the store actually occupies, appends the reading to a
history file, and writes bytes/day plus a one-year projection into
campaign/status.json where the cockpit already renders `numbers`.

WHY THIS EXISTS BEFORE THERE IS MUCH TO MEASURE. Two other atoms are
currently holding DECLARED numbers because this one had never run:
app/disk_pressure.py's thresholds and the storage projection itself. Both
say so in their own comments. A declared threshold is a guess wearing a
lab coat, and the only thing that turns it into a measurement is a series
like this one starting early enough to have history when it is needed.

WHAT IT COUNTS. SQLite in WAL mode is three files — the database, `-wal`,
and `-shm` — and the WAL alone can be a large fraction of the total between
checkpoints. Counting only the main file would under-report exactly when
the store is busiest, which is the moment the number matters. All three are
summed, and each is reported separately so a suspiciously large WAL is
visible rather than averaged away.

THE PROJECTION IS DELIBERATELY REFUSED UNTIL IT IS EARNED. Two readings a
minute apart would happily extrapolate to terabytes a year. bytes/day is
computed only from readings at least MIN_SPAN_H apart, and the projection
only from at least MIN_READINGS of them. Below that the fields are absent
rather than zero or estimated — an absent number invites a look at the
series, a wrong one gets budgeted against.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT / "evaluation_growth.jsonl"
STATUS = ROOT / "campaign" / "status.json"
DEFAULT_STORE = ROOT / "data" / "trace_store.sqlite3"

# A rate computed over a shorter span is noise: one checkpoint or one vacuum
# dominates it. Half a day is the shortest window in which normal daily
# rhythm (working hours, a night job) is partly represented.
MIN_SPAN_H = 12.0
MIN_READINGS = 3


def measure(store_path) -> dict:
    """Bytes on disk for the store and its WAL sidecars."""
    store = Path(store_path)
    parts = {}
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(store) + suffix)
        parts[suffix or "db"] = p.stat().st_size if p.exists() else 0
    return {"t_ms": int(time.time() * 1000), "path": str(store),
            "bytes": sum(parts.values()), "parts": parts,
            "exists": store.exists()}


def load_history(path=HISTORY):
    if not Path(path).exists():
        return []
    out = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def append_history(reading, path=HISTORY):
    with Path(path).open("a") as fh:
        fh.write(json.dumps(reading) + "\n")


def rate(history) -> dict:
    """bytes/day and a year projection — or an honest refusal.

    Returns only the keys it has earned. A caller that wants a number and
    finds none has learned something true; a caller handed a fabricated one
    has not.
    """
    usable = [h for h in history if h.get("exists")]
    if not usable:
        return {"growth_readings": 0}

    # CURRENT SIZE NEEDS NO HISTORY. Only the RATE does. Withholding today's
    # store size until a second reading exists would hide a fact already in
    # hand — the refusal below is about extrapolation, not about measurement.
    last = usable[-1]
    out = {"growth_readings": len(usable), "store_bytes": last["bytes"]}
    if len(usable) < 2:
        out["growth_status"] = "first reading — rate needs a second"
        return out

    first = usable[0]
    span_h = (last["t_ms"] - first["t_ms"]) / 3_600_000.0
    out["growth_span_hours"] = round(span_h, 2)

    if span_h < MIN_SPAN_H or len(usable) < MIN_READINGS:
        # Deliberately no bytes_per_day key. See the module docstring.
        out["growth_status"] = (
            "too early — need %d readings spanning %.0fh, have %d spanning "
            "%.1fh" % (MIN_READINGS, MIN_SPAN_H, len(usable), span_h))
        return out

    per_day = (last["bytes"] - first["bytes"]) / (span_h / 24.0)
    out["store_bytes_per_day"] = int(per_day)
    out["store_gb_per_year"] = round(per_day * 365 / (1024 ** 3), 2)
    out["growth_status"] = "measured"
    return out


def update_status(numbers, status_path=STATUS) -> dict:
    status = json.loads(Path(status_path).read_text())
    status.setdefault("numbers", {}).update(numbers)
    Path(status_path).write_text(json.dumps(status, indent=2,
                                            ensure_ascii=False) + "\n")
    return status


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default=str(DEFAULT_STORE))
    ap.add_argument("--history", default=str(HISTORY))
    ap.add_argument("--status", default=str(STATUS))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    reading = measure(args.store)
    if not args.dry_run:
        append_history(reading, args.history)
    history = load_history(args.history)
    numbers = rate(history)
    if not args.dry_run:
        update_status(numbers, args.status)

    print("store: %s" % reading["path"])
    print("  exists: %s  bytes: %d  (db %d / wal %d / shm %d)"
          % (reading["exists"], reading["bytes"], reading["parts"]["db"],
             reading["parts"]["-wal"], reading["parts"]["-shm"]))
    for k, v in sorted(numbers.items()):
        print("  %-22s %s" % (k, v))
    return 0


if __name__ == "__main__":
    sys.exit(main())
