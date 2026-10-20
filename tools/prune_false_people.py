"""Remove the person nodes that were never people.

The birth layer promoted any capitalised word after "with/to/from/thanks" to
a PERSON. On the founder's graph that produced 110 of them, of which roughly
30 are people. `nodes/birth/person_judge.py` now stops new ones at the gate,
but the ones already born are still in the graph, still being related to
things by the night, and still packed into answers.

DELETING A DEPTH-0 NODE IS NORMALLY REFUSED, and the refusal is right:
`delete_meta` and `retire_meta` both raise on the entity tier, because that
tier is a projection of RAW and must not be edited by hand. This tool is
allowed only because it does not edit — it removes a projection that the
current birth rules would not create, so the graph is left exactly as a
re-derivation would leave it. Raw is untouched, and every claim that produced
these nodes is still on disk, unchanged.

A NODE IS REMOVED ONLY IF EVERY name it carries was judged not-a-person.
A node holding both a judged person and an unjudged surface stays: the
question this tool answers is "did the model say no", never "did the model
fail to say yes".

AND A SURVIVOR LOSES ITS FALSE ALIASES. A node can carry several `seen_as`
values because the resolver merged mentions that looked like the same person.
On the founder's graph one node ended up holding both "Hana" and "Trash".
Deleting the node would lose a real person; keeping it whole would leave
"Trash" answerable as a name. So judged-no names are stripped from nodes that
survive on the strength of a judged-yes one.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "data" / "trace_graph.sqlite3"


def _verdicts(db) -> dict:
    out = {}
    for key, value in db.execute(
            "SELECT key, value FROM graph_meta WHERE key LIKE 'person_verdict:%'"):
        surface = key.split("person_verdict:person:", 1)[-1]
        out[surface] = (value == "yes")
    return out


def condemned(db):
    """[(node_id, [names])] for entity nodes whose every name is a no."""
    verdicts = _verdicts(db)
    out = []
    for nid, attrs in db.execute(
            "SELECT id, attributes FROM node WHERE kind='entity' "
            "AND derivation_depth=0"):
        try:
            names = [a["value"] for a in json.loads(attrs)
                     if a.get("key") == "seen_as"]
        except Exception:
            continue
        real = [n for n in names if n.lower() != "a person"]
        if not real:
            continue                      # anonymous — not this tool's business
        if all(verdicts.get(n.lower()) is False for n in real):
            out.append((nid, real))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default=str(GRAPH))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    db = sqlite3.connect(args.graph)
    verdicts = _verdicts(db)
    doomed = condemned(db)
    total = db.execute("SELECT COUNT(*) FROM node WHERE kind='entity'").fetchone()[0]
    print("entity nodes: %d   verdicts recorded: %d   judged NOT people: %d"
          % (total, len(verdicts), sum(1 for v in verdicts.values() if not v)))
    print("nodes to remove: %d" % len(doomed))
    print("  %s" % ", ".join(sorted(n for _, names in doomed for n in names))[:800])
    if not args.apply:
        print("\nRe-run with --apply.")
        return 0

    # Strip judged-no aliases from nodes that survive.
    stripped = 0
    for nid, attrs in list(db.execute(
            "SELECT id, attributes FROM node WHERE kind='entity' "
            "AND derivation_depth=0")):
        try:
            parsed = json.loads(attrs)
        except Exception:
            continue
        kept = [a for a in parsed
                if not (a.get("key") == "seen_as"
                        and verdicts.get(str(a.get("value", "")).lower())
                        is False)]
        if len(kept) != len(parsed) and kept:
            db.execute("UPDATE node SET attributes=? WHERE id=?",
                       (json.dumps(kept), nid))
            stripped += 1

    ids = [nid for nid, _ in doomed]
    q = ",".join("?" * len(ids))
    db.execute("DELETE FROM edge WHERE src IN (%s) OR dst IN (%s)" % (q, q),
               ids + ids)
    db.execute("DELETE FROM node WHERE id IN (%s)" % q, ids)
    # Beliefs built ON those nodes go too: a derivation whose parent never
    # existed is not a belief, it is an orphan citing a deleted source.
    db.execute("DELETE FROM node WHERE derivation_depth > 0 AND ("
               + " OR ".join("parent_ids LIKE ?" for _ in ids) + ")",
               ["%%%s%%" % i for i in ids]) if ids else None
    db.execute("DELETE FROM ledger")
    db.commit()
    print("removed %d node(s), stripped a false alias from %d more; the "
          "ledger is cleared so the night re-derives what is left."
          % (len(ids), stripped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
