#!/usr/bin/env python3
"""THE RETRIEVAL MUTATION HARNESS — does the test wall have teeth? (2026-07-23)

A green suite is evidence about the tests, not about the code, until you
break the code on purpose and watch the suite go red. This runs that
experiment on the retrieval path specifically, because that is where an
independent audit found the wall had NO teeth at all: five mutations, every
one of which destroys the system's ability to find a memory, and all 1068
tests stayed green through every one.

Usage:
    .venv/bin/python tools/mutate_retrieval.py            # all mutations
    .venv/bin/python tools/mutate_retrieval.py --only M6  # one

Each mutation is applied to a COPY of the repo under a scratch directory —
the working tree is never modified. Exit code is non-zero if any mutation
survives that is not on the KNOWN_SURVIVORS list, so this can gate a commit.

KNOWN SURVIVORS are not failures; they are findings. A mutation survives
when the product's behaviour genuinely does not depend on the thing mutated.
Each one below is load-bearing documentation: it says "this knob is not
earning its place", and it must be re-justified whenever it changes.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (id, description, file, find, replace)
MUTATIONS = [
    ("M1", "entry index embeds node.id only (all semantics destroyed)",
     "src/tracemem/supplier/vector_index.py",
     'return (label + " " + body).strip() or node.id', "return node.id"),
    ("M2", "retriever returns the WORST matches first",
     "src/tracemem/supplier/retriever.py",
     "entries = vector_index.topk(cue.text, k_entry)",
     "entries = list(reversed(vector_index.topk(cue.text, 10**6)))[:k_entry]"),
    ("M3", "every node gets the IDENTICAL embedding string",
     "src/tracemem/supplier/vector_index.py",
     'return (label + " " + body).strip() or node.id', 'return "identical"'),
    ("M4", "graph spread disabled (hops 2 -> 0)",
     "src/tracemem/supplier/retriever.py", "hops: int = 2", "hops: int = 0"),
    ("M5", "entry door narrowed 12 -> 1",
     "src/tracemem/supplier/retriever.py", "k_entry: int = 12", "k_entry: int = 1"),
    ("M6", "drill reverts to scanning the whole claim log",
     "src/tracemem/supplier/packer.py",
     "claims = claims_by_episode.get(episode_id)", "claims = None"),
    ("M7", "drill ordered by heat instead of relevance",
     "src/tracemem/supplier/packer.py",
     "packed_nodes, key=lambda n: -sims.get(id(n), 0.0))",
     "packed_nodes, key=lambda n: 0)"),
    ("M8", "index drops raw material, indexes labels only",
     "src/tracemem/supplier/vector_index.py",
     'body = (self._evidence(node) or "")[:self.EVIDENCE_CHARS]', 'body = ""'),
    ("M9", "tokenizer stops stripping punctuation",
     "src/tracemem/supplier/text_norm.py",
     'tok = raw.strip("\\"\'`.,;:!?()[]{}<>…-–—*_")', "tok = raw"),
    ("M10", "drill dedup removed (a claim may be packed twice)",
     "src/tracemem/supplier/packer.py",
     "if claim.id in seen_claims:", "if False:"),
    ("M11", "the index is never given a node's raw material",
     "src/tracemem/app/hub.py", "bind(self._node_evidence)", "pass"),
]

# Mutations the product genuinely does not depend on. Each needs a reason.
KNOWN_SURVIVORS = {
    "M5": "retrieve() spreads two hops and a PLACE node holds every episode "
          "at that place, so one entry reaches the whole connected "
          "component. Width is unearned. See test_spread_reaches_past_the_"
          "door_and_that_is_why_width_is_unearned.",
    "M7": "scratch activation is SEEDED from entry similarity and spread "
          "only decays it, so heat order and relevance order are the same "
          "list. The sort is a guard against that coinciding decision "
          "changing, not a live behaviour. See the note in packer.pack.",
}


def run(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="run a single mutation by id, e.g. M6")
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args(argv)

    muts = [m for m in MUTATIONS if not args.only or m[0] == args.only]
    if not muts:
        print("no mutation matched %r" % args.only, file=sys.stderr)
        return 2

    scratch = Path(tempfile.mkdtemp(prefix="tracemem-mutate-"))
    work = scratch / "repo"
    subprocess.run(["rsync", "-a",
                    "--exclude", ".venv", "--exclude", ".git",
                    "--exclude", "data", "--exclude", "gold",
                    "--exclude", "eval_runs", "--exclude", ".pytest_cache",
                    str(ROOT) + "/", str(work) + "/"], check=True)

    base = _suite(args.python, work)
    print("BASELINE: %s\n" % base)
    if re.search(r"\d+ failed", base):
        print("refusing to mutation-test a red suite", file=sys.stderr)
        return 2

    unexpected = []
    for mid, desc, rel, find, repl in muts:
        path = work / rel
        src = path.read_text()
        if find not in src:
            print("%-4s %-52s PATTERN MISS (mutation is stale)" % (mid, desc[:52]))
            unexpected.append(mid)
            continue
        path.write_text(src.replace(find, repl, 1))
        out = _suite(args.python, work)
        path.write_text(src)
        n = re.search(r"(\d+) failed", out)
        killed = bool(n)
        verdict = "CAUGHT(%s)" % n.group(1) if killed else "SURVIVED"
        print("%-4s %-52s %s" % (mid, desc[:52], verdict))
        if not killed and mid not in KNOWN_SURVIVORS:
            unexpected.append(mid)
        elif not killed:
            print("       known survivor: %s" % KNOWN_SURVIVORS[mid])

    shutil.rmtree(scratch, ignore_errors=True)
    if unexpected:
        print("\nUNEXPECTED SURVIVORS: %s" % ", ".join(unexpected))
        print("The retrieval wall lost teeth. Either the test that killed "
              "this mutation was weakened, or a new knob is unvalidated.")
        return 1
    print("\nAll mutations either caught or on the known-survivor list.")
    return 0


def _suite(python: str, cwd: Path) -> str:
    r = subprocess.run([python, "-m", "pytest", "tests", "-q", "-p", "no:randomly"],
                       capture_output=True, text=True, cwd=str(cwd))
    lines = [l for l in r.stdout.strip().splitlines() if l.strip()]
    return lines[-1] if lines else "(no output)"


if __name__ == "__main__":
    raise SystemExit(run())
