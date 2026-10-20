"""ORACLE for the bluff check (llm/bluff_check.py).

The live verification call needs the GPU, so the yes/no accuracy is measured
by tools/lie_catch.py --verify on the board. What is here is the guard's
CONTROL LOGIC — the part that must be right regardless of what the verifier
model says: it fails closed, it only ever removes unsupported claims, and it
never touches an answer that already abstained.
"""
from tracemem.llm.answer import Answer
from tracemem.llm.bluff_check import guard, is_supported


class FakeReasoner:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def complete(self, prompt):
        self.calls += 1
        return self.reply


BLOCKS = {"good": "the kiln fires thursday", "bad": "DISASTER C"}


def answer(text, cites, abstained=False):
    return Answer(text=text, citations=cites, abstained=abstained)


def test_a_supported_citation_passes():
    r = FakeReasoner("yes")
    ans = guard(answer("the kiln fires thursday [good]", ("good",)),
                "when does the kiln fire?", BLOCKS, r)
    assert ans.abstained is False
    assert ans.citations == ("good",)


def test_an_unsupported_citation_becomes_an_abstention():
    """The bluff: a cited block that does not answer the question. The guard
    would rather say 'not in memory' than return a cited fabrication."""
    r = FakeReasoner("no")
    ans = guard(answer("DISASTER C [bad]", ("bad",)),
                "what did the notification say?", BLOCKS, r)
    assert ans.abstained is True
    assert ans.text == "not in memory"
    assert ans.citations == ()


def test_it_fails_closed_on_a_garbled_verdict():
    """When the honesty of an answer is in doubt, the promise is kept by
    withholding it, not by shipping it and hoping."""
    for reply in ("maybe", "", "I think so", "yes and no", "nope"):
        r = FakeReasoner(reply)
        ans = guard(answer("something [bad]", ("bad",)), "q?", BLOCKS, r)
        assert ans.abstained is True, "did not fail closed on %r" % reply


def test_an_already_abstained_answer_is_untouched_and_uncosted():
    r = FakeReasoner("no")
    original = answer("not in memory", (), abstained=True)
    ans = guard(original, "q?", BLOCKS, r)
    assert ans is original
    assert r.calls == 0, "spent a verification call on an answer that abstained"


def test_an_answer_with_no_citation_is_untouched():
    r = FakeReasoner("no")
    ans = guard(answer("some prose with no cite", ()), "q?", BLOCKS, r)
    assert r.calls == 0
    assert ans.abstained is False


def test_one_holding_citation_is_enough_to_keep_the_answer():
    """An answer citing two blocks survives if EITHER genuinely supports it —
    the guard removes fabrications, it does not demand every citation be
    perfect."""
    replies = iter(["no", "yes"])
    r = FakeReasoner(None)
    r.complete = lambda prompt: next(replies)
    ans = guard(answer("x [bad] [good]", ("bad", "good")), "q?", BLOCKS, r)
    assert ans.abstained is False


def test_is_supported_only_trusts_a_clear_yes():
    assert is_supported("q", "block", FakeReasoner("yes")) is True
    assert is_supported("q", "block", FakeReasoner("YES, clearly")) is True
    assert is_supported("q", "block", FakeReasoner("no")) is False
    assert is_supported("q", "block", FakeReasoner("unsure")) is False
