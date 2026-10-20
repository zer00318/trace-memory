"""Undo an identity fusion: one node carrying many people becomes many nodes.

FOUND 2026-08-24 on the founder's live graph. One node held sixteen names:

    Bartholomew · Bart · Rory · Pam · Rosa · Hana · Nora · Fred ·
    Cora · Quentin · Wade · Adele · Vince · Reid · Ivy · Eli

`same_as_pass` groups by EACH name a node carries, so a node that
legitimately held two ("Bart"/"Bartholomew", a correct prefix match) grew a
SAME_AS edge to every node named either, both reinforced to the auto-merge
bar, and the fused node then bridged wider. The cause is fixed
(`identity.one_identity`); this repairs what it already did.

WHAT IS RECOVERABLE AND WHAT IS NOT, stated rather than glossed:
  · RECOVERED — each name, with its own episodes, grade, confidence and
    first-seen time. Those were never mixed: `accrue` keeps one Attribute per
    (key, value), so "Hana was seen in these 4 moments" survived the fusion
    intact and comes back exact.
  · NOT RECOVERED — the fused node's EDGES. A co-occurrence edge belonged to
    whichever person was actually present, and the merge did not record
    which. Re-attributing them by guess is how the fusion happened in the
    first place, so they are dropped. The people come back with correct
    sightings and no company until they are seen again.

Names that are prefix-variants of each other stay together — "Ben" and
"Benjamin" are one person, which is exactly what the resolver intended.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "data" / "trace_graph.sqlite3"
_NS = uuid.uuid5(uuid.NAMESPACE_URL, "tracemem-node")


def _same_family(a, b):
    a, b = str(a).strip().lower(), str(b).strip().lower()
    return a == b or (min(len(a), len(b)) >= 3
                      and (a.startswith(b) or b.startswith(a)))


def _families(names):
    fams = []
    for n in names:
        for f in fams:
            if any(_same_family(n, m) for m in f):
                f.append(n)
                break
        else:
            fams.append([n])
    return fams


def fused(db, floor=2):
    out = []
    for nid, attrs in db.execute(
            "SELECT id, attributes FROM node WHERE kind='entity' "
            "AND derivation_depth=0"):
        try:
            parsed = json.loads(attrs)
        except Exception:
            continue
        names = [a["value"] for a in parsed
                 if a.get("key") == "seen_as"
                 and str(a.get("value", "")).lower() != "a person"]
        fams = _families(names)
        if len(fams) >= floor:
            out.append((nid, parsed, fams))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default=str(GRAPH))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    db = sqlite3.connect(args.graph)
    victims = fused(db)
    print("fused nodes: %d" % len(victims))
    for nid, _p, fams in victims:
        print("  %s -> %d people: %s" % (nid[:8], len(fams),
                                         ", ".join("/".join(f) for f in fams)))
    if not args.apply:
        print("\nRe-run with --apply.")
        return 0

    made = 0
    for nid, parsed, fams in victims:
        for fam in fams:
            attrs = [a for a in parsed
                     if a.get("key") == "seen_as" and a.get("value") in fam]
            if not attrs:
                continue
            key = "person:%s" % sorted(fam, key=len)[0].lower()
            new_id = str(uuid.uuid5(_NS, key))
            db.execute(
                "INSERT OR REPLACE INTO node (id, kind, activation, "
                "last_active_t_ms, salience_ema, derivation_depth, "
                "parent_ids, attributes, builder_version) "
                "VALUES (?,'entity',?,?,?,0,'[]',?,'split')",
                (new_id, 0.5, max(a.get("t_ms", 0) for a in attrs), 0.05,
                 json.dumps(attrs)))
            made += 1
        db.execute("DELETE FROM edge WHERE src=? OR dst=?", (nid, nid))
        db.execute("DELETE FROM node WHERE id=?", (nid,))
    # Beliefs about the fused node cite a parent that no longer exists.
    db.execute("DELETE FROM node WHERE derivation_depth > 0")
    db.execute("DELETE FROM ledger")
    db.commit()
    print("\nsplit into %d people; beliefs and ledger cleared so the night "
          "re-derives from what is now true." % made)
    return 0


if __name__ == "__main__":
    sys.exit(main())
