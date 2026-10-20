"""A correction must not hand the model somebody else's receipts.

`founder.grade_claim` writes "<the truth> (about: <the line it corrects>)".
When the line corrected is an ANSWER, that quote contains the answer's [id]
markers — and feeding the correction into a later context shows the model
plausible-looking ids belonging to a DIFFERENT question's evidence.

WITNESSED 2026-08-28 on the real store. Asked "what did I do yesterday?"
about a day with no capture at all — the timeline said, correctly, "I have no
perception at all for yesterday" — the model produced a confident, detailed,
entirely fabricated day:

    Yesterday involved a significant focus on artifact design ... using Claude
    on localhost:4242 for over an hour [9081b271]. Prior to that, you spent
    time reviewing oxidized copper samples in Google Drive [7244b93a],
    exploring travel options to Lisbon and Chile [28378978] ...

Every one of those ids was copied out of a previous answer quoted inside a
correction. The guard caught it — none of them is in THIS context, so the
answer counted as uncited and was refused, and the person saw the evidence
instead of the invention.

It worked. But a context that hands a model real-looking citations for
evidence it was not given is a confabulation kit, and the guard should not be
the only thing standing between it and a person.
"""
from __future__ import annotations

from tracemem.supplier.corrections import _without_stale_ids, render


class Claim:
    def __init__(self, text, cid="abc12345deadbeef"):
        self.id = cid
        self.text = text


def test_ids_quoted_inside_a_correction_are_stripped():
    said = ("It was Friday. (about: Yesterday you worked with Claude "
            "[9081b271] and read about copper [7244b93a].)")
    out = _without_stale_ids(said)
    assert "9081b271" not in out
    assert "7244b93a" not in out
    assert "It was Friday." in out
    assert "worked with Claude" in out, "it removed the words, not just the ids"


def test_the_corrections_own_id_survives_and_is_shown():
    """Nothing citable is lost — the caller prints the correction's own id,
    which is the one that actually points at this evidence."""
    out = render([Claim("It was Friday. (about: something [9081b271])")])
    assert "[abc12345]" in out
    assert "9081b271" not in out


def test_a_correction_with_no_quoted_ids_is_untouched():
    out = _without_stale_ids("Juno is my sister.")
    assert out == "Juno is my sister."


def test_short_bracketed_text_is_not_mistaken_for_an_id():
    """Real prose contains brackets. Only a long hex run is a citation."""
    assert _without_stale_ids("I said [yes] to it") == "I said [yes] to it"
    assert _without_stale_ids("see [1] below") == "see [1] below"


def test_the_guard_is_still_the_backstop_not_the_only_wall():
    """This narrows what the model can copy. It does not replace the check
    that every cited id is actually in the context — that check is what
    caught this in the first place."""
    from tracemem.llm.answer import parse_answer

    class Block:
        text = "real evidence"
        source_ids = ("realid01",)

    class Ctx:
        blocks = (Block(),)

    a = parse_answer("A claim [9081b271] and another [realid01].", Ctx())
    assert a.citations == ("realid01",)
    assert a.invented == ("9081b271",)
    assert "[UNSUPPORTED]" in a.text
