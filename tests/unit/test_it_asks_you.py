"""The questions the memory has for you.

TRACE has generated these since July (nodes/birth/question_gen.py) — two
people who might be one person, a name it cannot place — and they piled up in
`hub.pending_questions` where nothing ever read them. A memory that notices
its own confusion and has no way to mention it is just a memory that stays
confused.
"""
from __future__ import annotations

from tracemem.domain.question import Question, QuestionStatus
from tracemem.supplier.questions import (MAX_SHOWN, evidence_for, find,
                                         open_questions, render)


def _q(qid, text="Is this the same Sam?", gain=0.5, about=(),
       status=QuestionStatus.QUEUED):
    return Question(id=qid, text=text, about_node_ids=tuple(about),
                    info_gain=gain, created_t_ms=1000, status=status)


class Store:
    def __init__(self, labels=None):
        self._l = labels or {}

    def get_node(self, nid):
        from tracemem.domain import Confidence, Grade
        from tracemem.domain.node import Attribute, Node, NodeKind
        if nid not in self._l:
            return None
        return Node(id=nid, kind=NodeKind.ENTITY, attributes=[
            Attribute(key="seen_as", value=self._l[nid], grade=Grade.VERBATIM,
                      confidence=Confidence(0.9), source_episode_ids=("ep1",),
                      t_ms=1000)])


# -- nothing to ask is a good state ---------------------------------------

def test_no_questions_renders_nothing_at_all():
    """None rather than a heading with nothing under it — an empty panel
    reads as a malfunction, and having no questions is fine."""
    assert render([], Store()) is None
    assert render(None, Store()) is None


def test_an_answered_question_is_not_asked_again():
    """A question that stays open after being answered teaches a person that
    answering does nothing."""
    qs = [_q("q1", status=QuestionStatus.ANSWERED)]
    assert open_questions(qs) == []
    assert render(qs, Store()) is None


def test_an_expired_question_is_not_asked_either():
    assert open_questions([_q("q1", status=QuestionStatus.EXPIRED)]) == []


# -- what it shows ---------------------------------------------------------

def test_the_most_valuable_question_comes_first():
    """The generator already scores what settling one would buy. Arrival
    order would spend a person's patience on the cheapest first."""
    qs = [_q("cheap", gain=0.1), _q("dear", gain=0.9), _q("mid", gain=0.5)]
    assert [q.id for q in open_questions(qs)] == ["dear", "mid", "cheap"]


def test_it_does_not_show_twenty_at_once():
    """Twenty questions is not a list anyone answers, it is a list they
    close."""
    qs = [_q("q%d" % i, gain=i / 100.0) for i in range(20)]
    assert len(open_questions(qs)) == MAX_SHOWN


def test_every_question_shows_why_it_is_asking():
    """"Is this the same Sam as before?" is unanswerable without seeing which
    Sams. Answering must take seconds, so the reason is on screen."""
    store = Store({"n1": "Sam Patel", "n2": "Sam"})
    q = _q("q1", about=("n1", "n2"))
    lines = evidence_for(q, store)
    assert any("Sam Patel" in l for l in lines)
    assert any("Sam" in l for l in lines)
    page = render([q], store)
    assert "Sam Patel" in page
    assert "./trace ask answer q1" in page


def test_a_missing_node_does_not_break_the_panel():
    q = _q("q1", about=("gone", "alsogone"))
    assert evidence_for(q, Store()) == []
    assert render([q], Store()) is not None


def test_the_panel_says_an_answer_is_evidence():
    page = render([_q("q1")], Store())
    assert "evidence" in page and "Nothing is edited" in page


# -- naming one -----------------------------------------------------------

def test_a_question_is_found_by_prefix():
    qs = [_q("abc12345"), _q("def67890")]
    assert find(qs, "abc123").id == "abc12345"


def test_an_ambiguous_prefix_settles_nothing():
    """Answering a different question from the one that was read, with no
    sign anything was chosen, puts words in someone's mouth."""
    assert find([_q("abc1"), _q("abc2")], "abc") is None


def test_an_empty_id_names_nothing():
    assert find([_q("abc1")], "") is None


# -- answering one --------------------------------------------------------

def test_an_answer_is_the_founders_own_testimony():
    """What comes back is testimony no sensor could produce — two people
    being one person is not something a screen can show. So it lands at
    VERBATIM like anything else the founder says."""
    from tracemem.app.founder import answer_claim
    from tracemem.domain import Grade, ProvenanceClass

    c = answer_claim("Is this the same Sam as before?",
                     "Yes, both are Sam Patel from work.",
                     t_ms=5, question_id="q1")
    assert c.grade is Grade.VERBATIM
    assert c.provenance.klass is ProvenanceClass.OWNED
    assert c.text.startswith("Yes, both are Sam Patel from work.")
    assert "Is this the same Sam" in c.text
    assert c.structured["about_question"] == "q1"


def test_the_answer_leads_and_the_question_follows():
    """A reader and an embedder should meet the true statement first and the
    thing it settles second. This sentence becomes the highest-graded claim
    about its subject in the whole store."""
    from tracemem.app.founder import answer_claim

    c = answer_claim("Who is Juno?", "My sister.", t_ms=5)
    assert c.text.index("My sister.") < c.text.index("Who is Juno?")


def test_an_answer_counts_as_a_judgement_for_retrieval():
    """It settles something about the world, unlike a stored question — so
    supplier/corrections must carry it into future contexts."""
    from tracemem.app.founder import answer_claim
    from tracemem.supplier.corrections import is_correction

    assert is_correction(answer_claim("q?", "the truth", t_ms=5)) is True


def test_it_is_store_legal(tmp_path):
    """The birth-stamp gate rejects loudly; an answer must pass it or every
    reply the founder gives is silently dropped."""
    from tracemem.app.founder import answer_claim
    from tracemem.text.claim_log import ClaimLog

    log = ClaimLog(str(tmp_path / "s.sqlite3"))
    log.append(answer_claim("q?", "the truth", t_ms=5, question_id="q1"))
    assert len(list(log.all_claims())) == 1


def test_the_ration_oracle_verbatim():
    """`Birth / Questions to you / The question ration`, its oracle exactly:
    50 pending questions + a cap of 5 → exactly 5 surface, highest-value
    first, each with its evidence attached."""
    store = Store({"n%d" % i: "Sam %d" % i for i in range(50)})
    qs = [_q("q%02d" % i, gain=i / 100.0, about=("n%d" % i,))
          for i in range(50)]

    shown = open_questions(qs, limit=5)

    assert len(shown) == 5
    assert [q.id for q in shown] == ["q49", "q48", "q47", "q46", "q45"]
    for q in shown:
        assert evidence_for(q, store), "a question surfaced with no evidence"
