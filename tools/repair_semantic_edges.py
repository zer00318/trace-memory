"""Undo the semantic-edge inflation, by DELETING what must be re-derived.

WHAT WENT WRONG. `semantic_pass` reinforced every existing meaning-edge on
every call, and the night watchman calls it once per 30-second slice. On the
founder's live graph that produced:

    semantic edges   312    weight 4.25 .. 6927.0    evidence_count up to 3130

A semantic weight is a cosine similarity. It cannot exceed 1.0. An evidence
count of 3,130 records three thousand separate observations that never
happened — the machine recomputing the same arithmetic and calling each
repetition a new fact. This is the failure class the codebase names as its
worst: a graph that grows more confident the more often you restart it.

WHY DELETE RATHER THAN RESCALE. There is no honest number to rescale to. The
inflated weight is not "the truth times N" — reinforce ADDS the similarity
each time, so the total mixes an unknown number of passes at an unknown set
of similarities, and dividing by a guessed N would manufacture a precise
number out of nothing. The similarities themselves are cheap and exactly
re-derivable from the node labels, which is the entire promise of a derived
tier: SEMANTIC edges are META, they carry no raw observation, and the next
night rebuilds them correctly. Deleting is the only operation here that does
not invent something.

Raw is untouched. Nodes are untouched. Only derived meaning-edges go.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "data" / "trace_graph.sqlite3"
# A cosine cannot exceed this. Anything above it is arithmetic, not evidence.
MAX_HONEST_WEIGHT = 1.0


def survey(db) -> dict:
    row = db.execute(
        "SELECT COUNT(*), MIN(weight), MAX(weight), MAX(evidence_count) "
        "FROM edge WHERE type='semantic'").fetchone()
    inflated = db.execute(
        "SELECT COUNT(*) FROM edge WHERE type='semantic' AND weight > ?",
        (MAX_HONEST_WEIGHT,)).fetchone()[0]
    return {"semantic_edges": row[0], "min_weight": row[1],
            "max_weight": row[2], "max_evidence": row[3],
            "impossible_weights": inflated}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default=str(GRAPH))
    ap.add_argument("--apply", action="store_true",
                    help="actually delete; without it this only reports")
    args = ap.parse_args(argv)

    db = sqlite3.connect(args.graph)
    before = survey(db)
    print("BEFORE: %s" % before)
    if before["semantic_edges"] == 0:
        print("nothing to repair")
        return 0
    if not args.apply:
        print("\n%d semantic edge(s) would be DELETED and re-derived by the "
              "next night. Re-run with --apply." % before["semantic_edges"])
        return 0

    db.execute("DELETE FROM edge WHERE type='semantic'")
    # The pass must not think it already ran: without clearing this, every
    # surviving node looks unmoved and the edges never come back.
    db.execute("DELETE FROM graph_meta WHERE key='semantic_pass_t_ms'")
    db.commit()
    print("AFTER:  %s" % survey(db))
    print("deleted %d semantic edge(s); the next night re-derives them from "
          "the node labels, which is where they always came from."
          % before["semantic_edges"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
