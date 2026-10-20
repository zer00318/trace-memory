"""OWNS: one command over a store SNAPSHOT -> report + failures.md (spec X §6).

Runs on a snapshot, never live infra (founder law: don't restart live infra
to measure it). failures.md is a first-class artifact: under
CATHEDRAL_JUSTIFIED it becomes the requirements doc for the combiner.
Embedder and reasoner are ports — fakes in tests, real adapters in anger.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Dict, List, Optional

from tracemem.evalx import baseline_rag, compression_probe, decision_gate, query_set
from tracemem.text.claim_log import ClaimLog


def _correct(q, ans) -> bool:
    if q.gap:
        return ans.abstained                       # a gap answered = confabulation
    low = ans.text.lower()
    return any(g.lower() in low for g in q.gold)


def run(store_snapshot_path: str, query_set_path: str, budget: int,
        embedder=None, reasoner=None, out_dir: Optional[str] = None,
        prior_upstream_loops: int = 0, progress=None, on_answer=None,
        max_claims: int = 0, claim_log=None, claims=None,
        vectors=None, on_corpus=None) -> Dict:
    if embedder is None or reasoner is None:
        raise ValueError("harness needs embedder and reasoner ports — "
                         "fakes in tests, sentence-transformers/ollama in anger")
    # AN ALREADY-OPEN LOG WINS. A real store is encrypted, and opening it by
    # path here would build a NullCrypto ClaimLog that reads back nothing:
    # every row skips as unreadable, the corpus comes out EMPTY, and the
    # experiment reports a clean zero instead of failing. The caller that
    # holds the keychain passes its own log; tests keep passing a path.
    log = claim_log if claim_log is not None else ClaimLog(store_snapshot_path)
    questions = query_set.load(query_set_path)
    invalid_emergent = set(query_set.emergent_validity(questions, log.all_claims()))

    # THE CORPUS IS EMBEDDED ONCE, not once per question. See
    # baseline_rag.Corpus for why the per-question version made this whole
    # experiment unrunnable on anything but a fixture.
    corpus = baseline_rag.Corpus(log, embedder, progress=progress,
                                 max_claims=max_claims, claims=claims,
                                 vectors=vectors)
    if on_corpus is not None:
        # Handed out so a caller can CACHE the pass. Embedding a real store is
        # tens of minutes and the whole point of the experiment is that the
        # question set gets edited and re-run.
        on_corpus(corpus)
    if not corpus.claims:
        # Said out loud rather than measured as 0.0 — an empty corpus is a
        # wiring fault (wrong path, wrong key), never a result.
        raise ValueError(
            "the corpus is empty: %s yielded no readable claims. If the store "
            "is encrypted, pass a ClaimLog built with its crypto rather than a "
            "path." % store_snapshot_path)

    per_type: Dict[str, List[bool]] = defaultdict(list)
    failures: List[str] = []
    for q in questions:
        ans = baseline_rag.answer(q.text, log, embedder, reasoner, budget,
                                  corpus=corpus)
        if on_answer is not None:
            on_answer(q, ans)
        ok = _correct(q, ans)
        if q.authored_from == "life" and not q.gap and q.id not in invalid_emergent:
            per_type[q.qtype].append(ok)
        if not ok:
            failures.append(
                "## %s (%s, from %s)\nQ: %s\nGOLD: %s\nGOT: %s\nRETRIEVED: %s\n"
                % (q.id, q.qtype, q.authored_from, q.text,
                   " | ".join(q.gold) or "(abstain — gap)",
                   ans.text, ", ".join(i[:8] for i in ans.retrieved_ids[:8])))

    strata = {t: (sum(v) / len(v) if v else 0.0) for t, v in per_type.items()}
    report = {
        "strata": strata,
        "factual_correct": strata.get("factual", 0.0),
        "emergent_correct": strata.get("emergent", 0.0),
        "verbatim_correct": strata.get("verbatim", 0.0),
        "capture_gap_rate": query_set.gap_rate(questions),
        # Reuses the corpus that was just embedded, and says so: without
        # this it re-embedded the whole store a second time and then compared
        # everything to everything.
        "novelty_curve": compression_probe.novelty_curve(
            log, embedder, claims=corpus.claims, vectors=corpus.vectors),
        "n_questions": len(questions),
        "n_gate_eligible": len(query_set.gate_eligible(questions)) - len(invalid_emergent),
        "invalid_emergent": sorted(invalid_emergent),   # mislabeled: single-claim gold
        "budget": budget,
    }
    v = decision_gate.verdict(report, prior_upstream_loops)
    report["verdict"] = v.code
    report["verdict_reason"] = v.reason

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "report.json"), "w") as f:
            json.dump(report, f, indent=2, sort_keys=True)
        with open(os.path.join(out_dir, "failures.md"), "w") as f:
            f.write("# failures — every one is a requirement\n\n" +
                    ("\n".join(failures) or "(none)\n"))
    return report
