"""OWNS: the bluff check — a verification pass on a cited answer (2026-07-23).

The never-lie promise cannot rest on a refusal RATE (a rate never proves
"never") and cannot rest on cosine relevance either — measured 2026-07-23,
a real answer scored question-block cosine 0.181 while a bluff scored 0.342,
so no geometric threshold separates them. What DOES separate them is asking:
does this block actually contain what the question asked for?

So this is a second, cheap model call whose ONLY job is to check the first
one's citation. It is deliberately not the answering model reasoning freely —
it is a narrow yes/no on a single fragment, which is the kind of judgement a
small model is reliable at even when open-ended answering tempts it to
invent. If the check says the fragment does not answer the question, the
answer is downgraded to an honest abstention: the product would rather say
"not in memory" than return a cited fabrication.

The check FAILS CLOSED. A malformed or ambiguous verifier reply is treated
as "not supported" — when the honesty of an answer is in doubt, the promise
is kept by withholding it, never by shipping it and hoping.
"""
from __future__ import annotations

import re

from tracemem.llm.answer import Answer


def _says_yes(raw: str) -> bool:
    """An unambiguous yes, and nothing else counts.

    Both guards used `low.startswith("yes") and "no" not in low.split()`, and
    a COMMA defeated it: "Yes, though no, parts are not in the fragment."
    splits to ["yes,", "though", "no,", ...] — plain "no" is not in that
    list, so a model hedging its way to a refusal was read as approval. A
    fabrication-catcher that punctuation defeats is not a catcher.
    """
    words = re.findall(r"[a-z]+", str(raw).strip().lower())
    return bool(words) and words[0] == "yes" and "no" not in words


VERIFY_PROMPT = (
    "You are a fact-checker for a memory system. It answered a question using "
    "one stored text fragment. Check ONE thing: does the FRAGMENT itself "
    "directly contain the information the QUESTION asks for? Not loosely "
    "related — actually contains the answer. Reply with exactly one word: "
    "yes or no.\n\n"
    "FRAGMENT: {block}\n"
    "QUESTION: {question}\n"
    "Does the fragment directly answer the question? "
)


# THE SECOND SHAPE OF THE SAME GUARD.
#
# `is_supported` asks "does the FRAGMENT directly contain what the QUESTION
# asks for". That is right for a needle question and WRONG for a summary, and
# the difference was measured on the live daemon 2026-08-23:
#
#   Q: what is lichess.org to me?
#   model: "Lichess.org is a site seen in 10 moments between 2026-07-29 and
#           2026-08-02 [f2cf5554]. It was seen alongside Brave Browser,
#           chess.org, search.brave.com... "        <- true, cited, useful
#   guard: is_supported -> False                    -> "not in memory"
#
# The dossier does not say what lichess.org MEANS to him, so the needle check
# correctly answers no, and a correct answer is destroyed by it. That is the
# mute-product failure PRIORITY_MASTER §3 names: a system that answers
# nothing scores identically to an honest one.
#
# The never-lie property is not weakened here, it is aimed. The guard exists
# to catch a FABRICATION WEARING A REAL ID, and for a summary the question
# that catches one is "does the answer state anything the fragment does not".
# ASK IT TO POINT AT THE LIE, not to render a verdict.
#
# The first version asked "is every statement in the ANSWER present in the
# FRAGMENT?" and a pedantic model reads that as string containment, so a
# faithful PARAPHRASE reads as unsupported. Measured 2026-08-24, the same
# question asked three times against an unchanged graph:
#
#     run 1  refused      run 2  passed      run 3  refused
#
# on an answer that added nothing — a ~67% false-refusal rate against the
# never-lie guard's own purpose. A guard that fires two times in three on
# true statements is not strict, it is broken, and before the evidence
# fallback existed each of those was "not in memory".
#
# AND IT MUST IGNORE META-STATEMENTS, because the answer prompts REQUIRE one.
# DOSSIER_RULES tells the model "if the dossier does not answer the question,
# say exactly what it does show and what it does not" — and the checker then
# flagged "The dossier does not show anything else about Hana" as
# unsupported, deterministically, on three runs. Pedantically it is right: a
# statement about the ABSENCE of information is not present in the fragment.
# But two of this system's own instructions were contradicting each other,
# and the guard was winning. A claim about the evidence cannot be a
# fabrication about the founder's life.
#
# Naming the offending fact is much harder to do spuriously than saying "no",
# so the verdict comes with its reason and the refusal becomes inspectable
# instead of a coin flip. It still FAILS CLOSED: an empty or unreadable reply
# is treated as ungrounded.
GROUND_PROMPT = (
    "You are fact-checking a memory system. Below is a FRAGMENT of stored "
    "evidence and an ANSWER written from it.\n\n"
    "List any statement in the ANSWER that the FRAGMENT does not support.\n"
    "Paraphrasing, summarising, re-ordering and combining lines ARE "
    "supported — judge the information, not the wording. A statement is "
    "unsupported only if the fragment does not contain that information at "
    "all.\n"
    "IGNORE statements about the dossier or timeline ITSELF — \"the dossier "
    "does not show anything else\", \"no further detail is recorded\". Those "
    "describe the evidence, not the world, and cannot be a fabrication about "
    "the user's life. Judge only claims about what happened or what things "
    "are.\n"
    "If every statement is supported, reply with exactly: NONE\n"
    "Otherwise reply with one short line per unsupported statement.\n\n"
    "FRAGMENT:\n{block}\n\nANSWER:\n{answer}\n\nUNSUPPORTED:"
)


def unsupported_in(answer_text: str, block_text: str, reasoner) -> str:
    """What the checker says is unsupported — "" when nothing is."""
    raw = str(reasoner.complete(
        GROUND_PROMPT.format(block=block_text, answer=answer_text)) or "")
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        if re.sub(r"[^a-z]", "", line.lower()).startswith("none"):
            return ""
        return line[:200]
    return "the checker said nothing"          # fails closed


def is_grounded(answer_text: str, block_text: str, reasoner) -> bool:
    return unsupported_in(answer_text, block_text, reasoner) == ""


def is_supported(question: str, block_text: str, reasoner) -> bool:
    """One narrow yes/no. Fails closed: only a CLEAN 'yes' counts.

    'yes and no' must not pass — it starts with 'yes' but is a hedge, and a
    hedge on the honesty of an answer is a no. So a supported verdict needs a
    reply that begins with 'yes' AND contains no standalone 'no'. (Caught by
    test_it_fails_closed_on_a_garbled_verdict, which is why the naive
    startswith check is not enough.)
    """
    return _says_yes(reasoner.complete(
        VERIFY_PROMPT.format(block=block_text, question=question)))


def guard(answer: Answer, question: str, blocks_by_id: dict, reasoner,
          mode: str = "needle", note=None) -> Answer:
    """Return the answer if its citation checks out, else an abstention.

    `blocks_by_id`: {source_id: block_text} for the context that was shown.
    An answer that abstained, or that cited nothing, is returned unchanged —
    the guard only ever REMOVES unsupported claims, never adds one.

    `note`: called with the checker's own words when a refusal happens, and
    only then. THE REASON ALREADY EXISTS AND WAS BEING THROWN AWAY —
    `unsupported_in` returns the offending sentence, `is_grounded` reduced it
    to a bool one line later, and every refusal after that was anonymous.
    Every guard refusal investigated so far has turned out to be the guard
    being RIGHT about something upstream had put in front of it: a duplicated
    neighbour, a leaked database type, a rule in the prompt demanding a
    sentence the checker then flagged. Each one cost a manual re-run with an
    instrumented copy of this function to find. Passing the string out costs
    nothing — no extra model call, no extra latency — and turns the next one
    into a log line.
    """
    if answer.abstained or not answer.citations:
        return answer

    if mode == "synthesis":
        # A synthesis may legitimately need several cited blocks. Verify the
        # answer once against their UNION. The former per-block loop both
        # rejected cross-block summaries and allowed one broad block to excuse
        # an unsupported sentence elsewhere.
        cited_blocks, missing = [], []
        for cid in answer.citations:
            block = blocks_by_id.get(cid)
            if block is None:
                missing.append(cid)
            elif block not in cited_blocks:
                cited_blocks.append(block)
        why = ("citation has no supplied evidence: %s" % ",".join(missing)
               if missing else unsupported_in(
                   answer.text, "\n\n".join(cited_blocks), reasoner))
        if why == "":
            return answer
        if note is not None:
            try:
                note("[%s] %s" % (",".join(answer.citations), why))
            except Exception:                                # noqa: BLE001
                pass
        return Answer(text="not in memory", citations=(), abstained=True)

    reasons = []
    for cid in answer.citations:
        block = blocks_by_id.get(cid)
        if block is None:
            continue
        ok = is_supported(question, block, reasoner)
        if not ok:
            reasons.append("[%s] the block does not support the question"
                           % cid)
        if ok:
            return answer          # at least one citation genuinely holds

    if note is not None and reasons:
        # Guarded, because a face that crashes while explaining a refusal is
        # strictly worse than one that refuses quietly.
        try:
            note(" | ".join(reasons))
        except Exception:                                    # noqa: BLE001
            pass
    return Answer(text="not in memory", citations=(), abstained=True)
