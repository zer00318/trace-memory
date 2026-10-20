"""OWNS: making sure what the founder SAID reaches the answer.

THE DEFECT THIS EXISTS FOR, measured 2026-08-25. A founder correction is
appended to the store as an OWNED claim at VERBATIM — the highest grade
there is, in his own words — and the answer path could not see it. Bound,
durable, and structurally unreachable:

    IN LOG: 1
      text: The kiln firing actually moved to Friday. (about: ...)
      klass=OWNED place=None app=None frame=None

Retrieval is node-centric. A correction carries no app, no place and no
frame, so it births no node, so nothing walks to it. The single most
authoritative statement in the store was the one statement no question
could reach — while the face told the founder "TRACE will re-derive from
this tonight." A promise the machine could not keep.

WHY NOT SIMPLY GIVE CORRECTIONS COORDINATES. Because they do not have any.
A correction is not something that happened at a place; it is a judgement
ABOUT something that happened. Stamping it with the corrected claim's app
and frame would make the founder's sentence look like a screen observation,
which is a lie about provenance in a store whose whole value is that
provenance is never a lie.

SO THEY ARE FETCHED DIRECTLY. Corrections are rare — a human types them one
at a time — and they are the highest-graded thing in the store. Reading them
per question is cheap, and it is the only mechanism that satisfies the law
this product recorded for itself:

    "correction + re-ask → answer contains both the screen's claim and the
     correction, neither deleted."

BOTH, ALWAYS. This module never removes or replaces the observation it
contradicts. A memory that deleted what it saw when told it was wrong would
be a memory that edits its own past on request.
"""
from __future__ import annotations

import re
from typing import List, Optional

from tracemem.app.founder import PERCEIVER
from tracemem.domain import ProvenanceClass

# How many corrections may enter one context. A ceiling rather than a
# selection: if a founder has typed more than this about one subject, the
# most recent are the ones that still stand.
MAX_CORRECTIONS = 6
# The citation stamp length every other renderer uses. One number, so a
# citation a person reads in an answer matches the one shown in the context.
CITE_LEN = 8
# Words too common to mean anything as an overlap signal.
_STOP = frozenset(
    "the a an and or but of to in on at for with from was were is are be been "
    "it its this that these those i me my you your he she they them what when "
    "where who how why did do does done had have has not no yes about".split())


def _tokens(s: str):
    return {t for t in "".join(
        ch.lower() if ch.isalnum() else " " for ch in str(s)).split()
        if len(t) > 2 and t not in _STOP}


def is_correction(claim) -> bool:
    """A founder JUDGEMENT, not merely anything the founder said.

    Perceiver + OWNED is not enough and the distinction is load-bearing.
    Stored questions (app/founder.ask_claim) carry exactly the same
    provenance — the founder really did say them — so a looser test would
    pull every past question into every future context labelled "your own
    words, and they outrank anything on screen". A question is not a claim
    about the world, and presenting one as authority would let "who is
    Juno?" outrank what the screen actually showed about her.

    `founder_grade` is the marker, and only a judgement has one.
    """
    prov = getattr(claim, "provenance", None)
    if prov is None or getattr(prov, "perceiver", "") != PERCEIVER:
        return False
    if getattr(prov, "klass", None) is not ProvenanceClass.OWNED:
        return False
    structured = getattr(claim, "structured", None) or {}
    return bool(structured.get("founder_grade"))


def find_corrections(claim_log, question: str,
                     limit: int = MAX_CORRECTIONS) -> List:
    """Founder statements bearing on this question, newest last.

    Matched on word overlap with the question. A correction that shares no
    words with what was asked is not silently included: padding every context
    with unrelated founder opinions would spend the budget that the actual
    evidence needs, and would invite the model to connect things the founder
    never connected.
    """
    want = _tokens(question)
    if not want or claim_log is None:
        return []
    hits = []
    try:
        # Only founder claims can qualify, and `bidder` is an indexed
        # plaintext column: decrypt those, never the whole store (every
        # ask timed out at 120s on the founder's 635k claims, 2026-10-07).
        source = (claim_log.by_bidder(PERCEIVER)
                  if hasattr(claim_log, "by_bidder")
                  else claim_log.all_claims())
        for c in source:
            if not is_correction(c):
                continue
            if _tokens(c.text) & want:
                hits.append(c)
    except Exception:                                        # noqa: BLE001
        # A face must never lose an answer because the correction lookup
        # stumbled. Missing corrections degrade the answer; a raised
        # exception removes it.
        return []
    hits.sort(key=lambda c: getattr(getattr(c, "coord", None), "t_ms", 0))
    return hits[-limit:]


# A CORRECTION QUOTES WHAT IT CORRECTS, AND THAT QUOTE CAN CARRY CITATIONS.
#
# `founder.grade_claim` writes "<the truth> (about: <the line it corrects>)",
# and when the line corrected is an ANSWER, that quote contains the answer's
# [id] markers. Feed the correction back into a later context and the model
# sees plausible-looking ids that belong to a DIFFERENT question's evidence.
#
# WITNESSED 2026-08-28 on the real store. Asked "what did I do yesterday?"
# about a day with NO capture at all — the timeline said "I have no
# perception at all for yesterday" — the model produced a confident,
# detailed, entirely fabricated day, citing [9081b271], [7244b93a],
# [28378978] and four more. Every one of those ids was copied out of a
# previous answer quoted inside a correction.
#
# The guard caught it: none of those ids is in THIS context, so the answer
# counted as uncited and was refused. It worked. But a context that hands a
# model real-looking citations for evidence it was not given is a
# confabulation kit, and the guard should not be the only thing between it
# and a person.
_ID_RE = re.compile(r"\[[0-9a-f]{6,}\]", re.I)


def _without_stale_ids(text: str) -> str:
    """Strip [id] markers out of quoted text.

    The correction's OWN id is printed by the caller, so nothing citable is
    lost — what goes is a set of ids pointing at evidence this context does
    not contain.
    """
    return _ID_RE.sub("", text).replace("  ", " ").strip()


def render(claims) -> Optional[str]:
    """The corrections block, or None when there is nothing to say.

    Labelled as the founder's own words so the model can tell testimony from
    observation. It is told that both stand, because the alternative — "this
    replaces what you saw" — is the deletion this product refuses.
    """
    if not claims:
        return None
    out = ["WHAT YOU TOLD ME YOURSELF (your own words, and they outrank "
           "anything on screen — but the original observation still stands "
           "and is shown too):"]
    for c in claims:
        # THE [id] IS SHOWN, NOT MERELY CARRIED.
        #
        # This block always had source_ids on the CtxBlock and never printed
        # one in its text, while every timeline moment prints its own. So a
        # model answering from the founder's own correction — which this very
        # block tells it outranks everything else — had nothing to cite.
        #
        # That became a real failure the moment the window path started
        # demanding a citation per sentence: the highest-authority evidence
        # in the context was the only evidence that could not be quoted, so
        # answers built on it came back uncited and were refused. The two
        # halves were each right and together they were a trap.
        out.append("  [%s] %s" % (str(c.id)[:CITE_LEN],
                                  _without_stale_ids(str(c.text).strip())))
    return "\n".join(out)
