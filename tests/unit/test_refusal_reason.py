"""A refusal must say what it refused, in the checker's own words.

Until now a guard refusal was anonymous. `unsupported_in` returns the exact
sentence it judged unsupported; `is_grounded` collapsed that to a bool one
line later, and the answer disappeared with no record of why.

Three refusals have been chased down by hand so far. Every one was the guard
being RIGHT about something upstream had handed it:

  · a neighbour listed twice, so the prose said it twice;
  · a database type name ("ENTITY") leaking into evidence the model then
    repeated as if it were a fact about the world;
  · a rule in the dossier prompt demanding the model state what the evidence
    does NOT show — which the checker then flagged as unsupported.

Each cost a manual re-run with an instrumented copy of the checker. The
string already existed at the moment of refusal. Carrying it out costs no
extra model call and no extra latency.
"""
from __future__ import annotations

from tracemem.llm.answer import Answer
from tracemem.llm import bluff_check


class Reasoner:
    """Says the answer is ungrounded, naming one sentence."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def complete(self, prompt, **kw):
        self.calls += 1
        return self.reply


def _answer():
    return Answer(text="Hana is your sister.", citations=("abc123",),
                  abstained=False)


def test_the_reason_reaches_the_note():
    seen = []
    out = bluff_check.guard(
        _answer(), "who is Hana?", {"abc123": "Hana — a person"},
        Reasoner("Hana is your sister."), mode="synthesis",
        note=seen.append)
    assert out.abstained
    assert len(seen) == 1
    assert "Hana is your sister." in seen[0]
    # The citation is named too — with several blocks, "something was
    # unsupported" does not say which piece of evidence to go look at.
    assert "abc123" in seen[0]


def test_nothing_is_noted_when_the_answer_stands():
    seen = []
    out = bluff_check.guard(
        _answer(), "who is Hana?", {"abc123": "Hana — a person"},
        Reasoner("none"), mode="synthesis", note=seen.append)
    assert not out.abstained
    assert seen == []


def test_needle_mode_reports_too():
    seen = []
    out = bluff_check.guard(
        _answer(), "who is Hana?", {"abc123": "Hana — a person"},
        Reasoner("no"), mode="needle", note=seen.append)
    assert out.abstained
    assert seen and "abc123" in seen[0]


def test_no_note_is_not_an_error():
    """Every existing caller passes no note. None of them may break."""
    out = bluff_check.guard(
        _answer(), "who is Hana?", {"abc123": "Hana — a person"},
        Reasoner("Hana is your sister."), mode="synthesis")
    assert out.abstained


def test_a_broken_note_cannot_break_the_answer():
    """A face that crashes while explaining a refusal is strictly worse than
    one that refuses quietly."""
    def boom(_):
        raise RuntimeError("the log is on fire")

    out = bluff_check.guard(
        _answer(), "who is Hana?", {"abc123": "Hana — a person"},
        Reasoner("Hana is your sister."), mode="synthesis", note=boom)
    assert out.abstained
    assert out.text == "not in memory"


def test_the_checker_is_not_called_twice_to_get_the_reason():
    """The reason is a by-product, not a second opinion.

    A second model call to log a string would double the latency of every
    refusal — that trade was proposed once in this codebase and rejected.
    One block, one call.
    """
    r = Reasoner("Hana is your sister.")
    bluff_check.guard(_answer(), "who is Hana?",
                      {"abc123": "Hana — a person"}, r,
                      mode="synthesis", note=lambda _: None)
    assert r.calls == 1


# -- through the REAL Brain, not a stub -----------------------------------

def test_the_real_brain_exposes_what_the_hub_recorded():
    """THIS CAUGHT A SHIPPED BUG.

    `ask_socket` reads `self._brain.last_refusal`; the value is set on the
    HUB; and Brain had no delegation. The socket read an attribute that did
    not exist, so every refusal in production reported no reason at all.
    Worse, the socket clears the field before each question — which quietly
    CREATED it on the Brain as "" and kept it there, making the failure look
    exactly like "no refusal happened".

    The tests above passed throughout, because they use a stub brain that
    carries the attribute directly. They prove the socket reads a field; only
    a test through the real object can prove the real object has one.
    """
    import os
    import sys
    import tempfile

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from tests.fakes import ContextAnswerReasoner, HashEmbedder

    from tracemem.app.brain import Brain
    from tracemem.app.wiring import build
    from tracemem.text.claim_log import ClaimLog

    d = tempfile.mkdtemp()
    log = ClaimLog(os.path.join(d, "s.sqlite3"))
    hub = build({"claim_log": log, "reasoner": ContextAnswerReasoner(),
                 "embedder": HashEmbedder()})
    brain = Brain(hub, log.append, lambda: 1)

    hub.last_refusal = "[abc123] a made-up sister"
    assert brain.last_refusal == "[abc123] a made-up sister"

    hub.last_citation_grades = {"abc12345": 4}
    assert brain.last_citation_grades == {"abc12345": 4}

    # And the socket's pre-question clear must reach the hub, not shadow it
    # with a Brain-local attribute that never changes again.
    brain.last_refusal = ""
    assert hub.last_refusal == ""
    assert brain.last_refusal == ""
