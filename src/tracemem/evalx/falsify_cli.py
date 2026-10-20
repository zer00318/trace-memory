#!/usr/bin/env python3
"""Run spec X's falsification experiment against a REAL store snapshot.

    ./trace falsify --author            # make the question template
    ./trace falsify FILE                # run it, get a verdict

It runs INSIDE THE SIGNED APP: the store is encrypted and only the signed
helper may read the key (F-KEYS). Run from a plain interpreter it used to
ask the legacy Keychain item the key had been moved out of (2026-10-07), and
Keyring then minted a fresh random master: a wrong key planted in the login
keychain, and an experiment that read nothing.

WHY THIS TOOL DID NOT EXIST UNTIL NOW, and why that mattered more than it
looked. `src/tracemem/evalx/` is a pre-registered experiment: thresholds
committed before any data existed, and a decision gate that can return
SHED_SUFFICES — "the napkin's heart is refuted by evidence; build better RAG,
not 04". It is the only instrument in this repo that can tell the founder to
STOP building the node tier. It had been run exactly once, on 2026-07-19, on
synthetic fixtures, with seven questions and three gate-eligible.

Meanwhile the node tier grew to 4,198 lines on the assumption the bet pays.

The reason was mechanical, not neglect: `baseline_rag.answer` embedded every
claim in the store for every question, one text per forward pass. On the real
store that is 633,761 model calls per question. The corpus is now embedded
once (baseline_rag.Corpus) and the embedder batches (embedder_st.embed_many),
which is what makes this runnable at all.

THE PROVENANCE RULE IS THE POINT (spec X §2, F2). Only questions written from
LIVED TRUTH, before anyone looked at the store, may reach the decision gate.
A question written by reading the store can only ever flatter the system.
This tool therefore cannot author the set that decides — no program can. What
it can do is hand the founder a template, run whatever he writes, and refuse
to pretend a store-authored set is a verdict.

NEVER AGAINST LIVE INFRA. It snapshots the store first (founder law: the eye
must not go dark so a measurement can happen), and reads that copy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

# Set by run(): the checkout root, derived from the store's location
# (<root>/data/trace_store.sqlite3), so the sealed app writes its report
# beside the memory it measured.
ROOT = Path(".")
STORE = ROOT / "data" / "trace_store.sqlite3"
OUT = ROOT / "eval_runs"
TEMPLATE = ROOT / "exams" / "life_questions.json"
_KEYCHAIN = None

# Named in the cache key: a different embedder means different vectors, and a
# cache that ignored it would score last week's model against this week's
# question.
EMBED_MODEL = "all-MiniLM-L6-v2"


def _load_vectors(path: Path, n: int):
    """Cached vectors for exactly n claims, or None."""
    try:
        import numpy as np
        arr = np.load(str(path))
        return arr if len(arr) == n else None
    except Exception:                                        # noqa: BLE001
        return None


def _save_vectors(path: Path, vectors) -> None:
    try:
        import numpy as np
        np.save(str(path), np.asarray(vectors, dtype="float32"))
    except Exception:                                        # noqa: BLE001
        pass


def _snapshot(src: Path, dest_dir: Path) -> Path:
    """A byte copy of the database and its WAL. sqlite's own backup API would
    take a read lock on a database the daemon is writing; copying is what the
    founder law about not disturbing live infra asks for."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    # THE -shm IS DELIBERATELY NOT COPIED. It is sqlite's shared-memory
    # scratch file, valid only for the processes that mapped it; carried over
    # beside a WAL that has moved on since, it makes the copy look corrupt and
    # reads fail with "disk I/O error" partway through a run. sqlite recovers
    # a database from its -wal alone, which is exactly what a snapshot wants.
    for suffix in ("", "-wal"):
        s = Path(str(src) + suffix)
        if s.exists():
            shutil.copy2(s, str(dest) + suffix)
    return dest


def _crypto_for(store: Path):
    """The cipher this store was written with, or None for a plaintext one.

    Reads the store's own `store_meta.cipher` rather than assuming: a
    plaintext store opened under a key is unreadable, and an encrypted one
    opened without is unreadable the other way. The store records which.
    """
    con = sqlite3.connect(str(store))
    try:
        row = con.execute(
            "SELECT value FROM store_meta WHERE key='cipher'").fetchone()
    except Exception:                                         # noqa: BLE001
        row = None
    finally:
        con.close()
    if not row or str(row[0]).lower() in ("none", "nullcrypto", ""):
        return None
    from tracemem.container.crypto import SodiumCrypto
    from tracemem.container.keyring import Keyring
    if _KEYCHAIN is None:
        raise SystemExit("this store is encrypted: run it through the signed "
                         "app, ./trace falsify ...")
    return SodiumCrypto(Keyring(_KEYCHAIN, create=False).derive("claim-log"))


def _capture_days(store: Path):
    """Which days this store actually saw anything — from the PLAINTEXT
    columns only. `bidder` and `t_ms` are unencrypted by design (the
    calibration query aggregates over them in SQL), so this reads nothing
    about what was on screen."""
    con = sqlite3.connect(str(store))
    try:
        rows = con.execute(
            "SELECT date(t_ms/1000,'unixepoch','localtime') d, count(*) "
            "FROM claim WHERE bidder <> 'system:witness' "
            "GROUP BY d HAVING count(*) > 1000 ORDER BY d").fetchall()
    finally:
        con.close()
    return rows


def author(store: Path) -> None:
    """Write the template the founder fills in. Deliberately thin on hints.

    It lists the days capture was actually running, and NOT what was captured
    on them. Knowing which days exist is not knowing what is in them, and the
    days with no capture are where GAP questions come from — those are
    first-class here, because they measure the eye rather than the memory.
    """
    days = _capture_days(store) if store.exists() else []
    example = [
        {"id": "life-001",
         "text": "What was I working on the afternoon of <a day you remember>?",
         "type": "factual",
         "authored_from": "life",
         "gold": ["<a phrase that MUST appear in a correct answer>"],
         "gap": False},
        {"id": "life-002",
         "text": "Who did I talk to most last week?",
         "type": "emergent",
         "authored_from": "life",
         "gold": ["<the name>"],
         "gap": False},
        {"id": "life-003",
         "text": "What did the message from <someone> actually say?",
         "type": "verbatim",
         "authored_from": "life",
         "gold": ["<the exact words>"],
         "gap": False},
        {"id": "life-004",
         "text": "What did I do on <a day you were away from the Mac>?",
         "type": "factual",
         "authored_from": "life",
         "gold": [],
         "gap": True},
    ]
    TEMPLATE.parent.mkdir(parents=True, exist_ok=True)
    if TEMPLATE.exists():
        print("%s already exists — not overwriting it." % TEMPLATE)
    else:
        TEMPLATE.write_text(json.dumps(example, indent=1) + "\n")
        print("wrote %s" % TEMPLATE)
    print()
    print("HOW TO FILL IT IN — this is the half no program can do for you.")
    print()
    print("  · Write each question from what you REMEMBER, before looking at")
    print("    the store. A question written by reading what was captured can")
    print("    only ever flatter the system, and the gate excludes it.")
    print("  · type=factual   — a fact about a day.")
    print("    type=verbatim  — the exact words of something on screen.")
    print("    type=emergent  — an answer no single moment contains: a")
    print("                     pattern, a comparison, a 'who did I ... most'.")
    print("                     THIS is the stratum the whole bet rests on.")
    print("  · gold is a list of substrings; an answer is right if ANY appears.")
    print("  · gap=true means the truth was never captured and the ONLY right")
    print("    answer is to abstain. Include some — they measure the eye.")
    print("  · Aim for 25-40 questions with at least 10 emergent ones. Fewer")
    print("    than that and the verdict is noise.")
    print()
    if days:
        print("Capture was actually running on these days (count of claims):")
        for d, n in days:
            print("   %s  %6d" % (d, n))
        print()
        print("Days NOT in that list are where your gap questions belong.")
    else:
        print("This store has no day with real capture — fix the eye first.")


def _verdict_lines(report: dict) -> list:
    e = report["emergent_correct"]
    f = report["factual_correct"]
    v = report["verdict"]
    lines = [
        "",
        "=" * 72,
        "VERDICT: %s" % v,
        report["verdict_reason"],
        "=" * 72,
        "",
        "  factual   %.2f      (bar %.2f — below it, capture is the bottleneck)"
        % (f, 0.6),
        "  emergent  %.2f      (>=0.60 refutes the cathedral, <0.30 justifies it)"
        % e,
        "  verbatim  %.2f" % report["verbatim_correct"],
        "  gap rate  %.2f      (share of life questions the eye never saw)"
        % report["capture_gap_rate"],
        "  gate-eligible questions: %d of %d"
        % (report["n_gate_eligible"], report["n_questions"]),
    ]
    if report["invalid_emergent"]:
        lines += [
            "",
            "  MISLABELLED as emergent (gold appears verbatim in one claim, so",
            "  a top-k retriever answers them without combining anything):",
            "    " + ", ".join(report["invalid_emergent"]),
        ]
    if report["n_gate_eligible"] < 10:
        lines += [
            "",
            "  !! %d gate-eligible questions is not a verdict, it is an"
            % report["n_gate_eligible"],
            "     anecdote. The 2026-07-19 rehearsal had 3 and has been quoted",
            "     as a result ever since. Write more before believing this.",
        ]
    return lines


def run(argv, keychain=None, store_path=None) -> int:
    """Entry from `python -m tracemem --falsify-…` inside the signed app."""
    global ROOT, STORE, OUT, TEMPLATE, _KEYCHAIN
    if store_path is not None:
        STORE = Path(store_path)
        ROOT = STORE.resolve().parent.parent
        OUT = ROOT / "eval_runs"
        TEMPLATE = ROOT / "exams" / "life_questions.json"
    _KEYCHAIN = keychain
    return main(argv)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--author", action="store_true",
                    help="write the question template and explain the rules")
    ap.add_argument("--queries", default=str(TEMPLATE),
                    help="the question set to run (JSON or JSONL)")
    ap.add_argument("--store", default=str(STORE))
    ap.add_argument("--budget", type=int, default=2000,
                    help="context words handed to the model per question")
    ap.add_argument("--model", default="gemma3:12b-it-qat")
    ap.add_argument("--out", default=None,
                    help="report directory (default eval_runs/falsify_<date>)")
    ap.add_argument("--max-claims", type=int, default=0,
                    help="sample the corpus to N evenly-spaced claims — for "
                         "iterating on the question set, never for a verdict")
    ap.add_argument("--no-cache", action="store_true",
                    help="re-embed the corpus even if a cached pass exists")
    ap.add_argument("--loops", type=int, default=0,
                    help="how many upstream repair loops have already run (A4)")
    args = ap.parse_args(argv)

    store = Path(args.store)
    if args.author:
        author(store)
        return 0

    queries = Path(args.queries)
    if not queries.exists():
        print("no question set at %s — run with --author first." % queries)
        return 2

    from tracemem.evalx import harness, query_set
    from tracemem.llm.reasoner_ollama import OllamaReasoner
    from tracemem.supplier.embedder_st import SentenceTransformerEmbedder
    from tracemem.text.claim_log import ClaimLog

    qs = query_set.load(str(queries))
    life = [q for q in qs if q.authored_from == "life"]
    if not life:
        print("Every question in this set is store-authored. Spec X excludes")
        print("those from the gate on purpose — they can only flatter the")
        print("system. This will run and report, and it is NOT a verdict.")
        print()

    snap_dir = ROOT / ".falsify_snapshot"
    print("snapshotting the store (the eye keeps watching) …")
    snap = _snapshot(store, snap_dir)

    out = Path(args.out) if args.out else (
        OUT / ("falsify_" + datetime.now().strftime("%Y%m%d_%H%M")))

    t0 = time.time()
    last = [0.0]

    def progress(done, total):
        now = time.time()
        if now - last[0] < 5 and done < total:
            return
        last[0] = now
        rate = done / max(1e-6, now - t0)
        left = (total - done) / rate if rate else 0
        print("  embedding corpus %d/%d  (%.0f/s, ~%.0fs left)"
              % (done, total, rate, left), flush=True)

    def on_answer(q, ans):
        mark = "abstain" if ans.abstained else "answer "
        print("  [%s] %-9s %s" % (mark, q.qtype, q.text[:56]), flush=True)

    # THE SNAPSHOT IS ENCRYPTED, like the store it came from. Opened by path
    # with no crypto it reads back nothing at all and the experiment would
    # score a clean zero on an empty room — which is exactly how the never-lie
    # measurement spent a month scoring nothing (2026-08-23). The key comes
    # from the Keychain, the same way the daemon gets it.
    log = ClaimLog(str(snap), crypto=_crypto_for(snap))
    readable = log.count()
    print("running %d question(s) against %s (%s claims readable)"
          % (len(qs), snap.name, format(readable, ",")))
    if not readable:
        print("nothing readable in that store — check the key, not the code.")
        shutil.rmtree(snap_dir, ignore_errors=True)
        return 3
    sampled = None
    if args.max_claims:
        # Select by the PLAINTEXT time index and decrypt only the sample.
        # Without this a "quick" run still authenticates and decrypts every
        # row in the store before throwing almost all of them away.
        ids = log.ids_in_range(0, 2 ** 62)
        if len(ids) > args.max_claims:
            step = len(ids) / float(args.max_claims)
            ids = [ids[int(i * step)] for i in range(args.max_claims)]
        sampled = [c for c in (log.get(i) for i in ids) if c is not None]
        print("sampled %d of the store's claims (NOT a verdict — a rehearsal)"
              % len(sampled))

    # THE CORPUS PASS IS PAID ONCE. Measured on the founder's real claims the
    # embedder does about 130/s, so a full store is roughly 80 minutes — and
    # the whole point of this tool is that the founder ITERATES on his
    # question set. An experiment you must budget a working morning for every
    # time you add a question is an experiment that gets run once.
    #
    # The key is (store size, claim count, model, sample size). This store is
    # APPEND-ONLY, so a byte length and a row count cannot both match across a
    # real change; a length mismatch on load skips the cache in silence,
    # because a stale cache would score the wrong text against every question
    # — the kind of wrong that looks like a result.
    n_corpus = len(sampled) if sampled is not None else readable
    vectors, cache_path = None, None
    if not args.no_cache:
        key = hashlib.sha256(
            ("%d|%d|%s|%d" % (store.stat().st_size, n_corpus,
                              EMBED_MODEL, args.max_claims)).encode()
        ).hexdigest()[:16]
        cache_dir = OUT / ".corpus_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / ("%s.npy" % key)
        vectors = _load_vectors(cache_path, n_corpus)
        if vectors is not None:
            print("reusing %d cached vectors (%s)"
                  % (len(vectors), cache_path.name))

    built = {}
    embedder = SentenceTransformerEmbedder()
    report = harness.run(
        str(snap), str(queries), args.budget, claim_log=log, claims=sampled,
        vectors=vectors,
        embedder=embedder,
        reasoner=OllamaReasoner(args.model),
        out_dir=str(out), prior_upstream_loops=args.loops,
        progress=progress, on_answer=on_answer, max_claims=args.max_claims,
        on_corpus=lambda c: built.setdefault("vectors", c.vectors))

    report["ran_at"] = datetime.now().isoformat(timespec="seconds")
    report["store"] = str(store)
    report["model"] = args.model
    report["life_authored"] = len(life)
    report["max_claims"] = args.max_claims or None
    with open(out / "report.json", "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    if vectors is None and cache_path is not None and built.get("vectors"):
        _save_vectors(cache_path, built["vectors"])

    print("\n".join(_verdict_lines(report)))
    print()
    print("report:   %s" % (out / "report.json"))
    print("failures: %s   <- every line is a requirement" % (out / "failures.md"))
    shutil.rmtree(snap_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit("run it through the signed app: ./trace falsify ...")
