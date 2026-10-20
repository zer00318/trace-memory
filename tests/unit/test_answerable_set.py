"""THE CONFOUND KILLER (priority MASTER #3).

`eval_runs/lie_catch.json` reads `refused 21/21, refusal_rate 1.0, verdict
"ZERO fabrications — earns the mouth here"`. That is not evidence of honesty.
**A system that answers nothing scores identically** — so does one with an
empty store, a broken retriever, or a mouth hardwired to return "not in
memory". Product truth #4 sits at the top of the founder's failure hierarchy
and was unmeasured in the only direction that could falsify it.

Refusal is a virtue only if the system COULD have answered. These tests hold
the two properties that make the pair of sets meaningful:

  1. the questions are answerable BY CONSTRUCTION — derived from the very
     blocks the model is handed, never authored and then self-graded;
  2. the combined verdict tells a MUTE system apart from an HONEST one, which
     is the single thing the old number could not do.
"""
from tracemem.evalx.answerable import (AnswerableQuestion, derive, score,
                                       verdict)


class _B:
    def __init__(self, text, sid):
        self.text = text
        self.source_ids = (sid,)


def _blocks():
    return [
        _B("rollercoasterxpert 1790", "962eea27"),
        _B("bingchilling413 1343", "e866f1d0"),
        _B("anntonnnyyy https://www.chess.com/game/", "e307de81"),
    ]


# ---- the questions are answerable by construction -------------------------

def test_every_question_is_answered_by_a_block_the_model_is_handed():
    """A refusal on this set can never be defensible caution: the answer is
    provably sitting in the context."""
    qs = derive(_blocks())

    assert qs
    by_id = {b.source_ids[0]: b.text for b in _blocks()}
    for q in qs:
        assert q.source_id in by_id
        assert q.anchor in by_id[q.source_id]


def test_the_gold_is_read_from_the_screen_not_written_by_me():
    """Hand-writing questions AND their answers and then grading against them
    is the oldest way to pass your own exam. Anchor and answer are both text
    the founder's screen actually produced."""
    qs = derive(_blocks())
    by_id = {b.source_ids[0]: b.text for b in _blocks()}

    for q in qs:
        for token in q.gold.split():
            assert token in by_id[q.source_id]
        assert q.anchor not in q.gold.split()


def test_an_anchor_shared_by_two_blocks_is_never_used():
    """LOAD-BEARING, not fussy. A repeated anchor makes the question genuinely
    ambiguous, so citing 'the other' block would be CORRECT while the scorer
    marked it wrong — and a metric that punishes right answers is worse than
    no metric, because it will be optimised against."""
    blocks = [_B("sharedtoken alpha", "aaaa1111"),
              _B("sharedtoken beta", "bbbb2222")]

    qs = derive(blocks)

    assert all(q.anchor.lower() != "sharedtoken" for q in qs)


def test_blocks_too_thin_to_ask_about_are_skipped_not_faked():
    assert derive([_B("ok", "aaaa1111"), _B("", "bbbb2222")]) == []


# ---- scoring: refusal is a FAILURE here -----------------------------------

def _q():
    return AnswerableQuestion(q="what?", anchor="rollercoasterxpert",
                              gold="1790", source_id="962eea27", why="")


def test_a_refusal_scores_as_not_answered():
    """The whole inversion. On the unanswerable set 'not in memory' is the
    right answer; here it is the failure, because the evidence was present."""
    s = score(_q(), "not in memory", citations=[])

    assert s["answered"] is False
    assert s["cited"] is False


def test_a_correct_citation_is_recognised():
    s = score(_q(), "You saw rollercoasterxpert 1790.", citations=["962eea27"])

    assert s["answered"] is True and s["cited"] is True
    assert s["gold_recall"] == 1.0


def test_citing_the_wrong_memory_does_not_count():
    """Text can be echoed out of the prompt; a correct citation cannot be
    guessed. That is why the citation is the sharper of the two signals."""
    s = score(_q(), "You saw rollercoasterxpert 1790.", citations=["deadbeef"])

    assert s["answered"] is True
    assert s["cited"] is False


def test_paraphrase_is_not_punished():
    """A model that paraphrases correctly is not lying, and a metric that
    punishes paraphrase would push the mouth toward parroting."""
    s = score(_q(), "Their rating showed as 1790.", citations=["962eea27"])

    assert s["answered"] and s["cited"]


# ---- the verdict: mute and honest must not look alike ---------------------

def test_a_mute_system_no_longer_passes():
    """THE ORACLE. This is the exact system the old 21/21 called honest: it
    refuses everything, including questions whose answers it was handed."""
    v = verdict(refusal_rate=1.0, answer_rate=0.0, cite_rate=0.0)

    assert "MUTE" in v
    assert "HONEST AND USEFUL" not in v


def test_an_honest_and_useful_system_passes():
    v = verdict(refusal_rate=1.0, answer_rate=0.9, cite_rate=0.9)

    assert v.startswith("HONEST AND USEFUL")


def test_one_fabrication_still_fails_however_good_the_rest():
    """A never-lie product is not satisfied by a rate — the existing tool
    already refuses to grade on that curve, and this must not soften it."""
    v = verdict(refusal_rate=0.95, answer_rate=1.0, cite_rate=1.0)

    assert v.startswith("FAIL")


def test_answering_without_citing_is_called_out():
    v = verdict(refusal_rate=1.0, answer_rate=1.0, cite_rate=0.1)

    assert "SHOWING ITS WORK" in v


def test_half_a_measurement_is_reported_as_no_measurement():
    """Either set alone is unfalsifiable. Saying so is the point of the whole
    exercise — the old file claimed a verdict from one half."""
    assert verdict(1.0, None, None).startswith("INCOMPLETE")
    assert verdict(None, 1.0, 1.0).startswith("INCOMPLETE")
