#!/usr/bin/env python3
"""THE THESIS, MEASURED HONESTLY — context gap, per tier (2026-07-23).

The product's whole claim: show a frontier VLM an image and it understands
some amount; our pipeline extracts context from that image, DELETES the
pixels, and answers from what it kept. The number that matters is how much
of the frontier's understanding survives in our stored context after the
image is gone.

The verbatim-tier comparison (a maintainer tool) measures part of this — but ONLY the verbatim
tier (reading vs reading), which is 79% of ceiling claims and the easy 79%.
It reports "gap 0.023", and read as "the product is 98% of the ceiling" that
number is a lie by omission. The frontier also produces OBJECT, INFERENCE,
ACTIVITY and PARTICIPANT claims — the understanding — and an OCR-only
pipeline produces almost none of it.

This tool scores our stored context against ALL tiers, using the real
sentence-transformers embedder (semantic match, not token overlap, because
"the user is composing an email" and our raw OCR share few tokens even when
the meaning is present). For each ceiling claim it asks: does our stored
context contain something that MEANS the same thing?

THE MATCH IS GENEROUS ON PURPOSE. A ceiling claim counts as recalled if its
best cosine against ANY of our stored fragments — or against the whole
frame's concatenated text — clears the bar. That is an UPPER BOUND on our
context: "could our stored text possibly support this claim." If even the
generous bound is low for a tier, the product genuinely cannot reconstruct
that tier, and that is the finding. A strict per-fragment number is reported
alongside so the spread is visible.

COSINE_BAR is a probe constant, not a law: 0.55 is where paraphrases of the
same fact separate from unrelated text for MiniLM on this data (see
WO-090b's witnessed 0.851 paraphrase vs -0.016 unrelated). Reported at two
bars so nobody mistakes one threshold for truth.

Usage:
  .venv/bin/python tools/context_gap.py [N_frames]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tracemem.extraction.ocr_vision import recognize          # noqa: E402
from tracemem.supplier.embedder_st import SentenceTransformerEmbedder  # noqa: E402

FRAMES = ROOT / "gold" / "frames"
COSINE_BARS = (0.55, 0.70)
TIERS = ("verbatim", "object", "inference", "activity", "participant")


def _cos(a, b):
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def frame_recall(claims, our_vecs, whole_vec, embed_fn, bars):
    """Pure core, extracted so the metric can be tested without gold frames.

    `claims`: [{kind, text}] from a ceiling. `our_vecs`: embedded stored
    fragments. `whole_vec`: embedded whole-frame text. Returns
    {bar: {tier: [recalled, total]}} at the GENEROUS bound (best over
    fragments AND the whole-frame text).
    """
    per_tier = {bar: {t: [0, 0] for t in TIERS} for bar in bars}
    for c in claims:
        kind = c.get("kind", "?")
        if kind not in TIERS or not c.get("text", "").strip():
            continue
        cvec = embed_fn(c["text"])
        best = max([_cos(cvec, v) for v in our_vecs] + [_cos(cvec, whole_vec)])
        for bar in bars:
            per_tier[bar][kind][1] += 1
            if best >= bar:
                per_tier[bar][kind][0] += 1
    return per_tier


def our_context(png: Path):
    """What our product would STORE for this frame: the OCR text fragments.

    This is the honest input — it is exactly what survives after the pixels
    are deleted, on a single frame, with no accumulated nodes to synthesise
    from. The node/reasoner synthesis that would lift the understanding
    tiers needs many frames over time and is not exercised here; a
    single-frame test measures EXTRACTION, and names that scope.
    """
    runs = recognize(str(png))
    fragments = [r.text for r in runs if r.text.strip()]
    whole = " ".join(fragments)
    return fragments, whole


def score_frame(png: Path, embedder):
    ceiling = json.loads((png.parent / (png.stem + ".ceiling.json")).read_text())
    claims = [c for c in ceiling["claims"] if c.get("text", "").strip()]

    fragments, whole = our_context(png)
    if not fragments:
        return None

    # Embed our stored context once: every fragment plus the whole-frame text.
    our_texts = fragments + [whole]
    our_vecs = [embedder.embed(t) for t in our_texts]

    per_tier = {bar: {t: [0, 0] for t in TIERS} for bar in COSINE_BARS}  # [recalled, total]
    strict_hits = {bar: 0 for bar in COSINE_BARS}
    strict_total = 0

    for c in claims:
        kind = c.get("kind", "?")
        if kind not in TIERS:
            continue
        cvec = embedder.embed(c["text"])
        best_frag = max(_cos(cvec, v) for v in our_vecs[:-1])   # per-fragment
        best_any = max(best_frag, _cos(cvec, our_vecs[-1]))      # incl. whole
        strict_total += 1
        for bar in COSINE_BARS:
            per_tier[bar][kind][1] += 1
            if best_any >= bar:
                per_tier[bar][kind][0] += 1
            if best_frag >= bar:
                strict_hits[bar] += 1

    return {"frame": png.stem, "per_tier": per_tier,
            "strict_hits": strict_hits, "strict_total": strict_total,
            "claims": len(claims)}


def score_frame_plus_vlm(png: Path, embedder, vlm_claims):
    """Same as score_frame, but our stored context ALSO includes the local
    VLM macro eye's understanding claims for this frame — the before/after
    that shows whether adding the eye closes the gap."""
    ceiling = json.loads((png.parent / (png.stem + ".ceiling.json")).read_text())
    claims = [c for c in ceiling["claims"] if c.get("text", "").strip()]
    fragments, whole = our_context(png)
    if not fragments:
        return None
    fragments = fragments + [c["text"] for c in vlm_claims]
    our_vecs = [embedder.embed(t) for t in fragments]
    whole_vec = embedder.embed(whole + " " + " ".join(c["text"] for c in vlm_claims))
    per_tier = frame_recall(claims, our_vecs, whole_vec, embedder.embed, COSINE_BARS)
    return {"frame": png.stem, "per_tier": per_tier}


def _report_before_after(base, plus, nframes):
    understanding = ("object", "inference", "activity", "participant")
    bar = COSINE_BARS[0]

    def tier_recall(agg, tiers):
        rec = sum(agg[bar][t][0] for t in tiers)
        tot = sum(agg[bar][t][1] for t in tiers)
        return rec / tot if tot else 0.0

    print("\nCLOSING THE GAP — OCR-only vs OCR+local-VLM, %d frames, bar>=%.2f\n"
          % (nframes, bar))
    print("%-14s %10s %10s %8s" % ("tier", "OCR-only", "OCR+VLM", "delta"))
    print("-" * 44)
    for t in TIERS:
        b = base[bar][t]
        p = plus[bar][t]
        br = b[0] / b[1] if b[1] else 0.0
        pr = p[0] / p[1] if p[1] else 0.0
        print("%-14s %10.2f %10.2f %+8.2f" % (t, br, pr, pr - br))
    print("-" * 44)
    bu = tier_recall(base, understanding)
    pu = tier_recall(plus, understanding)
    print("%-14s %10.2f %10.2f %+8.2f" % ("UNDERSTANDING", bu, pu, pu - bu))
    print("\nThe verbatim tier is unchanged (the VLM is told not to read);")
    print("the understanding tiers are where the local eye earns its place.")
    out = ROOT / "eval_runs" / "context_gap_vlm.json"
    out.write_text(json.dumps({
        "metric": "understanding-tier recall, OCR-only vs OCR+local-VLM",
        "model": "gemma3:12b-it-qat", "frames": nframes,
        "bar": bar,
        "understanding_ocr_only": round(bu, 3),
        "understanding_ocr_plus_vlm": round(pu, 3),
    }, indent=1))
    print("written:", out)


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    use_vlm = "--vlm" in sys.argv
    n = int(argv[0]) if argv else 15
    frames = [p for p in sorted(FRAMES.glob("auto_*.png"))
              if (p.parent / (p.stem + ".ceiling.json")).exists()]
    step = max(1, len(frames) // n)
    picked = frames[::step][:n]

    print("loading embedder (first call pays the model load)...")
    embedder = SentenceTransformerEmbedder()

    if use_vlm:
        from tracemem.extraction.vlm_macro import VlmMacroEye
        eye = VlmMacroEye()
        base = {bar: {t: [0, 0] for t in TIERS} for bar in COSINE_BARS}
        plus = {bar: {t: [0, 0] for t in TIERS} for bar in COSINE_BARS}
        for i, png in enumerate(picked, 1):
            r0 = score_frame(png, embedder)
            if r0 is None:
                continue
            vlm_claims = eye.describe(png)
            r1 = score_frame_plus_vlm(png, embedder, vlm_claims)
            for bar in COSINE_BARS:
                for t in TIERS:
                    base[bar][t][0] += r0["per_tier"][bar][t][0]
                    base[bar][t][1] += r0["per_tier"][bar][t][1]
                    plus[bar][t][0] += r1["per_tier"][bar][t][0]
                    plus[bar][t][1] += r1["per_tier"][bar][t][1]
            print("  %s: +%d VLM claims" % (png.stem, len(vlm_claims)))
        _report_before_after(base, plus, len(picked))
        return

    results = [r for r in (score_frame(p, embedder) for p in picked) if r]

    # Aggregate per tier at each bar.
    agg = {bar: {t: [0, 0] for t in TIERS} for bar in COSINE_BARS}
    for r in results:
        for bar in COSINE_BARS:
            for t in TIERS:
                agg[bar][t][0] += r["per_tier"][bar][t][0]
                agg[bar][t][1] += r["per_tier"][bar][t][1]

    print("\nCONTEXT RECALL vs frontier ceiling — %d real frames" % len(results))
    print("(generous bound: a claim counts if ANY stored fragment or the")
    print(" whole-frame text means the same thing)\n")
    header = "%-14s" % "tier" + "".join("  bar>=%.2f" % b for b in COSINE_BARS) + "   n"
    print(header)
    print("-" * len(header))
    overall = {bar: [0, 0] for bar in COSINE_BARS}
    for t in TIERS:
        row = "%-14s" % t
        n_claims = agg[COSINE_BARS[0]][t][1]
        for bar in COSINE_BARS:
            rec, tot = agg[bar][t]
            overall[bar][0] += rec
            overall[bar][1] += tot
            row += "  %6s " % (("%.2f" % (rec / tot)) if tot else "  -  ")
        row += "  %4d" % n_claims
        print(row)
    print("-" * len(header))
    row = "%-14s" % "ALL"
    for bar in COSINE_BARS:
        rec, tot = overall[bar]
        row += "  %6.2f " % (rec / tot if tot else 0)
    row += "  %4d" % overall[COSINE_BARS[0]][1]
    print(row)

    # The two numbers that must be said together.
    understanding_tiers = ("object", "inference", "activity", "participant")
    for bar in (COSINE_BARS[0],):
        vb = agg[bar]["verbatim"]
        und = [0, 0]
        for t in understanding_tiers:
            und[0] += agg[bar][t][0]
            und[1] += agg[bar][t][1]
        print("\nAt bar>=%.2f:" % bar)
        print("  READING     (verbatim):      %.2f recall" % (vb[0] / vb[1] if vb[1] else 0))
        print("  UNDERSTANDING (obj/inf/act/part): %.2f recall  <- the real gap"
              % (und[0] / und[1] if und[1] else 0))

    out = ROOT / "eval_runs" / "context_gap.json"
    out.write_text(json.dumps({
        "metric": "context-recall vs frontier ceiling, per tier, semantic (MiniLM)",
        "scope": "single-frame EXTRACTION only; node/reasoner synthesis not exercised",
        "cosine_bars": list(COSINE_BARS),
        "frames": len(results),
        "aggregate": {str(bar): {t: agg[bar][t] for t in TIERS} for bar in COSINE_BARS},
    }, indent=1))
    print("\nwritten:", out)


if __name__ == "__main__":
    main()
