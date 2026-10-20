"""ORACLE for Grading rules per source (03_text).

PROOF: no grade literals outside the table; a unit test covers the table.
"""
import re
from pathlib import Path

import pytest

from tracemem.domain.grade import Grade
from tracemem.domain.grading import GRADE_BY_SOURCE, grade_for, known_sources

SRC = Path(__file__).resolve().parents[2] / "src" / "tracemem"


# -- the table itself ------------------------------------------------------
def test_the_brief_mapping_holds():
    """OCR → verbatim; reasoning → derived; the founder's answers → the top."""
    assert grade_for("ocr") == Grade.VERBATIM
    assert grade_for("asr") == Grade.VERBATIM
    assert grade_for("founder") == Grade.VERBATIM
    assert grade_for("reasoner") == Grade.DERIVED
    assert grade_for("gist") == Grade.GIST
    assert grade_for("specialist") == Grade.SPECIALIST


def test_a_qualifier_does_not_change_the_family():
    assert grade_for("specialist:chess") == grade_for("specialist")
    assert grade_for("OCR:vision") == Grade.VERBATIM


def test_an_unknown_source_is_a_construction_error_not_a_default():
    """THE laundering trap: a default would turn a guess into testimony,
    and nothing downstream could ever tell the difference."""
    with pytest.raises(ValueError) as e:
        grade_for("mystery_helper")
    assert "never defaulted" in str(e.value)


def test_the_error_tells_you_how_to_fix_it():
    with pytest.raises(ValueError) as e:
        grade_for("nope")
    assert "GRADE_BY_SOURCE" in str(e.value)


def test_every_entry_is_a_real_grade():
    assert all(isinstance(g, Grade) for g in GRADE_BY_SOURCE.values())


def test_reasoning_never_outranks_transcription():
    """A model's opinion about stored claims must sit below the claims."""
    assert grade_for("reasoner") < grade_for("ocr")
    assert grade_for("marination") < grade_for("specialist")


def test_the_table_is_enumerable_for_the_error_message():
    assert "ocr" in known_sources() and "founder" in known_sources()


# -- the claim builder obeys it -------------------------------------------
def test_the_builder_reads_the_table_not_a_literal():
    from tracemem.domain import CaptureCoord, CoordinateStack, SpanCoord, WorldCoord
    from tracemem.extraction.claim_builder import build_claim
    stack = CoordinateStack(
        world=WorldCoord(place_key="p"),
        capture=CaptureCoord(frame_id="f", device_id="d"),
        span=SpanCoord(bbox=(0.0, 0.0, 1.0, 1.0)), t_ms=1)
    assert build_claim({"text": "x"}, stack, "ocr").grade == Grade.VERBATIM
    assert build_claim({"text": "x"}, stack, "gist").grade == Grade.GIST


def test_the_builder_still_refuses_an_unknown_perceiver():
    from tracemem.domain import CaptureCoord, CoordinateStack, SpanCoord, WorldCoord
    from tracemem.extraction.claim_builder import build_claim
    stack = CoordinateStack(
        world=WorldCoord(place_key="p"),
        capture=CaptureCoord(frame_id="f", device_id="d"),
        span=SpanCoord(bbox=(0.0, 0.0, 1.0, 1.0)), t_ms=1)
    with pytest.raises(ValueError):
        build_claim({"text": "x"}, stack, "unknown_thing")


def test_the_old_import_site_still_resolves_to_the_one_table():
    """Re-exported for compatibility — but it must BE the table, not a copy
    that can drift out of sync with it."""
    from tracemem.extraction.claim_builder import GRADE_BY_PERCEIVER
    assert GRADE_BY_PERCEIVER is GRADE_BY_SOURCE


# -- the grep half of the PROOF -------------------------------------------
# Modules that legitimately compare or step grades (the degrade law, the
# supplier's scoring weights, the pruner's decay) rather than ASSIGNING one
# to a new claim. Named explicitly so the exemption is reviewable.
GRADE_LOGIC = {
    "domain/grade.py", "domain/grading.py",
    "supplier/scoring.py",              # retrieval weights per grade
    "supplier/packer.py",               # picks max grade among attributes
    "nodes/combination/guardrails.py",  # enforces G3
    "nodes/combination/judge.py",
    "nodes/combination/kinds/contradiction.py",
    "nodes/retention/pruner.py",
    "nodes/marination/background.py",
    "nodes/marination/schemas.py",
    "evalx/fixtures.py",                # synthetic fixtures, never the store
}

ASSIGNS_GRADE = re.compile(r"grade\s*=\s*Grade\.[A-Z]+")


def test_no_builder_assigns_a_grade_literal():
    """PROOF: grep — no grade literals outside the table. Targets ASSIGNMENT
    (`grade=Grade.X`) rather than every mention, because comparing grades is
    exactly what the grade scale is for."""
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel in GRADE_LOGIC:
            continue
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if ASSIGNS_GRADE.search(line):
                offenders.append(f"{rel}:{i}: {line.strip()}")
    assert not offenders, (
        "grade assigned from a literal instead of domain/grading.py:\n  "
        + "\n  ".join(offenders))


def test_the_grep_trap_bites():
    assert ASSIGNS_GRADE.search("        grade=Grade.VERBATIM,")
    assert not ASSIGNS_GRADE.search("if a.grade == Grade.VERBATIM:")
