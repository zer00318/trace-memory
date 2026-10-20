"""OWNS: THE table — which source earns which grade (map: Text / Grading
rules per source).

Written once, obeyed everywhere. Every claim builder imports this; none
carries a grade literal of its own. The failure that motivates the rule is
laundering: a builder that defaults an unknown source to VERBATIM turns a
guess into testimony, and nothing downstream can tell the difference ever
again — the grade is the ONLY record of how much the system should be
trusted about that sentence.

An unknown source is a CONSTRUCTION ERROR, not a default. That is the
whole design: adding a perceiver forces a deliberate decision about what
its output is worth, made here, in daylight, next to all the others.

ON "your answers → gold" (the brief's phrasing): the grade scale
(domain/grade.py) has five ordered tiers and no gold. Rather than bolt on a
sixth — the ordering is load-bearing, G1/G2/G3 depend on it, and a new top
tier would silently re-rank every existing comparison — a founder answer
lands at VERBATIM and is distinguished by its PROVENANCE (perceiver
"founder"), which is where "who said it" belongs. Grade answers "how
faithful is this to what was perceived"; provenance answers "who perceived
it". A founder statement is maximally faithful testimony, which is exactly
VERBATIM. Recorded here rather than silently deviating from the brief.

SPEC: 00 §2, 01 §11
ORACLE: tests/unit/test_grading.py + the grep invariant that no builder
        carries its own grade literal.
"""
from __future__ import annotations

from tracemem.domain.grade import Grade

# source family -> the grade its output EARNS.
GRADE_BY_SOURCE = {
    # -- faithful transcription: the machine copied, it did not interpret --
    "ocr": Grade.VERBATIM,          # pixels -> text, character-faithful
    "asr": Grade.VERBATIM,          # transcript (word-faithful, not tone)
    "founder": Grade.VERBATIM,      # the human's own answer — see docstring
    "witness": Grade.VERBATIM,      # the system observing ITSELF (app/witness.py)
    "ax": Grade.VERBATIM,           # the macOS Accessibility tree. Strictly
                                    # BETTER than ocr and the table cannot say
                                    # so: VERBATIM is the ceiling, and ocr
                                    # already holds it. The difference is that
                                    # OCR has a transcription error mode and
                                    # this has NONE — the OS hands over the
                                    # exact string the app is displaying, so
                                    # 'chess.com' can never arrive as
                                    # 'esa.com'. Grade measures fidelity, not
                                    # the absence of a failure mode, so where
                                    # the two disagree the tie is broken by
                                    # PRECEDENCE at birth, not by grade.
    "owned": Grade.VERBATIM,        # a native read of the user's OWN file — the
                                    # claim's text is copied character-faithful
                                    # from the source (like OCR of a document);
                                    # OWNED-ness is provenance, not fidelity

    # -- domain readers: narrow, trained, better than gist in their lane --
    "specialist": Grade.SPECIALIST,  # board state, code symbols, sheet music

    # -- summary: lossy on purpose ----------------------------------------
    "gist": Grade.GIST,             # scene summary
    "vlm": Grade.GIST,              # a general model describing a frame

    # -- reasoning over other claims: never outranks its weakest parent ---
    "reasoner": Grade.DERIVED,      # an LLM's opinion about stored claims
    "marination": Grade.DERIVED,    # night-shift consolidation
}


def retracted() -> Grade:
    """The grade of a statement this system has TAKEN BACK.

    Not a grade anything EARNS — it is where a value goes when the machine
    decides it was wrong. Retiring is not the same act as perceiving, and the
    table above is only about the second.

    It lives here anyway, because the whole point of that table is that no
    builder names a grade value on its own. A module that wrote
    `a.grade = Grade.FLOOR` inline would be doing exactly that, and the grep
    guard rightly fails it — a rule with one convenient exception is a
    convention.

    WHY DEMOTE RATHER THAN DELETE. `label_of` picks the highest-graded value,
    so a retracted one can never stand for its node again — but it is still
    there to be read, counted and argued with. That is the difference between
    a memory that revised itself and one that rewrote itself.
    """
    return Grade.FLOOR


def grade_for(source_name: str) -> Grade:
    """The grade this source earns. Raises on an unknown family.

    `source_name` may carry a qualifier after a colon ("specialist:chess"),
    which the family lookup strips — a specialist is a specialist whatever
    it specialises in.
    """
    family = str(source_name).split(":", 1)[0].lower()
    if family not in GRADE_BY_SOURCE:
        raise ValueError(
            f"unknown source family {family!r} — a grade must be EARNED, "
            f"never defaulted. Add it to GRADE_BY_SOURCE "
            f"(domain/grading.py) with a comment saying what its output is "
            f"worth. Known: {sorted(GRADE_BY_SOURCE)}")
    return GRADE_BY_SOURCE[family]


def known_sources():
    return sorted(GRADE_BY_SOURCE)
