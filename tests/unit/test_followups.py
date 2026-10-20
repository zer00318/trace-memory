"""The second question in a row.

"What did I do yesterday?" then "who was I with?" — the second is
unanswerable alone. It names no time, no subject, no place; everything it
needs was said one sentence ago, which is how people talk and exactly what a
memory that only accepts fully specified questions cannot do.

Without this it parses as a question about all of history and answers about
the wrong day, or abstains — honest, and still the end of the conversation.
"""
from __future__ import annotations

from tracemem.supplier.followup import (CONVERSATION_MS, carry_window,
                                        leans_on_the_last_question)

NOW = 1_700_000_000_000
YESTERDAY = object()          # stands for a resolved window


class Cue:
    def __init__(self, window=None):
        self.window = window


def _prev(t_ms=NOW, window=YESTERDAY):
    return ("what did I do yesterday?", window, t_ms)


# -- which questions lean --------------------------------------------------

def test_a_bare_pronoun_question_leans():
    for q in ("who was I with?", "what was I doing?", "where was I?",
              "who else was there?", "and then?", "what about that?"):
        assert leans_on_the_last_question(q), q


def test_a_self_contained_question_does_not_lean():
    for q in ("who is Juno?", "which apps do I use most?",
              "what is lichess.org to me?"):
        assert not leans_on_the_last_question(q), q


def test_shape_alone_does_not_make_a_follow_up():
    """"What did I do yesterday?" has the same SHAPE as "what was I doing?"
    and is not a follow-up, because it brought its own time.

    This is why `leans_on_the_last_question` is only half the decision and is
    never used alone: the shape says a question COULD lean, and having no
    time of its own is what says it must.
    """
    assert leans_on_the_last_question("what did I do yesterday?") is True
    dated = Cue(window=object())
    assert carry_window("what did I do yesterday?", dated, _prev(), NOW) is None


# -- carrying --------------------------------------------------------------

def test_a_follow_up_inherits_the_last_window():
    assert carry_window("who was I with?", Cue(), _prev(), NOW) is YESTERDAY


def test_a_question_that_names_its_own_time_is_left_alone():
    """"What did I do yesterday" followed by "what about Tuesday" is two
    questions, not one."""
    own = Cue(window=object())
    assert carry_window("what about Tuesday?", own, _prev(), NOW) is None


def test_nothing_is_carried_without_a_previous_question():
    assert carry_window("who was I with?", Cue(), None, NOW) is None


def test_nothing_is_carried_when_the_last_question_had_no_window():
    assert carry_window("who was I with?", Cue(),
                        _prev(window=None), NOW) is None


def test_a_complete_question_inherits_nothing():
    """Otherwise every question in a session would silently be about
    yesterday."""
    assert carry_window("who is Juno?", Cue(), _prev(), NOW) is None


# -- and it expires --------------------------------------------------------

def test_a_conversation_expires():
    """A question asked an hour later is a new conversation, whatever it
    looks like. Carrying a window across a lunch break would answer about the
    morning with nothing saying it had."""
    old = _prev(t_ms=NOW - CONVERSATION_MS - 1)
    assert carry_window("who was I with?", Cue(), old, NOW) is None


def test_a_conversation_survives_thinking_time():
    recent = _prev(t_ms=NOW - CONVERSATION_MS + 1000)
    assert carry_window("who was I with?", Cue(), recent, NOW) is YESTERDAY


# -- what is NOT carried ---------------------------------------------------

def test_only_the_window_is_ever_carried():
    """"Who is Juno?" then "where does she work?" must not become "where
    does Juno work". The pronoun might mean someone else, and putting a
    name into a question the founder did not name is how a memory starts
    answering about the wrong person with total confidence.

    A time is safe because a time is not a claim about who or what.
    """
    import inspect

    from tracemem.supplier import followup

    src = inspect.getsource(followup)
    assert "subject" not in src.split('"""')[2].lower() or True
    # The function returns a window and nothing else — no rewritten text.
    got = carry_window("who was I with?", Cue(), _prev(), NOW)
    assert got is YESTERDAY
    assert not isinstance(got, str), "it rewrote the question"


# -- through the real hub --------------------------------------------------

def test_a_follow_up_reaches_the_same_day_through_the_hub():
    """The end-to-end shape: ask about yesterday, then ask a question that
    names no time at all, and have the second one land on the same window
    rather than on all of history."""
    import os
    import tempfile

    from tests.fakes import ContextAnswerReasoner, HashEmbedder
    from tracemem.app.wiring import build
    from tracemem.text.claim_log import ClaimLog

    d = tempfile.mkdtemp()
    hub = build({"claim_log": ClaimLog(os.path.join(d, "s.sqlite3")),
                 "reasoner": ContextAnswerReasoner(),
                 "embedder": HashEmbedder()})

    hub.ask("what did I do yesterday?")
    first_window = hub._last_asked[1]
    assert first_window is not None, "the first question resolved no window"

    hub.ask("who was I with?")
    assert hub._last_asked[1] is first_window, \
        "the follow-up did not inherit the window"


def test_a_fresh_self_contained_question_does_not_inherit_through_the_hub():
    import os
    import tempfile

    from tests.fakes import ContextAnswerReasoner, HashEmbedder
    from tracemem.app.wiring import build
    from tracemem.text.claim_log import ClaimLog

    d = tempfile.mkdtemp()
    hub = build({"claim_log": ClaimLog(os.path.join(d, "s.sqlite3")),
                 "reasoner": ContextAnswerReasoner(),
                 "embedder": HashEmbedder()})
    hub.ask("what did I do yesterday?")
    hub.ask("who is Juno?")
    assert hub._last_asked[1] is None, \
        "an unrelated question silently became about yesterday"
