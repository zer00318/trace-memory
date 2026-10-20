"""OWNS: the second question in a row.

`Follow-ups`. "What did I do yesterday?" then "who was I with?" — the second
question is unanswerable on its own. It names no time, no subject and no
place; everything it needs was said out loud one sentence ago, which is how
people actually talk and precisely what a memory that only accepts fully
specified questions cannot do.

Without this, "who was I with?" parses as a question about all of history,
retrieves whatever the embedder likes, and answers about the wrong day — or
abstains, which at least is honest but still ends the conversation.

WHAT CARRIES OVER IS THE WINDOW, NOT THE WORDS. The previous question's
resolved TIME is reused; its subject is not. "Who is Juno?" followed by
"where does she work?" must not silently become "where does Juno work" —
the pronoun might mean someone else entirely, and putting a name into a
question the founder did not name is how a memory starts answering about the
wrong person with total confidence. Time is safe to carry because a time is
not a claim about who or what.

IT CARRIES ONLY INTO A QUESTION THAT ASKS FOR IT. A follow-up is a question
with NO time of its own. One that names its own time is complete and is left
exactly as asked — "what did I do yesterday" followed by "what about
Tuesday" is two questions, not one.

AND IT EXPIRES. A question asked an hour after the last one is a new
conversation, whatever it looks like. Carrying a window across a lunch break
would answer about the morning with no sign that it had.
"""
from __future__ import annotations

import re
from typing import Optional

# How long a conversation stays open. Long enough to think and type; short
# enough that coming back after a meeting starts fresh.
CONVERSATION_MS = 10 * 60 * 1000

# Questions that lean on what was just said. Not an attempt at real anaphora
# resolution — just the shapes that cannot stand alone: a bare pronoun, an
# explicit continuation, or an elliptical fragment.
_LEANS = re.compile(
    r"\b(and then|then what|what about|how about|who else|what else|"
    r"anything else|and (?:who|what|where|why|when)|"
    r"(?:who|what|where|why|when|how)\s+(?:was|were|did|do|does|is|are)\s+"
    r"(?:i|we|it|he|she|they|that|this|them)\b)", re.I)


def leans_on_the_last_question(text: str) -> bool:
    return bool(_LEANS.search(str(text or "")))


def carry_window(question: str, cue, previous, now_ms: int,
                 window_ms: int = CONVERSATION_MS):
    """The window this follow-up inherits, or None.

    `cue` is this question's own parsed cue; `previous` is (question_text,
    window, asked_t_ms) from the last ask, or None.
    """
    if cue is not None and getattr(cue, "window", None) is not None:
        return None                    # it named its own time; leave it alone
    if not previous:
        return None
    _, window, asked_t_ms = previous
    if window is None:
        return None
    if now_ms - int(asked_t_ms or 0) > window_ms:
        # A NEW CONVERSATION. Carrying a window across a lunch break would
        # answer about the morning with nothing saying it had.
        return None
    if not leans_on_the_last_question(question):
        return None
    return window
