"""Remove object/topic nodes minted by a physical grammar from screen OCR.

`entity_extractor` no longer mints these (see `_has_a_body`), but the ones
already born are still in the graph and still surface as company in dossiers:
"Hana was seen alongside green clay". Measured 2026-08-24, every value on
the founder's graph:

    object  black box · black mar · blue and · green clay · green curtai ·
            grey bubble · orange life · red and · white cropped ...   25/25
    topic   "gauge she has told you plainly s" · "their accessibility
            contexts and m" · "window_stack api precisely"             3/4

Same standing as `prune_false_people.py`: this removes projections the
CURRENT birth rules would not create, which is what a full re-derivation from
raw would do. Raw is untouched; a camera claim can mint these again the
moment the phone pillar lands.

KEPT: any object or topic whose claims came from a sense with a body. There
are none today, and there will be, so the tool checks rather than assumes.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "data" / "trace_graph.sqlite3"
UNFED_KINDS = ("object", "topic")
# Anonymous "people" belong here too: on screen `_ANON_PERSON_RE` matches the
# WORDS — "someone commented on your post" is a notification, not company.
# A named person is NOT touched; only the nameless ones, which on screen data
# are never a person at all.


def condemned(db):
    out = []
    for nid, kind, attrs in db.execute(
            "SELECT id, kind, attributes FROM node WHERE derivation_depth=0 "
            "AND kind IN (%s)" % ",".join("?" * len(UNFED_KINDS)),
            UNFED_KINDS):
        try:
            values = [a.get("value") for a in json.loads(attrs)]
        except Exception:
            continue
        out.append((nid, kind, [v for v in values if v]))

    for nid, attrs in db.execute(
            "SELECT id, attributes FROM node WHERE derivation_depth=0 "
            "AND kind='entity'"):
        try:
            names = [str(a.get("value", "")) for a in json.loads(attrs)
                     if a.get("key") == "seen_as"]
        except Exception:
            continue
        if names and all(n.strip().lower() == "a person" for n in names):
            out.append((nid, "anon-person", names))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default=str(GRAPH))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    db = sqlite3.connect(args.graph)
    doomed = condemned(db)
    print("object/topic nodes born from a body-less sense: %d" % len(doomed))
    for _nid, kind, vals in doomed[:30]:
        print("  %-6s %s" % (kind, ", ".join(str(v) for v in vals)[:70]))
    if not args.apply:
        print("\nRe-run with --apply.")
        return 0

    ids = [nid for nid, _k, _v in doomed]
    if ids:
        q = ",".join("?" * len(ids))
        db.execute("DELETE FROM edge WHERE src IN (%s) OR dst IN (%s)" % (q, q),
                   ids + ids)
        db.execute("DELETE FROM node WHERE id IN (%s)" % q, ids)
        # Beliefs built on them are orphans citing a deleted parent.
        db.execute("DELETE FROM node WHERE derivation_depth > 0 AND ("
                   + " OR ".join("parent_ids LIKE ?" for _ in ids) + ")",
                   ["%%%s%%" % i for i in ids])
        db.execute("DELETE FROM ledger")
        db.commit()
    print("removed %d; the ledger is cleared so the night re-derives what "
          "is left." % len(ids))
    return 0


if __name__ == "__main__":
    sys.exit(main())
