"""The macro eye's understanding, welded into claims the store will take.

Two things are load-bearing here and neither is about text handling.

THE GRADE. `domain/grading` maps "vlm" → Grade.GIST. A scene description is
a reading of pixels, not a transcription of them: "the user is playing chess"
is an interpretation, and it lands in the same store as OCR verbatim. If it
could outrank that verbatim, a confident wrong inference would overrule text
the machine actually read — the exact failure the grade scale exists to
prevent. So these tests pin the grade, and pin that this module never sets
one itself.

THE STORE'S GATE. Every claim needs five birth stamps or `ClaimLog.append`
rejects it loudly. Constructing a Claim proves nothing about whether it can
be written — so one test writes them to a real log rather than trusting the
constructor.
"""
from __future__ import annotations

from tracemem.domain import Grade
from tracemem.extraction.macro_claims import MACRO_CONFIDENCE, macro_claims

SEEN = [{"kind": "object", "text": "a chess board mid-game"},
        {"kind": "activity", "text": "playing a game of chess"},
        {"kind": "inference", "text": "the user is losing"}]


def _claims(seen=None, **kw):
    kw.setdefault("t_ms", 1_700_000_000_000)
    kw.setdefault("frame_id", "f-abc")
    kw.setdefault("device_id", "mac-1")
    return macro_claims(SEEN if seen is None else seen, **kw)


def test_every_macro_claim_is_gist():
    """Never verbatim. A VLM did not READ anything."""
    for c in _claims():
        assert c.grade is Grade.GIST
        assert c.provenance.perceiver == "vlm"


def test_the_grade_comes_from_the_table_not_from_here():
    """If someone adds a grade literal to macro_claims.py, this fails.

    There is one grade table (domain/grading.py) and modules that mint claims
    must not grow private opinions about their own trustworthiness.
    """
    import ast
    import inspect

    from tracemem.extraction import macro_claims as m

    # Checked against the parsed CODE, not the file text — the docstring
    # names Grade.GIST on purpose, to explain the rule it is documenting.
    # A test that cannot tell prose from code would forbid the explanation.
    tree = ast.parse(inspect.getsource(m))
    imported = {alias.asname or alias.name
                for node in ast.walk(tree)
                if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in node.names}
    assert "Grade" not in imported, \
        "macro_claims imports Grade — it is one edit from grading itself"
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "Grade" not in names and "GIST" not in attrs


def test_a_glance_is_less_confident_than_a_reading():
    """0.6 against the OCR eye's 0.9 default. This sense is guessing in a way
    reading is not, and the number says so before anyone has to reason about
    grades."""
    assert MACRO_CONFIDENCE < 0.9
    for c in _claims():
        assert c.confidence.value == MACRO_CONFIDENCE


def test_understanding_lives_nowhere_in_particular():
    """No bbox. "The user appears to be reading documentation" is not located
    on the frame, and inventing a full-frame box would let a whole-scene
    inference match a span query as though it had been localised."""
    for c in _claims():
        assert c.coord.span is not None, "the layer is structurally mandatory"
        assert c.coord.span.bbox is None


def test_the_kind_stays_machine_readable():
    """Not mashed into the text as "object: a chess board". The four kinds are
    the vocabulary the ceiling is scored against, and context_gap compares
    like with like."""
    kinds = [c.structured["kind"] for c in _claims()]
    assert kinds == ["object", "activity", "inference"]
    for c in _claims():
        assert not c.text.startswith(c.structured["kind"] + ":")


def test_junk_costs_only_itself():
    """A local model's JSON is not always clean. One bad entry must not take
    down a pass and every good claim in the same batch with it."""
    out = _claims([{"kind": "object", "text": "a chess board"},
                   {"kind": "wishful", "text": "the user is happy"},
                   {"kind": "object", "text": "   "},
                   {"kind": "object"},
                   "not even a dict",
                   None,
                   {"kind": "activity", "text": "playing chess"}])
    assert [c.text for c in out] == ["a chess board", "playing chess"]


def test_an_empty_bid_id_is_never_written():
    """The store's gate only rejects None, so "" would sail through — and
    `predicted_vs_actual_by_bidder` aggregates over these, so empty ids merge
    every macro pass this daemon ever ran into one uncostable blob."""
    for c in _claims():
        assert c.bid_id == "macro:f-abc"
    for c in _claims(bid_id="a-real-bid"):
        assert c.bid_id == "a-real-bid"


def test_they_survive_the_real_store(tmp_path):
    """Constructing a Claim proves nothing about writing one.

    `ClaimLog.append` rejects loudly if any of the five birth stamps is
    missing, and never defaults them. This is the test that would have caught
    a macro pass that ran nightly and threw every claim away.
    """
    from tracemem.text.claim_log import ClaimLog

    log = ClaimLog(str(tmp_path / "s.sqlite3"))
    try:
        for c in _claims():
            log.append(c)                     # raises if a stamp is missing
        assert len(list(log.all_claims())) == 3
    finally:
        try:
            log.close()
        except AttributeError:
            pass


# -- the macro eye must never name a person -----------------------------

def _mentions(text, kind="activity", perceiver=None):
    from tracemem.nodes.birth.entity_extractor import extract_mentions

    class Ep:
        id = "ep1"
        place_key = "home"

    cs = macro_claims([{"kind": kind, "text": text}],
                      t_ms=1000, frame_id="f", device_id="mac-1")
    return [m.surface for m in extract_mentions(Ep(), cs) if m.kind == "person"]


def test_the_macro_eye_never_mints_a_person():
    """MEASURED on gold/frames/auto_0025.png. The screen says
    `FM Sample Playername`. The macro eye reported `GM Example Player` — wrong
    on two words of three.

    On that exact phrasing nothing was born, because the person grammars want
    "with X" or "from X". That is LUCK, not a rule, and these are the
    phrasings where the luck runs out. A phantom person with a mangled name
    is what the founder then meets in a dossier, and it cannot be corrected
    by anyone because nobody ever said it.
    """
    for phrasing in ["a video call with Alex Example",
                     "the user is chatting with Sara Example",
                     "an email from Nina Example",
                     "GM Example Player (chess player)"]:
        assert _mentions(phrasing) == [], \
            "the macro eye named a person from %r" % phrasing


def test_no_kind_of_macro_claim_can_name_a_person():
    """The `participant` kind was removed at the source, but any of the three
    remaining kinds can mention a name in passing — "editing a document with
    Alex Example" is an activity. The birth gate is the second line of
    defence and must cover all of them."""
    for kind in ("object", "activity", "inference"):
        assert _mentions("a video call with Alex Example", kind=kind) == []


def test_a_reading_sense_still_names_people():
    """THE REGRESSION THIS MUST NOT CAUSE.

    The founder's 26 real people came from senses that were reading. Barring
    the VLM must not bar them — the rule is about senses that never claimed
    to be transcribing, not about every sense that can be wrong.
    """
    from tracemem.domain import (CaptureCoord, CoordinateStack, SpanCoord,
                                 WorldCoord)
    from tracemem.extraction.claim_builder import build_claim
    from tracemem.nodes.birth.entity_extractor import extract_mentions

    class Ep:
        id = "ep1"
        place_key = "home"

    coord = CoordinateStack(world=WorldCoord(place_key="home"),
                            capture=CaptureCoord(frame_id="f",
                                                 device_id="mac-1"),
                            span=SpanCoord(), t_ms=1000)
    c = build_claim({"text": "a video call with Juno Example"}, coord, "ocr",
                    bidder="b", bid_id="i", bid_predicted_value=0.0,
                    actual_cost=0.0, scope="frame")
    assert [m.surface for m in extract_mentions(Ep(), [c])
            if m.kind == "person"] == ["Juno"]
