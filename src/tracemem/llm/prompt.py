"""OWNS: Context → prompt with VISIBLE grades and the abstention clause (05 §5)."""
from __future__ import annotations

# STRENGTHENED 2026-07-23 after tools/lie_catch.py caught the candidate mouth
# fabricating 5/21: it grabbed one vivid captured block ("DISASTER C") and
# reused it as the answer to unrelated unanswerable questions, citing a real
# id so the lie looked grounded. That is the exact failure product-truth #4
# forbids ("it NEVER lies to you").
#
# The counter is PRINCIPLED, not tuned to those 21 questions (tuning to the
# test is authoring your own pass). It states what the blocks actually are —
# short OCR fragments, usually unrelated — makes abstention the default, and
# names the specific dishonest move (repurposing a fragment about one thing
# as the answer to another). Measured against the same held-out set.
ABSTAIN = (
    'The MEMORY BLOCKS above are short OCR fragments from brief screen '
    'captures. They are OFTEN UNRELATED to the question, and MOST questions '
    'cannot be answered from them. Answer ONLY if a specific block DIRECTLY '
    'states the answer, and cite it like [id]. Do NOT repurpose a fragment '
    'about one topic as the answer to a different question. If you would be '
    'guessing, inferring, or reaching for a loosely related fragment, or the '
    'answer is simply not present, reply with exactly these three words and '
    'nothing else: not in memory')


def render_prompt(context, question: str) -> str:
    lines = []
    for b in context.blocks:
        src = ",".join(b.source_ids[:2])
        lines.append("(%s|%s|%s) %s" % (b.kind, b.grade.name, src, b.text))
    return "MEMORY BLOCKS:\n%s\n\n%s\n\nQUESTION: %s\nANSWER:" % (
        "\n".join(lines), ABSTAIN, question)


# THE SECOND QUESTION SHAPE. The prompt above is a NEEDLE prompt: it tells the
# model that most blocks are unrelated and that anything short of a direct
# statement must be refused. That is right for "what was the wifi password"
# and catastrophic for "what was I doing on August 3" — a timeline answers
# that question by being summarised, and the needle prompt refuses to
# summarise, so the honest guard turned into a mute product.
#
# The abstention discipline is NOT relaxed here; it is re-aimed. The model may
# describe and group what the timeline shows, and may not add a fact the
# timeline does not contain. "Say only what is on these lines" is a bar a
# summary can meet, unlike "quote a block that directly answers".
WINDOW_RULES = (
    "The TIMELINE above is what was actually on screen during the period "
    "asked about, read by the machine at the time. Each line is a stretch of "
    "time on one thing, with its [id], in CHRONOLOGICAL ORDER.\n"
    "Answer by SUMMARISING, not by listing. Say what the person was doing in "
    "a few sentences, in time order, leading with whatever took the longest. "
    "Group related lines into one statement — several lines about the same "
    "app, site or task are ONE activity, not several. Do not narrate the "
    "timeline line by line, do not repeat near-identical fragments, and "
    "never write 'before that' or 'earlier still'. Quote a `seen:` fragment "
    "only when it carries the actual content the question asks about.\n"
    # CITING IS NOT A FORMATTING PREFERENCE, and asking for it in passing got
    # it in passing. MEASURED 2026-08-26 on the real store: the model
    # sometimes answered with no [id] at all, and an uncited answer cannot be
    # verified, so it is refused and the person gets raw evidence instead of
    # a summary. The rule is stated as a consequence rather than a request,
    # because a model that knows WHY a requirement exists follows it more
    # often than one told to comply.
    "EVERY sentence you write must end with the [id] it came from, like "
    "[a3b4115f]. An answer with no [id] cannot be checked and will be thrown "
    "away unread — so a sentence you cannot cite is a sentence not worth "
    "writing. If you can only cite part of what you want to say, write only "
    "that part.\n"
    "You may ONLY say what these lines show. Do not add topics, opinions, "
    "reasons or people that are not written here, and never guess what "
    "happened in a gap. If the timeline is empty, or a COVERAGE line says "
    "the period was not watched, say exactly that instead of guessing."
)


def render_window_prompt(context, question: str) -> str:
    """A timeline plus the question it is meant to answer.

    The PERIOD header is not decoration. Without it the model has only the
    clock times on individual lines and no idea what span they are supposed
    to cover, so it narrates the few longest moments as though they were the
    whole day — measured on 2026-08-03, where an answer covering 23:06-23:59
    silently omitted everything from 07:31 onward.
    """
    lines = [b.text for b in context.blocks]
    moments = [b for b in context.blocks if b.kind == "moment"]
    header = ""
    if moments:
        first, last = moments[0], moments[-1]
        header = ("PERIOD: %d moment(s) recorded, first begins at the top of "
                  "the timeline and the last at the bottom.\n" % len(moments))
        del first, last
    return "TIMELINE:\n%s%s\n\n%s\n\nQUESTION: %s\nANSWER:" % (
        header, "\n".join(lines), WINDOW_RULES, question)


# THE THIRD QUESTION SHAPE: about a THING rather than a period or a needle.
#
# The needle prompt refuses anything a block does not directly state, which is
# right for "what was the wifi password" and wrong for "who is Juno" — the
# answer to that is an assembly of what is attached to her, and a prompt that
# forbids assembly turns a full dossier into "not in memory". Measured on the
# live daemon 2026-08-23: three of three entity questions refused over
# contexts that held the answer.
#
# The abstention discipline is re-aimed, not relaxed: describe what is on
# these lines, add nothing that is not.
DOSSIER_RULES = (
    "The DOSSIER above is everything this memory holds about the thing(s) "
    "asked about: how often each was seen, over what span, what it appeared "
    "alongside, what the nightly pass concluded, and text that was actually "
    "on screen. Each entry carries its [id].\n"
    "Answer by summarising what the dossier shows, and cite the [id] of "
    "every entry you use. Say how you know — 'seen in 7 moments alongside "
    "X' is a better answer than a bare assertion.\n"
    "You may ONLY say what these lines show. Do not add facts, roles, "
    "relationships or opinions that are not written here. If the dossier "
    "does not answer the question, say exactly what it does show and what "
    "it does not."
)


def render_dossier_prompt(context, question: str) -> str:
    lines = [b.text for b in context.blocks]
    return "DOSSIER:\n%s\n\n%s\n\nQUESTION: %s\nANSWER:" % (
        "\n".join(lines), DOSSIER_RULES, question)


# THE FOURTH SHAPE: an ORDER, not a fact. "What do I use most" is answered by
# counting, and the count carries a caveat that must survive into the answer —
# a moment is an occasion, not a duration.
RANKING_RULES = (
    "The RANKING above is what this memory holds, ordered by how many "
    "separate moments each thing appeared in.\n"
    "Answer with the order it shows, cite the durable [id] printed on every "
    "entry you use, and lead with the top few rather than reciting the whole "
    "list. Each id opens the graph node and its underlying moments.\n"
    "The counts are MOMENTS (occasions), NOT minutes or hours. Never convert "
    "them into time spent, and never say something was used 'for' any "
    "duration. If the question asks how long, say that this memory counts "
    "occasions rather than duration.\n"
    "You may ONLY use the entries shown. Do not add anything not listed."
)


def render_ranking_prompt(context, question: str) -> str:
    lines = [b.text for b in context.blocks]
    return "RANKING:\n%s\n\n%s\n\nQUESTION: %s\nANSWER:" % (
        "\n".join(lines), RANKING_RULES, question)
