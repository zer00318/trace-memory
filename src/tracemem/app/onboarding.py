"""OWNS: what TRACE promises on day one, and the proof it is not lying.

`Onboarding / Honest expectations` — the last onboarding screen. Its oracle
is unusual and it is the whole point: THE COPY MUST MATCH WHAT A DAY-ONE ASK
ACTUALLY RETURNS. Not "sounds about right"; matches, checked by a test
against a cold store.

WHY THAT ORACLE. The first ten minutes decide whether anyone keeps this
running, and the first ask on day one is the moment the product is least able
to impress. Every instinct is to write around it — "your memory is building",
"come back soon" — and that is the beginning of a memory that oversells,
which is the one failure this product cannot survive. If it will exaggerate
about itself on day one it will exaggerate about your life on day thirty.

So the copy states three things, and each is checkable:

  it knows nothing yet        — a cold ask returns "not in memory"
  it will not guess           — it abstains rather than inventing
  it thinks tonight           — the night runs while the machine is idle

MEASURED ON A COLD STORE 2026-08-25, which nobody had ever actually run:

  who is Juno?            -> not in memory
  which apps do I use most?  -> not in memory
  what did I do yesterday?   -> I have no perception at all for yesterday.
                                No gap witness explains it, which means the
                                eye was not running.

That last one is the sentence this screen has to live up to, and it is a good
one: it distinguishes "nothing happened" from "I was not watching", on day
one, unprompted.
"""
from __future__ import annotations

# The exact strings a day-one ask returns. The screen quotes these rather
# than paraphrasing them, so the promise and the behaviour cannot drift
# apart without a test failing.
DAY_ONE_ABSTENTION = "not in memory"
DAY_ONE_NO_PERCEPTION = "I have no perception at all for"

HONEST_EXPECTATIONS = """\
WHAT TO EXPECT

Right now TRACE knows nothing about you. Not a little — nothing.

Ask it something today and it will tell you so: "not in memory", or
"I have no perception at all for yesterday". It will not guess, and it
will not fill the silence with something plausible. That refusal is the
product. If it ever answers something it cannot show you evidence for,
it is broken, and you should stop trusting it.

Tonight, while you are not using the machine, it thinks about what it
saw today. Tomorrow it starts knowing you.

Three things worth knowing before you start:

  · It watches your screen, and the pictures never leave this Mac.
    Only text does, and only into a store on this disk that is
    encrypted with a key that is yours.

  · Every answer cites what it saw. Follow a citation with
    `./trace ask --moment <id>` and you will see the actual moment.

  · When it is wrong, tell it. Press "That's wrong" and say what really
    happened. It records your words as evidence and re-derives from
    them; it never edits away what it saw.

You can make it forget all of this at any time, and it will prove it did:
  ./trace wipe
"""


def render() -> str:
    return HONEST_EXPECTATIONS
