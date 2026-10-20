"""Questions are memory too — and answers must never become evidence.

`Ask/History` (campaign/tree, The faces): "Store every ask+answer in the
store itself — they're claims about what you wondered."

What someone wondered on a Tuesday is a real fact about that Tuesday, and
often a better index into the day than anything that was on screen.

THE LOAD-BEARING HALF IS WHAT IS *NOT* STORED AS TEXT.
=====================================================
`text` is what retrieval matches on and what gets packed into a context as
evidence. Put the model's answer there and the memory can retrieve its own
output as an observation about the world: one confident mistake becomes a
source, gets re-read tomorrow as corroboration, and hardens into a fact
nobody ever witnessed. It would look exactly like a memory that had learned
something, and nothing in the system would flag it.

So the answer lives in `structured` — durable, re-readable, inert.
"""
from __future__ import annotations

import pytest

from tracemem.app.founder import ask_claim
from tracemem.domain import Grade, ProvenanceClass


class Answer:
    def __init__(self, text, citations=(), abstained=False):
        self.text = text
        self.citations = citations
        self.abstained = abstained


ANS = Answer("You were reading about kiln schedules.", ("abc123", "def456"))


def test_a_question_is_the_founders_own_words():
    c = ask_claim("what was I reading?", ANS, t_ms=5)
    assert c.grade is Grade.VERBATIM
    assert c.provenance.klass is ProvenanceClass.OWNED
    assert 'You asked: "what was I reading?"' == c.text


def test_the_answer_is_never_in_the_claims_text():
    """THE anti-feedback-loop property. If this ever fails, the memory can
    cite itself and every future answer is quietly contaminated."""
    c = ask_claim("what was I reading?", ANS, t_ms=5)
    assert "kiln schedules" not in c.text
    assert "You were reading" not in c.text


def test_the_answer_is_still_kept():
    """Inert, not discarded — history and follow-ups both need it."""
    c = ask_claim("what was I reading?", ANS, t_ms=5)
    assert c.structured["answered"] == "You were reading about kiln schedules."
    assert c.structured["answer_citations"] == ["abc123", "def456"]
    assert c.structured["asked"] == "what was I reading?"
    assert c.structured["abstained"] is False


def test_an_abstention_is_recorded_as_one():
    """That the memory had nothing is itself worth remembering — it is how
    anyone later learns which questions this thing kept failing."""
    c = ask_claim("what is my bank balance?",
                  Answer("not in memory", (), True), t_ms=5)
    assert c.structured["abstained"] is True
    assert c.structured["answered"] == "not in memory"


def test_a_question_carries_no_verdict():
    """supplier/corrections.py keys off `founder_grade`. A stored question
    must never carry one, or every past ask would be injected into every
    future context as though the founder had asserted it."""
    c = ask_claim("who is Juno?", ANS, t_ms=5)
    assert "founder_grade" not in c.structured


def test_a_question_is_not_mistaken_for_a_correction():
    """The same provenance, a different kind of statement.

    A question and a correction are both genuinely the founder's own OWNED
    words. Only one of them is a claim about the world. Presenting "who is
    Juno?" as testimony that "outranks anything on screen" would let a
    question overrule what was actually observed about her.
    """
    from tracemem.app.digest import DigestLine
    from tracemem.app.founder import grade_claim
    from tracemem.supplier.corrections import is_correction

    asked = ask_claim("who is Juno?", ANS, t_ms=5)
    judged = grade_claim(DigestLine(section="believe", text="Juno is a cat"),
                         "wrong", "Juno is a person.", t_ms=6)
    assert is_correction(asked) is False
    assert is_correction(judged) is True


def test_it_is_store_legal(tmp_path):
    """The birth-stamp gate rejects loudly; a question must pass it without
    special-casing, or every ask silently fails to record."""
    from tracemem.text.claim_log import ClaimLog

    log = ClaimLog(str(tmp_path / "s.sqlite3"))
    log.append(ask_claim("what was I reading?", ANS, t_ms=5))
    got = list(log.all_claims())
    assert len(got) == 1
    assert got[0].structured["asked"] == "what was I reading?"
