"""Clear the derived tier so the night re-derives it with the current engine.

WHEN THIS IS THE RIGHT THING. Derived nodes (PATTERN, HYPOTHESIS, QUESTION)
are META: they carry no raw observation and are, by construction, a pure
function of the entity tier plus the code that combined it. When that code
had a defect, the beliefs it produced are not opinions the memory has
outgrown — they are output of a broken machine, and the honest move is to
re-run the machine rather than to keep its mistakes and reason about them.

WHAT MADE THEM WRONG, measured 2026-08-20 on 168 episodes:

    dev:mac-1        76.8% of episodes    (the device id — in everything)
    Brave Browser    76.2%                (his only browser)
    youtube.com      57.7%
    median node       1 episode

The combination tier had no notion of a node being uninformative, so once the
night could finally run it produced beliefs like "dev:mac-1 and Thriller: both
repeatedly present with youtube.com" — a true statement about a constant, and
worth nothing. `candidate_selector.ubiquitous` now gates those out.

WHY DELETE RATHER THAN RETIRE. `retire_meta` writes a tombstone, which is the
moat: "you used to think X, and stopped" is a real part of the memory. But a
tombstone means the belief was CORRECTED BY EVIDENCE. These were never
believed on evidence; they were produced by a scan that could not tell a
constant from an entity. Writing 719 tombstones would put that fiction into
the one record whose whole value is that it is true.

THE LEDGER GOES TOO. `select_candidates` skips any pair whose ledger entry is
fresh, so leaving it would delete the beliefs and then refuse to re-derive
them — a memory emptied and locked. Same for the pattern scan's entries.

RAW IS UNTOUCHED. The entity tier is untouched. Only the derived tier and the
work ledger, both of which the night rebuilds.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "data" / "trace_graph.sqlite3"


def survey(db) -> dict:
    kinds = dict(db.execute(
        "SELECT kind, COUNT(*) FROM node WHERE derivation_depth > 0 "
        "GROUP BY kind").fetchall())
    return {
        "derived_nodes": sum(kinds.values()),
        "by_kind": kinds,
        "entity_tier": db.execute(
            "SELECT COUNT(*) FROM node WHERE derivation_depth = 0"
        ).fetchone()[0],
        "ledger_rows": db.execute("SELECT COUNT(*) FROM ledger").fetchone()[0],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default=str(GRAPH))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    db = sqlite3.connect(args.graph)
    before = survey(db)
    print("BEFORE: %s" % before)
    if not args.apply:
        print("\nwould delete %d derived node(s) and %d ledger row(s); the "
              "entity tier (%d nodes) and all raw claims are untouched.\n"
              "Re-run with --apply."
              % (before["derived_nodes"], before["ledger_rows"],
                 before["entity_tier"]))
        return 0

    db.execute("DELETE FROM edge WHERE src IN "
               "(SELECT id FROM node WHERE derivation_depth > 0) "
               "OR dst IN (SELECT id FROM node WHERE derivation_depth > 0)")
    db.execute("DELETE FROM node WHERE derivation_depth > 0")
    db.execute("DELETE FROM ledger")
    db.commit()
    after = survey(db)
    print("AFTER:  %s" % after)
    print("\nThe next night re-derives from the entity tier, now with the "
          "ubiquity gate. Watch `./trace status` — it should go from settled "
          "to working and back to settled.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
