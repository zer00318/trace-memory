"""The most-asked question had the fewest protections.

MEASURED 2026-08-26 against a snapshot of the real store. `window_answer.py`
contained no reference to the never-lie guard at all — no bluff_check, no
verify, nothing. A grep for "guard|bluff|verify|corrections" returned
nothing.

So "what did I do yesterday?" — the single most-asked question in this
product, the one a person tries first — was answered by a local model over
packed evidence with NOTHING checking that what it said was in that evidence.
Every other path has been guarded since July.

It is the worst possible place for the gap to be, and the hardest to notice,
precisely because that path answers well most of the time.
"""
from __future__ import annotations

from tracemem.app.window_answer import WindowAnswerer
from tracemem.llm.answer import Answer


class Clock:
    def now_ms(self):
        return 1_700_000_000_000


class Log:
    def __init__(self, claims=()):
        self._c = list(claims)

    def all_claims(self):
        return list(self._c)

    def read_range(self, a, b):
        return list(self._c)


class Says:
    def __init__(self, reply, verdict="no"):
        self.reply = reply
        self.verdict = verdict
        self.calls = 0

    def complete(self, prompt, **kw):
        self.calls += 1
        # The guard's checker asks a second, different question.
        if "does the fragment" in prompt.lower() or "unsupported" in prompt.lower():
            return self.verdict
        return self.reply


def _answerer(reasoner, **kw):
    return WindowAnswerer(lambda: Log(), reasoner, Clock(), **kw)


def test_the_module_now_knows_the_guard_exists():
    """The grep that came back empty."""
    import inspect

    from tracemem.app import window_answer

    src = inspect.getsource(window_answer)
    assert "bluff_guard" in src
    assert "find_corrections" in src


def test_the_guard_is_on_by_default():
    """A defence you have to remember to enable is off when it matters."""
    assert _answerer(Says("x"))._verify is True


def test_an_uncited_answer_is_never_served():
    """Nothing about it can be checked, so the evidence is shown instead —
    which cannot fabricate, because it IS the context."""
    from tracemem.supplier.packer import Context, CtxBlock
    from tracemem.domain import Grade

    a = _answerer(Says("You went to the dentist and it went badly."))
    ctx = Context((CtxBlock(kind="claim", text="kiln firing on Thursday",
                            grade=Grade.VERBATIM, source_ids=("abc123",),
                            t_ms=1),), 10)
    out = a._guard(Answer(text="You went to the dentist.", citations=(),
                          abstained=False), "what did I do?", ctx)
    assert "dentist" not in out.text
    assert "could not summarise" in out.text
    assert "cited nothing" in a.last_refusal


def test_a_refused_summary_falls_back_to_the_evidence():
    """For a summary, "not in memory" would be a lie of a different kind: the
    memory is holding the evidence and saying it holds nothing."""
    from tracemem.supplier.packer import Context, CtxBlock
    from tracemem.domain import Grade

    a = _answerer(Says("prose", verdict="the whole thing is made up"))
    ctx = Context((CtxBlock(kind="claim", text="kiln firing on Thursday",
                            grade=Grade.VERBATIM, source_ids=("abc123",),
                            t_ms=1),), 10)
    out = a._guard(Answer(text="Something unsupported. [abc123]",
                          citations=("abc123",), abstained=False),
                   "what did I do?", ctx)
    assert "kiln firing on Thursday" in out.text
    assert out.abstained is False


def test_a_guard_that_stumbles_does_not_serve_the_unverified_answer():
    """The failure direction. If the check cannot be completed, the answer it
    was going to check is exactly the thing not to hand over."""
    from tracemem.supplier.packer import Context, CtxBlock
    from tracemem.domain import Grade

    class Angry:
        def complete(self, prompt, **kw):
            raise RuntimeError("model wedged")

    a = _answerer(Angry())
    ctx = Context((CtxBlock(kind="claim", text="evidence line",
                            grade=Grade.VERBATIM, source_ids=("abc123",),
                            t_ms=1),), 10)
    out = a._guard(Answer(text="claim [abc123]", citations=("abc123",),
                          abstained=False), "q?", ctx)
    assert "evidence line" in out.text
    assert a.last_refusal


def test_an_abstention_is_left_alone():
    a = _answerer(Says("x"))
    given = Answer(text="I was not watching yesterday.", citations=(),
                   abstained=True)
    assert a._guard(given, "q?", None) is given


def test_citation_grades_are_recorded_for_the_face():
    """They came back empty on the real store because the socket read the
    BRAIN's fields for an answer the brain did not write."""
    a = _answerer(Says("x"))
    assert a.last_citation_grades == {}


def test_the_prompt_says_why_citing_matters():
    """MEASURED on the real store: the model sometimes answered with no [id]
    at all, so the guard refused it and the person got raw evidence instead
    of a summary. The old rule asked for citations in passing and got them in
    passing.

    Stated as a consequence rather than a request, because a model that knows
    WHY a requirement exists follows it more often than one told to comply.
    """
    from tracemem.llm.prompt import WINDOW_RULES

    assert "cannot be checked" in WINDOW_RULES
    assert "thrown away" in WINDOW_RULES
    assert "EVERY sentence" in WINDOW_RULES


def test_a_correction_can_actually_be_cited():
    """The trap two correct halves made together.

    The corrections block always carried source_ids on its CtxBlock and never
    printed one in its text, while every timeline moment prints its own. So a
    model answering from the founder's own correction — which that block
    tells it outranks everything else — had nothing to cite.

    Harmless until the window path started demanding a citation per sentence.
    Then the highest-authority evidence in the context became the only
    evidence that could not be quoted, and answers built on it came back
    uncited and were refused.
    """
    from tracemem.supplier.corrections import render

    class Claim:
        id = "abc12345deadbeef"
        text = "It moved to Friday, not Thursday."

    out = render([Claim()])
    assert "[abc12345]" in out
    assert "It moved to Friday" in out
