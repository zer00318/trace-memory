#!/usr/bin/env python3
"""THE ARENA (dry-run harness) — specs/node_tournament.md §protocol.

Runs any set of Contenders through the same claim stream and question
set, machine-checks every citation INDEPENDENTLY of the engines' own
validation (proof-audit spirit: the referee re-derives, never trusts),
and emits one scorecard per contender.

Dry-run mode uses a synthetic week with PLANTED facts. The real arena
run swaps in: the banked real week's claims + the sealed F4 questions +
the mouth/ceiling scoring — same harness, same scorecards.

Usage: .venv/bin/python tools/arena.py    (writes eval_runs/arena_dryrun.json)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.helpers import make_claim                      # noqa: E402
from tracemem.contenders import (                          # noqa: E402
    FutureAnswerMemory, UnderstandingMemory, WiringMemory,
)

MARINATE_BUDGET = 500      # same budget for every contender — fairness is the law


def build_week():
    """Synthetic week with planted facts; returns (claims, questions).
    Each question: {q, class, planted: [claim texts that answer it]}."""
    claims, plant = [], {}
    def add(text, t_ms, frame):
        c = make_claim(text=text, t_ms=t_ms, frame=frame)
        claims.append(c)
        return c
    for i in range(14):
        add(f"bought coffee at Marco cafe morning {i}", 8_000_000 + i * 86_400_00, f"f-cof-{i}")
    plant["wifi"] = [add("cafe wifi password is espresso99", 8_100_000, "f-wifi")]
    plant["sam"] = [add("Sam sent the Berlin contract draft", 9_000_000, "f-sam1"),
                    add("Sam confirmed the Berlin meeting for Thursday", 9_100_000, "f-sam2")]
    plant["deploy"] = [add("deploy failed with OOM on staging", 9_200_000, "f-dep1"),
                       add("raised memory limit fixed the staging deploy", 9_300_000, "f-dep2")]
    add("weather widget showed rain", 9_400_000, "f-noise1")
    add("spotify played jazz playlist", 9_500_000, "f-noise2")
    questions = [
        {"q": "what is the cafe wifi password?", "class": "factual", "planted": "wifi"},
        {"q": "what did Sam say about the Berlin meeting?", "class": "factual", "planted": "sam"},
        {"q": "what happened with the staging deploy?", "class": "cross-moment-why", "planted": "deploy"},
        {"q": "how many times did I buy coffee?", "class": "aggregate", "planted": None},
    ]
    return claims, questions, {k: {c.id for c in v} for k, v in plant.items()}


def run(contenders, claims, questions, plant_ids):
    valid_ids = {c.id for c in claims}
    cards = {}
    for m in contenders:
        t0 = time.perf_counter()
        m.ingest(claims)
        ingest_s = time.perf_counter() - t0
        t0 = time.perf_counter()
        m.marinate(MARINATE_BUDGET)
        marinate_s = time.perf_counter() - t0
        per_q, cited_valid, cited_total = [], 0, 0
        for spec in questions:
            t0 = time.perf_counter()
            pkg = m.supply(spec["q"])
            dt = time.perf_counter() - t0
            ids = pkg.cited_ids()
            cited_total += len(ids)
            cited_valid += len(ids & valid_ids)          # referee's own check
            hit = (spec["planted"] is None or
                   bool(ids & plant_ids[spec["planted"]]))
            per_q.append({"q": spec["q"], "class": spec["class"],
                          "blocks": len(pkg.blocks), "latency_ms": round(dt * 1000, 3),
                          "planted_hit": hit, "routed": list(pkg.route_to_store)})
        rep = m.storage()
        cards[m.name] = {
            "ingest_s": round(ingest_s, 4), "marinate_s": round(marinate_s, 4),
            "bytes": rep.bytes_used, "objects": rep.object_count,
            "citation_validity": (cited_valid / cited_total) if cited_total else 1.0,
            "recall_at_pack": sum(1 for r in per_q if r["planted_hit"]) / len(per_q),
            "questions": per_q,
        }
    return cards


def main():
    claims, questions, plant_ids = build_week()
    contenders = [UnderstandingMemory(), FutureAnswerMemory(), WiringMemory()]
    try:                                   # the incumbent joins when its deps import
        from tests.fakes import HashEmbedder
        from tracemem.contenders.design_zero import DesignZeroAdapter
        contenders.append(DesignZeroAdapter(HashEmbedder()))
    except Exception as e:                 # dry-run only; the real arena hard-requires it
        print(f"design_0 absent from dry run: {e}")
    cards = run(contenders, claims, questions, plant_ids)
    out = Path(__file__).resolve().parent.parent / "eval_runs" / "arena_dryrun.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(cards, indent=1))
    print(f"{'contender':10s} {'recall':>7s} {'cit.ok':>7s} {'bytes':>9s} {'objs':>5s}")
    for name, c in cards.items():
        print(f"{name:10s} {c['recall_at_pack']:7.2f} {c['citation_validity']:7.2f} "
              f"{c['bytes']:9d} {c['objects']:5d}")
    print(f"→ {out}")


if __name__ == "__main__":
    main()
