"""How much to trust each receipt, said in words.

`Ask/Grades on display`. Every claim has carried a grade since July and no
face ever showed one, so an answer resting on a VLM's GLANCE looked exactly
like one resting on text the machine actually READ. The scale existed
entirely for the machine's own arithmetic.

AND NOT AS THE NAME OF AN ENUM. "VERBATIM" means something precise to this
codebase and nothing to a person; showing it beside a citation tells them the
name of a constant, not whether to believe the sentence.
"""
from __future__ import annotations

from tracemem.domain.grade import Grade, in_words


def test_every_grade_has_a_human_word():
    for g in Grade:
        word = in_words(g)
        assert word and word.upper() != g.name


def test_read_and_glanced_at_are_distinguishable():
    """The whole point. OCR recovered characters; a VLM described a scene.
    A person deciding whether to act on an answer needs to know which."""
    assert in_words(Grade.VERBATIM) == "read"
    assert in_words(Grade.GIST) == "glanced at"
    assert in_words(Grade.DERIVED) == "worked out"


def test_an_unknown_grade_reads_as_unsure_not_as_a_crash():
    """A face must never lose an answer because a grade was odd — and it must
    not round an unknown one UP into confidence."""
    assert in_words(None) == "unsure"
    assert in_words("banana") == "unsure"
    assert in_words(99) == "unsure"


def test_only_one_module_defines_the_wording():
    """One table. Every face inventing its own wording is how a product ends
    up disagreeing with itself about what a grade means — which has already
    happened once in this codebase, with NodeKind.

    Checked against CODE, not file text: a comment quoting the wording to
    explain what a field contains is documentation, and a test that cannot
    tell prose from code forbids the explanation.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    hits = []
    for f in (list((root / "src").rglob("*.py"))
              + list((root / "tools").rglob("*.py"))):
        if f.name == "grade.py":
            continue
        try:
            tree = ast.parse(f.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == "glanced at":
                hits.append(str(f.relative_to(root)))
    assert hits == [], "a second wording table appeared in %r" % hits
