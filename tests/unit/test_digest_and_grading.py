"""ORACLE for P3 — the digest, and the founder's voice as evidence.

The founder's own words when asked how the memory tier could be measured:
"show what the brain did and what it thinks cumulatively, like a summary.
Since I'm the only user, I can grade it."

The law under test (F-GRADE): a grade changes what is TRUE in the graph, never
how the graph DECIDES.
"""
from __future__ import annotations

import pytest

from tracemem.app.digest import (
    UNSURE_BELOW, Digest, DigestLine, build_digest, render,
)
from tracemem.app.founder import UnknownVerdict, grade_claim
from tracemem.app.witness import WitnessKind, witness
from tracemem.domain import (
    Attribute, Confidence, Grade, Node, NodeKind, ProvenanceClass,
)
from tracemem.nodes.graph_store import MemoryGraphStore
from tracemem.text.claim_log import ClaimLog
from tests.helpers import make_claim

T0, T1 = 1_000_000, 1_000_000 + 86_400_000


def _meta(nid, text, conf=0.8, t_ms=T0 + 1000, eps=("ep-1",)):
    return Node(id=nid, kind=NodeKind.PATTERN, derivation_depth=1,
                parent_ids=("p",),
                attributes=[Attribute(key="claim", value=text,
                                      grade=Grade.DERIVED,
                                      confidence=Confidence(conf),
                                      source_episode_ids=eps, t_ms=t_ms)])


def _log(tmp_path, claims=()):
    log = ClaimLog(str(tmp_path / "s.sqlite3"))
    for c in claims:
        log.append(c)
    return log


# -- the line id must survive regeneration --------------------------------

def test_a_line_id_is_stable_across_regeneration():
    """The founder grades a line in the morning; the digest is rebuilt. The
    grade must still name the same statement, so the id is content-derived,
    never a row number."""
    a = DigestLine(section="believe", text="you usually read in the evening")
    b = DigestLine(section="believe", text="you usually read in the evening")
    assert a.id == b.id
    c = DigestLine(section="believe", text="something else entirely")
    assert c.id != a.id


def test_a_line_from_a_different_section_is_a_different_line():
    assert (DigestLine(section="believe", text="x").id
            != DigestLine(section="unsure", text="x").id)


def test_an_unknown_section_is_a_construction_error():
    with pytest.raises(ValueError):
        DigestLine(section="vibes", text="x")


# -- what the digest reports ----------------------------------------------

def test_beliefs_appear_with_their_receipts(tmp_path):
    g = MemoryGraphStore()
    g.upsert_node(_meta("m1", "you usually read chess.com in the evening"))
    d = build_digest(g, _log(tmp_path), T0, T1)
    believed = d.section("believe")
    assert [l.text for l in believed] == [
        "you usually read chess.com in the evening"]
    assert believed[0].citations == ("ep-1",)


def test_a_belief_with_no_receipt_is_never_shown(tmp_path):
    """A digest line with no citation is exactly the confabulation this
    product exists to refuse."""
    g = MemoryGraphStore()
    n = _meta("m1", "a belief from nowhere")
    n.attributes[0].source_episode_ids = ()
    g.nodes["m1"] = n                       # bypass Attribute's own guard
    assert build_digest(g, _log(tmp_path), T0, T1).section("believe") == []


def test_a_low_confidence_belief_is_reported_as_unsure_not_as_believed(tmp_path):
    g = MemoryGraphStore()
    g.upsert_node(_meta("m1", "maybe Sam owns the deploy",
                        conf=UNSURE_BELOW - 0.1))
    d = build_digest(g, _log(tmp_path), T0, T1)
    assert d.section("believe") == []
    assert [l.text for l in d.section("unsure")] == ["maybe Sam owns the deploy"]


def test_the_entity_tier_is_not_a_belief(tmp_path):
    """depth-0 is an observation with a name, not something derived."""
    g = MemoryGraphStore()
    g.upsert_node(Node(id="e1", kind=NodeKind.SITE,
                       attributes=[Attribute(key="site", value="chess.com",
                                             grade=Grade.VERBATIM,
                                             confidence=Confidence(1.0),
                                             source_episode_ids=("ep-1",),
                                             t_ms=T0 + 1)]))
    assert build_digest(g, _log(tmp_path), T0, T1).section("believe") == []


def test_retired_beliefs_become_what_changed_my_mind(tmp_path):
    """The moat: retiring tombstones a belief rather than vaporizing it,
    precisely so this section can exist."""
    g = MemoryGraphStore()
    g.upsert_node(_meta("m1", "Sam runs the deploy"))
    g.retire_meta("m1", T0 + 5000)
    d = build_digest(g, _log(tmp_path), T0, T1)
    assert [l.text for l in d.section("changed")] == [
        "I no longer think: Sam runs the deploy"]


def test_beliefs_outside_the_window_are_not_in_todays_digest(tmp_path):
    g = MemoryGraphStore()
    g.upsert_node(_meta("old", "last week's idea", t_ms=T0 - 999_999))
    assert build_digest(g, _log(tmp_path), T0, T1).section("believe") == []


def test_coverage_comes_from_witnesses_and_holes_are_stated(tmp_path):
    """Never from claim density — that would call a quiet afternoon a blind
    spot and a broken loop excellent coverage."""
    blind = witness(WitnessKind.CLOSED_EYES, t_ms=T0 + 1000,
                    span_t2_ms=T0 + 1000 + 3_600_000, detail="lid shut")
    d = build_digest(MemoryGraphStore(), _log(tmp_path, [blind]), T0, T1)
    assert d.coverage_pct is not None and d.coverage_pct < 100.0
    assert d.gaps
    assert any("watching" in l.text for l in d.section("saw"))


def test_an_empty_section_says_so_rather_than_vanishing(tmp_path):
    text = render(build_digest(MemoryGraphStore(), _log(tmp_path), T0, T1))
    assert "WHAT I NOW BELIEVE" in text
    assert "(nothing)" in text


def test_render_shows_the_grading_instruction(tmp_path):
    assert "grade" in render(build_digest(
        MemoryGraphStore(), _log(tmp_path), T0, T1)).lower()


# -- F-GRADE: the founder's voice is evidence ------------------------------

def test_a_grade_becomes_a_claim_at_the_top_of_the_scale():
    line = DigestLine(section="believe", text="Sam runs the deploy")
    c = grade_claim(line, "wrong", "Priya runs the deploy, not Sam.", t_ms=5)
    assert c.grade == Grade.VERBATIM
    assert c.provenance.perceiver == "founder"
    assert c.provenance.klass is ProvenanceClass.OWNED
    assert "Priya runs the deploy" in c.text
    assert c.structured["founder_grade"] == "wrong"
    assert c.structured["about_line"] == line.id


def test_the_grade_is_re_readable_against_the_line_it_judged():
    line = DigestLine(section="believe", text="x")
    c = grade_claim(line, "right", t_ms=5)
    assert c.coord.capture.source == "founder:digest:%s" % line.id
    assert c.coord.capture.frame_id is None        # not a screen observation


def test_a_founder_claim_is_store_legal():
    """The store's birth-stamp gate rejects None loudly; a grade must pass it
    without special-casing."""
    import tempfile
    from pathlib import Path
    line = DigestLine(section="believe", text="x")
    with tempfile.TemporaryDirectory() as d:
        log = ClaimLog(str(Path(d) / "s.sqlite3"))
        log.append(grade_claim(line, "right", t_ms=5))
        assert log.count() == 1


def test_an_unknown_verdict_is_refused_rather_than_guessed():
    line = DigestLine(section="believe", text="x")
    with pytest.raises(UnknownVerdict):
        grade_claim(line, "sort of", t_ms=5)


def test_grading_never_touches_the_graph_or_a_threshold():
    """THE LAW: a grade changes what is TRUE, never how the graph DECIDES.
    If this module ever imports a store or a tuning constant, the brain has
    started optimising for the founder's approval."""
    import ast
    import inspect
    from tracemem.app import founder

    # The AST, not a grep: this module's docstring EXPLAINS which knobs it
    # refuses to touch, and a text search cannot tell an explanation from a
    # use. The invariant is about code, so it is checked against code.
    tree = ast.parse(inspect.getsource(founder))
    used = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            used.add(n.id)
        elif isinstance(n, ast.Attribute):
            used.add(n.attr)
        elif isinstance(n, ast.alias):
            used.add(n.name.split(".")[-1])
        elif isinstance(n, ast.ImportFrom) and n.module:
            used.update(n.module.split("."))

    for forbidden in ("graph_store", "upsert_node", "RESOLVE_BAR",
                      "cold_floor", "KIND_PRIOR", "retire_meta",
                      "delete_meta", "nodes", "marination"):
        assert forbidden not in used, (
            "app/founder.py references %r — a grade must change what is TRUE, "
            "never how the graph DECIDES" % forbidden)
