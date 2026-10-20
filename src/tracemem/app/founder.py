"""OWNS: the founder's own voice, entering memory as EVIDENCE (F-GRADE).

THE RULING THIS IMPLEMENTS, and it is a one-line law worth holding onto:

    A founder grade changes what is TRUE in the graph.
    It never changes how the graph DECIDES.

The founder asked for the digest to be graded and for the grades to feed back,
then delegated the mechanism ("you work it out and tell me what I need to do").
Three mechanisms were available and two of them are traps:

  · tuning the knobs (combination priors, RESOLVE_BAR, cold_floor) would teach
    the brain to produce beliefs he approves of rather than beliefs that are
    true — and the whole product is the ability to be SURPRISED by your own
    memory. A brain optimised for approval cannot surprise you. Refused.
  · a pure scorecard changes nothing and improves nothing.
  · **evidence** — the grade becomes a CLAIM, at the top of the grade scale,
    and everything downstream re-derives from it. Chosen.

WHY VERBATIM AND NOT A NEW TIER. `domain/grading.py` already reserves the
`founder` slot at Grade.VERBATIM, and its docstring argues the case: grade
answers "how faithful is this to what was perceived", provenance answers "who
perceived it". A founder statement is maximally faithful testimony. Adding a
sixth tier above VERBATIM would silently re-rank every existing comparison,
because G1/G2/G3 all depend on the ordering.

WHY OWNED AND NOT LIVED. A typed correction was not observed on a screen — it
has no frame, so a LIVED claim would be structurally invalid (`Claim.__post_init__`
enforces that). It is a record the user authored, which is precisely what the
OWNED world is for, and its `source` locator names the digest line it answers
so the fork is re-readable forever.

WHAT THE GRADE DOES NOT DO: it does not delete, edit or overwrite the belief it
judges. Raw is immutable and the meta tier is corrected by CONTRADICTION, not
by erasure — a wrong belief that is contradicted by higher-grade evidence loses
on the merits and is retired by the night, leaving a tombstone that can still
answer "you used to think X". Nothing here reaches into the graph.
"""
from __future__ import annotations

from typing import Optional

from tracemem.domain import (
    CaptureCoord, Claim, Confidence, CoordinateStack, Modality, Provenance,
    ProvenanceClass, SpanCoord, WorldCoord,
)
from tracemem.domain.grading import grade_for

PERCEIVER = "founder"

# The three verdicts. Deliberately small: a grading ritual the founder does
# every morning must cost seconds, and a scale with seven points is a scale
# nobody uses consistently.
VERDICTS = ("right", "wrong", "meh")

_SAYS = {
    "right": "The founder confirms: %s",
    "wrong": "The founder says this is WRONG: %s",
    "meh": "The founder finds this not worth knowing: %s",
}


class UnknownVerdict(ValueError):
    pass


def answer_claim(question_text: str, said: str, t_ms: int = 0,
                 question_id: str = "", device_id: str = "mac-1") -> Claim:
    """The founder answering a question the MEMORY asked.

    The other direction from `ask_claim`, and the more valuable one. TRACE
    noticed something it could not settle — two people who might be one
    person, a name it could not place — and asked. What comes back is
    testimony no sensor could ever produce, so it lands at VERBATIM like any
    other thing the founder says.

    THE ANSWER IS THE TEXT AND THE QUESTION IS THE CONTEXT, in that order.
    A reader (and an embedder) should meet the true statement first and the
    thing it settles second — the same rule `grade_claim` follows, for the
    same reason: this sentence is going to be the highest-graded statement
    about its subject in the entire store.
    """
    text = "%s (asked: %s)" % (said.strip(), question_text.strip())
    coord = CoordinateStack(
        world=WorldCoord(place_key=None),
        capture=CaptureCoord.owned(
            source="founder:answered:%s" % (question_id or t_ms),
            device_id=device_id),
        span=SpanCoord(bbox=None),
        t_ms=t_ms,
    )
    return Claim(
        text=text, coord=coord,
        provenance=Provenance(perceiver=PERCEIVER, modality=Modality.SENSOR,
                              klass=ProvenanceClass.OWNED),
        grade=grade_for(PERCEIVER),          # THE table, never a literal
        confidence=Confidence(1.0),
        # `founder_grade` marks a JUDGEMENT for supplier/corrections.py. An
        # answer to the memory's own question IS a judgement about the world
        # — it settles something — so it carries one, unlike a stored ask.
        structured={"founder_grade": "answered",
                    "about_question": question_id,
                    "asked": question_text.strip()},
        bidder=PERCEIVER, bid_id="founder:answered:%s" % (question_id or t_ms),
        bid_predicted_value=0.0, actual_cost=0.0,
        scope="memory",
    )


def ask_claim(question: str, answer=None, t_ms: int = 0,
              device_id: str = "mac-1") -> Claim:
    """One question the founder asked → one OWNED claim about wondering it.

    A question IS testimony. "who is Juno" is the founder's own words
    about what he wanted to know, and what someone wondered on a Tuesday is
    a real fact about that Tuesday — often a better index into the day than
    anything that was on screen. It also gives follow-ups something to
    resolve against and gives search something to find.

    THE ANSWER IS DELIBERATELY NOT IN THE CLAIM'S TEXT.
    ==================================================
    This is the whole design and it is a safety property, not a style choice.
    `text` is what retrieval matches on and what gets packed into a context
    as evidence. Put the model's answer there and the memory can retrieve its
    own output as an observation about the world — a machine citing itself,
    where one confident mistake becomes a source, gets re-read tomorrow as
    corroboration, and hardens into a fact nobody ever witnessed. It would
    look exactly like a memory that had learned something.

    So the answer lives in `structured`, where it is durable, re-readable and
    inert: it is kept for the founder's history and for follow-ups, and it
    can never be mistaken for something the machine SAW.

    Note the asymmetry with `grade_claim`, and that it is deliberate: a
    CORRECTION belongs in the text, because the founder said it. An ANSWER
    does not, because a model said it.
    """
    said = 'You asked: "%s"' % question.strip()
    coord = CoordinateStack(
        world=WorldCoord(place_key=None),
        capture=CaptureCoord.owned(
            source="founder:asked:%d" % t_ms, device_id=device_id),
        span=SpanCoord(bbox=None),
        t_ms=t_ms,
    )
    cites = tuple(str(c) for c in (getattr(answer, "citations", ()) or ()))
    return Claim(
        text=said, coord=coord,
        provenance=Provenance(perceiver=PERCEIVER, modality=Modality.SENSOR,
                              klass=ProvenanceClass.OWNED),
        grade=grade_for(PERCEIVER),          # THE table, never a literal
        confidence=Confidence(1.0),
        # `asked` is the marker that separates a question from a judgement.
        # supplier/corrections.py keys off `founder_grade`, so a stored
        # question must never carry one — otherwise every past ask would be
        # injected into every future context as though it were a correction.
        structured={"asked": question.strip(),
                    "answered": str(getattr(answer, "text", "") or ""),
                    "answer_citations": list(cites),
                    "abstained": bool(getattr(answer, "abstained", False))},
        bidder=PERCEIVER, bid_id="founder:asked:%d" % t_ms,
        bid_predicted_value=0.0, actual_cost=0.0,
        scope="memory",                      # wondering is not a frame read
    )


def grade_claim(line, verdict: str, correction: str = "",
                t_ms: int = 0, device_id: str = "mac-1") -> Claim:
    """One graded digest line → one OWNED claim in the founder's own voice.

    `line` is a digest.DigestLine. `correction` is the one sentence the
    founder types when he marks something wrong — and that sentence, not the
    verdict, is the valuable part: it is the highest-graded statement in the
    entire store the moment it lands.
    """
    if verdict not in VERDICTS:
        raise UnknownVerdict(
            "verdict must be one of %r, got %r — never guess what a person "
            "meant about their own memory" % (VERDICTS, verdict))

    said = _SAYS[verdict] % line.text
    if correction.strip():
        # The correction leads. A reader (and an embedder) should meet the
        # true statement first and the thing it corrects second.
        said = "%s (about: %s)" % (correction.strip(), line.text)

    coord = CoordinateStack(
        world=WorldCoord(place_key=None),
        # The source names the exact line this answers, so the fork stays
        # re-readable: a grade is only meaningful against what it graded.
        capture=CaptureCoord.owned(
            source="founder:digest:%s" % line.id, device_id=device_id),
        span=SpanCoord(bbox=None),
        t_ms=t_ms,
    )
    return Claim(
        text=said, coord=coord,
        provenance=Provenance(perceiver=PERCEIVER, modality=Modality.SENSOR,
                              klass=ProvenanceClass.OWNED),
        grade=grade_for(PERCEIVER),          # THE table, never a literal
        confidence=Confidence(1.0),
        structured={"founder_grade": verdict, "about_line": line.id,
                    "about_node": line.node_id,
                    "corrected": bool(correction.strip())},
        bidder=PERCEIVER, bid_id="founder:%s:%d" % (line.id, t_ms),
        bid_predicted_value=0.0, actual_cost=0.0,
        scope="memory",                      # a judgement is not a frame read
    )
