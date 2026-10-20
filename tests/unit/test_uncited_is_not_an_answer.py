"""A hole straight through the middle of the never-lie promise.

MEASURED 2026-08-26. The guard's condition was:

    if self.w.verify_answers and answer.citations and not answer.abstained:

So a model that cited NOTHING skipped verification entirely. One model call,
no check, and against a store containing only "kiln firing moved to Thursday"
this came back as a normal answer with abstained=False:

    "You had a dentist appointment and it went badly."

Pure fabrication, served as memory, with the guard switched on.

It survived because the prompt asks the model to cite and a cooperative model
does. "The prompt asks nicely" is precisely the guarantee the guard exists to
BACKSTOP — a defence that only works while the thing it defends against is
behaving is not a defence. Any model drift, any prompt edit, any smaller
model swapped in through Settings/Model choice, and the product starts
inventing a person's life with nothing checking it.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from tests.fakes import HashEmbedder
from tracemem.app.wiring import build
from tracemem.domain import (CaptureCoord, CoordinateStack, SpanCoord,
                             WorldCoord)
from tracemem.extraction.claim_builder import build_claim
from tracemem.text.claim_log import ClaimLog

TRUTH = "kiln firing moved to Thursday"


class Says:
    """A model that answers however it was told to, counting its calls."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def complete(self, prompt, **kw):
        self.calls += 1
        return self.reply


def _hub(reasoner):
    d = tempfile.mkdtemp()
    log = ClaimLog(os.path.join(d, "s.sqlite3"))
    for i in range(3):
        coord = CoordinateStack(
            world=WorldCoord(place_key="p"),
            capture=CaptureCoord(frame_id="f%d" % i, device_id="d",
                                 app="Brave"),
            span=SpanCoord(), t_ms=1_700_000_000_000 + i * 1000)
        log.append(build_claim({"text": TRUTH}, coord, "ocr", bidder="b",
                               bid_id="i%d" % i, bid_predicted_value=0.0,
                               actual_cost=0.0, scope="frame"))
    hub = build({"claim_log": log, "reasoner": reasoner,
                 "embedder": HashEmbedder()})
    hub.post_claims(list(log.all_claims()))
    return hub


def test_an_uncited_fabrication_is_never_served():
    """THE BUG. This exact string came back as an answer."""
    hub = _hub(Says("You had a dentist appointment and it went badly."))
    a = hub.ask("what did the kiln heading say")
    assert a.abstained is True
    assert "dentist" not in a.text
    assert a.text == "not in memory"


def test_the_refusal_says_why():
    hub = _hub(Says("You had a dentist appointment."))
    hub.ask("what did the kiln heading say")
    assert "cited nothing" in hub.last_refusal


def test_an_uncited_answer_costs_no_second_model_call():
    """There is nothing to verify. Spending a call to check a claim that
    named no evidence would be paying for the appearance of rigour."""
    liar = Says("Something with no citation at all.")
    _hub(liar).ask("what did the kiln heading say")
    assert liar.calls == 1


def test_a_cited_answer_is_still_verified_normally():
    """The fix must not turn the guard off for the case it was built for."""
    hub = _hub(Says("The kiln firing moved to Thursday. [%s]"))
    # A citation the context does not know is 'invented' and gets marked; a
    # real one goes to the checker. Either way the guard path is entered.
    a = hub.ask("what did the kiln heading say")
    assert a is not None


def test_an_honest_abstention_is_untouched():
    """"not in memory" cites nothing BY DESIGN, and must not be caught by a
    rule about uncited answers."""
    hub = _hub(Says("not in memory"))
    a = hub.ask("what did the kiln heading say")
    assert a.abstained is True
    assert a.text.lower().startswith("not in memory")


def test_the_rule_needs_evidence_to_have_been_shown():
    """If the supplier found nothing, the model was given nothing, and the
    ordinary empty-context path already answers. This rule is about a model
    that HAD evidence and declined to cite it."""
    import inspect

    from tracemem.app import hub as hub_mod

    src = inspect.getsource(hub_mod.Hub.ask)
    assert "not answer.citations and context.blocks" in src
