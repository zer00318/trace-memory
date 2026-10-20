"""Does the mouth answer, and how often does its own guard stop it?

`lie_catch` measures HONESTY — does it refuse what it cannot know. This
measures the other half: when it CAN know, does it say so. A memory that
refuses everything scores perfectly on the first and is useless.

WHY THIS EXISTS. The never-lie guard is a model call and it is not free of
error in either direction. Measured 2026-08-24, the same question against an
unchanged graph fell back to raw evidence 5 times in 6 — every one of them a
true, cited answer thrown away. That was invisible until it was counted:
each individual refusal looks like modesty.

Three outcomes per question, and the middle one is the interesting one:

    ANSWERED   prose, cited, past the guard
    EVIDENCE   the guard refused the prose; the raw evidence was shown
               instead (app/hub._evidence_only). Honest, less useful.
    DIRECT     there was never enough to summarise — one or two facts — so
               the evidence was shown without asking the model. Nothing went
               wrong; this is the intended answer for a thin subject.
    REFUSED    "not in memory" — correct for the control questions, a
               failure for the others.

DIRECT IS COUNTED SEPARATELY AND KEPT OUT OF THE REFUSAL RATE, deliberately.
Those questions never reached the guard, so folding them in would make the
guard look better every time a subject got thinner — a metric that improves
when the product learns less. The rate is over the questions that actually
faced the checker, and the DIRECT count is printed beside it so a rate
computed over three questions is never mistaken for one over six.

The controls are questions this memory genuinely cannot answer. They must
come back REFUSED; if they ever ANSWER, the guard has gone soft and that
matters more than any refusal rate.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOCK = Path.home() / ".trace" / "ask.sock"
OUT = ROOT / "eval_runs" / "answer_health.json"

# One per shape the supplier routes, plus controls.
QUESTIONS = [
    ("period", "what did I do yesterday?"),
    ("period", "what was I doing on August 3?"),
    ("thing", "who is Hana?"),
    ("thing", "what is lichess.org to me?"),
    ("order", "which apps do I use most?"),
    ("order", "what websites do I use the most?"),
    ("control", "what is my bank balance?"),
    ("control", "what did my doctor say at my last appointment?"),
]

FALLBACK_MARK = "could not summarise"          # the guard refused the prose
DIRECT_MARK = "not enough here to summarise"   # the model was never asked


def ask(question: str, timeout: float = 300.0) -> dict:
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(timeout)
    c.connect(str(SOCK))
    try:
        c.sendall((json.dumps({"op": "ask", "question": question}) + "\n")
                  .encode())
        buf = b""
        while b"\n" not in buf:
            chunk = c.recv(4096)
            if not chunk:
                break
            buf += chunk
    finally:
        c.close()
    return json.loads(buf.decode().strip() or "{}")


def classify(reply: dict) -> str:
    text = str(reply.get("text") or "")
    if reply.get("abstained") or text.strip().lower().startswith("not in memory"):
        return "REFUSED"
    if FALLBACK_MARK in text:
        return "EVIDENCE"
    if DIRECT_MARK in text:
        return "DIRECT"
    return "ANSWERED"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=1,
                    help="runs per question; >1 measures the guard's variance")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)

    if not SOCK.exists():
        print("no daemon on %s — start one with ./trace start" % SOCK,
              file=sys.stderr)
        return 2

    rows, t0 = [], time.time()
    for shape, q in QUESTIONS:
        for run in range(args.repeat):
            started = time.time()
            why = ""
            try:
                reply = ask(q)
                verdict = classify(reply)
                # The checker's own sentence, when there was one. A count of
                # refusals says the guard is biting; only the sentence says
                # what it bit, and every refusal chased down so far has been
                # the guard correctly objecting to something upstream fed it.
                why = str(reply.get("refused_because") or "")
            except Exception as exc:                          # noqa: BLE001
                verdict = "ERROR:%s" % type(exc).__name__
            rows.append({"shape": shape, "question": q, "run": run + 1,
                         "verdict": verdict, "refused_because": why,
                         "seconds": round(time.time() - started, 1)})
            print("  %-8s %-9s %-42s %.0fs"
                  % (shape, verdict, q[:42], rows[-1]["seconds"]), flush=True)
            if why:
                print("           ^ %s" % why[:120], flush=True)

    real = [r for r in rows if r["shape"] != "control"]
    ctrl = [r for r in rows if r["shape"] == "control"]
    answered = sum(1 for r in real if r["verdict"] == "ANSWERED")
    evidence = sum(1 for r in real if r["verdict"] == "EVIDENCE")
    direct = sum(1 for r in real if r["verdict"] == "DIRECT")
    refused = sum(1 for r in real if r["verdict"] == "REFUSED")
    faced_guard = answered + evidence
    leaked = [r for r in ctrl if r["verdict"] != "REFUSED"]

    summary = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "runs_per_question": args.repeat,
        "answerable": len(real),
        "answered": answered,
        "fell_back_to_evidence": evidence,
        "shown_directly": direct,
        "wrongly_refused": refused,
        "reached_the_guard": faced_guard,
        "guard_refusal_rate": (round(evidence / faced_guard, 3)
                               if faced_guard else None),
        "controls": len(ctrl),
        "controls_correctly_refused": len(ctrl) - len(leaked),
        "controls_that_answered": [r["question"] for r in leaked],
        "seconds": round(time.time() - t0, 1),
        # Grouped by shape, because the distribution is the actionable part.
        # The first run of this tool answered every period and ranking
        # question and fell back on both dossier questions — which named the
        # next thing to fix, and a bare 0.333 would not have.
        "by_shape": {sh: {v: sum(1 for r in rows
                                 if r["shape"] == sh and r["verdict"] == v)
                          for v in sorted({r["verdict"] for r in rows
                                           if r["shape"] == sh})}
                     for sh in sorted({r["shape"] for r in rows})},
        "refusals": [{"question": r["question"], "why": r["refused_because"]}
                     for r in rows if r.get("refused_because")],
    }
    # A control that answers is worse news than any refusal rate, so it is
    # the verdict rather than a footnote.
    if leaked:
        summary["verdict"] = ("GUARD IS SOFT — a question this memory cannot "
                              "answer was answered")
    elif refused:
        summary["verdict"] = "MUTE — an answerable question returned nothing"
    elif evidence:
        summary["verdict"] = ("USABLE — every answerable question produced "
                              "something true; %d of the %d that reached the "
                              "guard came back as raw evidence rather than "
                              "prose" % (evidence, faced_guard))
    else:
        summary["verdict"] = "HEALTHY — answered every answerable question"

    # A DAEMON OLDER THAN THIS FIELD RETURNS NO REASONS. Said out loud,
    # because "refusals: []" next to a refusal rate of 0.333 reads as "we
    # looked and there was nothing to see", when the truth is that nobody
    # asked. Silence and absence must not print the same.
    if summary["fell_back_to_evidence"] and not summary["refusals"]:
        summary["note"] = ("no reasons came back — the running daemon "
                           "predates `refused_because`; restart it to see "
                           "WHY each refusal happened")

    print("\n" + json.dumps(summary, indent=1))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"summary": summary, "rows": rows},
                                         indent=1))
    print("written: %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
