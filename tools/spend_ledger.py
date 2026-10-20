#!/usr/bin/env python3
"""OWNS: the one frontier spend ledger (map: Extraction / Frontier spend
control — enforcement; Workshop / Frontier cost ledger — display).

    tools/spend_ledger.py                 show the running total
    tools/spend_ledger.py --publish       write the totals into status.json

Frontier APIs are allowed on the founder's own data during development
(ruling 2026-07-19). "Allowed" is not "unmetered": this is a self-funded
project, the calls are made by automated sessions that do not feel money,
and the failure mode is a bill arriving after the spending is over.

THREE PROPERTIES, and the third is the one that makes it real:

1. APPEND-ONLY. Every call writes one line: when, which tool, which model,
   tokens, dollars, and why. Same shape of law as the claim log — a spend
   record that can be edited is a spend record nobody can trust.

2. ONE FILE. Ceiling runs, stand-in organs, and any future frontier caller
   all write here. Two ledgers means the total is a guess.

3. A HARD CAP THAT REFUSES. `check_before_spending()` raises once the
   month's total is at the cap. Not a warning, not a log line — a refusal,
   before the call. A budget that only reports is a budget that gets
   discovered afterwards.

THE CAP IS DECLARED, NOT DERIVED (2026-07-22): MONTHLY_CAP_USD is a
founder-owned number, and this file is not where it gets decided. It is set
low on purpose — a cap that is too low fails loudly and gets raised
deliberately, while a cap that is too high fails silently and gets
discovered on a statement.

WHAT THIS DOES NOT DO: it does not price calls. The caller reports its own
cost, because only the caller knows the model and token counts, and a price
table baked in here would rot the first time a provider changed one.
`estimate_usd` exists as a convenience with its rates stamped and dated —
and it says so, so nobody mistakes it for a bill.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "evaluation_spend.jsonl"
STATUS = ROOT / "campaign" / "status.json"

# FOUNDER-OWNED, declared 2026-07-22. Deliberately low: a cap that is too
# low fails loudly and is raised on purpose; one that is too high fails
# silently and is discovered on a statement.
MONTHLY_CAP_USD = 50.0

# Per-million-token rates, STAMPED AND DATED so they can be seen to be
# stale. Used only by estimate_usd(), never to record an actual charge.
RATES_AS_OF = "2026-07-22"
RATES_USD_PER_MTOK = {
    "claude-opus-4-8": {"in": 15.0, "out": 75.0},
    "claude-sonnet-5": {"in": 3.0, "out": 15.0},
    "claude-haiku-4-5": {"in": 1.0, "out": 5.0},
}


class SpendCapReached(RuntimeError):
    """The month's budget is gone. Raised BEFORE a call, never after."""


def _month_of(t_ms):
    return time.strftime("%Y-%m", time.localtime(t_ms / 1000.0))


def load(path=LEDGER):
    if not Path(path).exists():
        return []
    out = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def record(tool, model, usd, tokens_in=0, tokens_out=0, why="",
           t_ms=None, path=LEDGER):
    """Append one spend line. Returns the record written."""
    if usd < 0:
        raise ValueError("a negative charge is a refund, not a spend — "
                         "record it as its own line with why='refund'")
    entry = {
        "t_ms": int(t_ms if t_ms is not None else time.time() * 1000),
        "tool": tool, "model": model, "usd": round(float(usd), 6),
        "tokens_in": int(tokens_in), "tokens_out": int(tokens_out),
        "why": str(why)[:200],
    }
    with Path(path).open("a") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


def month_total(entries=None, month=None, path=LEDGER):
    entries = load(path) if entries is None else entries
    month = month or time.strftime("%Y-%m")
    return round(sum(e["usd"] for e in entries
                     if _month_of(e["t_ms"]) == month), 6)


def total(entries=None, path=LEDGER):
    entries = load(path) if entries is None else entries
    return round(sum(e["usd"] for e in entries), 6)


def check_before_spending(expected_usd=0.0, cap=MONTHLY_CAP_USD, path=LEDGER):
    """Raise if this call would cross the month's cap.

    Checks the PROJECTED total, not the current one: a check that only
    looks at what has already been spent lets the single call that blows
    the budget through, which is usually the expensive one.
    """
    spent = month_total(path=path)
    if spent + expected_usd > cap:
        raise SpendCapReached(
            "frontier spend cap reached: $%.2f already spent this month, "
            "this call adds ~$%.2f, cap is $%.2f. Raise MONTHLY_CAP_USD "
            "deliberately (founder decision) or wait for the month to roll."
            % (spent, expected_usd, cap))
    return spent


def estimate_usd(model, tokens_in, tokens_out):
    """A CONVENIENCE ESTIMATE, not a bill. Returns None for an unknown
    model rather than guessing — a fabricated cost in a spend ledger is
    worse than a missing one."""
    rate = RATES_USD_PER_MTOK.get(model)
    if rate is None:
        return None
    return round(tokens_in / 1e6 * rate["in"]
                 + tokens_out / 1e6 * rate["out"], 6)


def summary(path=LEDGER, cap=MONTHLY_CAP_USD):
    entries = load(path)
    if not entries:
        return {"frontier_spend_usd_total": 0.0,
                "frontier_spend_usd_month": 0.0,
                "frontier_spend_cap_usd": cap,
                "frontier_spend_calls": 0,
                "frontier_spend_status": "no frontier calls recorded"}
    month = month_total(entries)
    return {
        "frontier_spend_usd_total": total(entries),
        "frontier_spend_usd_month": month,
        "frontier_spend_cap_usd": cap,
        "frontier_spend_calls": len(entries),
        "frontier_spend_pct_of_cap": round(100.0 * month / cap, 1) if cap else None,
        "frontier_spend_status": "at cap" if month >= cap else "under cap",
    }


def publish(path=LEDGER, status_path=STATUS, cap=MONTHLY_CAP_USD):
    """The display half: the cockpit renders status.json's `numbers`."""
    status = json.loads(Path(status_path).read_text())
    status.setdefault("numbers", {}).update(summary(path, cap))
    Path(status_path).write_text(json.dumps(status, indent=2,
                                            ensure_ascii=False) + "\n")
    return status["numbers"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=str(LEDGER))
    ap.add_argument("--status", default=str(STATUS))
    ap.add_argument("--publish", action="store_true")
    args = ap.parse_args(argv)

    got = summary(args.ledger)
    for k, v in sorted(got.items()):
        print("  %-28s %s" % (k, v))
    print("\n  rates stamped as of %s (estimates only, never a bill)"
          % RATES_AS_OF)
    if args.publish:
        publish(args.ledger, args.status)
        print("\npublished to %s" % args.status)
    return 0


if __name__ == "__main__":
    sys.exit(main())
