"""The onboarding copy, pinned to what actually happens.

`Onboarding / Honest expectations` has an unusual oracle and it is the whole
point: THE COPY MUST MATCH WHAT A DAY-ONE ASK ACTUALLY RETURNS.

The first ten minutes decide whether anyone keeps this running, and the first
ask is the moment the product is least able to impress. Every instinct is to
write around that — "your memory is building", "come back soon" — and that is
where a memory learns to oversell. A thing that will exaggerate about itself
on day one will exaggerate about your life on day thirty.

So these tests run a COLD store and check the screen against it.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ))

from tracemem.app.onboarding import (DAY_ONE_ABSTENTION,
                                     DAY_ONE_NO_PERCEPTION, render)


@pytest.fixture
def cold_hub():
    from tests.fakes import ContextAnswerReasoner, HashEmbedder
    from tracemem.app.wiring import build
    from tracemem.text.claim_log import ClaimLog

    d = tempfile.mkdtemp()
    return build({"claim_log": ClaimLog(os.path.join(d, "s.sqlite3")),
                  "reasoner": ContextAnswerReasoner(),
                  "embedder": HashEmbedder()})


def test_a_cold_ask_refuses_exactly_as_the_screen_says(cold_hub):
    """If someone softens the abstention wording, this fails — and it should,
    because the screen quotes it."""
    a = cold_hub.ask("who is Juno?")
    assert a.abstained is True
    assert DAY_ONE_ABSTENTION in a.text
    assert DAY_ONE_ABSTENTION in render()


def test_a_cold_period_question_says_it_was_not_watching(cold_hub):
    """The good sentence this screen has to live up to. It distinguishes
    "nothing happened" from "I was not watching" on day one, unprompted."""
    a = cold_hub.ask("what did I do yesterday?")
    assert DAY_ONE_NO_PERCEPTION in a.text
    assert DAY_ONE_NO_PERCEPTION in render()


def test_day_one_never_wraps_an_honest_sentence_in_an_apology(cold_hub):
    """MEASURED before this leaf existed: the day-one answer was

        There is not enough here to summarise, so here is everything
        memory holds:

        I have no perception at all for yesterday...

    The second half is right. The first half is a wrapper bolted onto a
    sentence that IS the answer — and on day one it is the first thing
    anybody reads."""
    a = cold_hub.ask("what did I do yesterday?")
    assert "not enough here to summarise" not in a.text
    assert "could not summarise" not in a.text
    assert a.text.startswith("I have no perception")


def test_a_cold_ask_invents_nothing(cold_hub):
    """The refusal IS the product. An empty store answering anything at all
    about a person's life would be pure fabrication."""
    for q in ("who is Juno?", "which apps do I use most?",
              "what is lichess.org to me?"):
        a = cold_hub.ask(q)
        assert a.abstained is True, "it answered %r from an empty store" % q
        assert not a.citations


def test_the_screen_promises_nothing_it_cannot_do():
    """Everything the copy tells someone to run must exist."""
    text = render()
    for promised in ("./trace ask --moment", "./trace wipe", "That's wrong"):
        assert promised in text
    # And it must not promise the two things that are still founder-owned.
    assert "always on" not in text.lower()
    assert "phone" not in text.lower()


def test_the_screen_says_the_refusal_is_the_point():
    text = render()
    assert "will not guess" in text
    assert "cites" in text
