"""The LIVED / OWNED / BELIEF provenance axis + the two-world coordinate.

Atom reshape 2026-07-24 (ONTRACK_ANALYSIS §recommendation 1): make an OWNED
fact representable and keep a plan from posing as an observation. These are
the acceptance oracles for that change.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from tracemem.domain import (
    CaptureCoord, Claim, Confidence, CoordinateStack, Grade, Modality,
    Provenance, ProvenanceClass, SpanCoord, WorldCoord,
)
from tracemem.text.claim_log import ClaimLog
from tracemem.text.migrations import migrate_claim_table


def _owned_claim(text="meeting with Tessa on Tuesday",
                 source="/Users/x/Documents/notes.md", t_ms=1000):
    """An OWNED fact from a file: no frame, no pixel bbox, no gps — a source."""
    coord = CoordinateStack(
        world=WorldCoord(place_key=None),
        capture=CaptureCoord.owned(source=source, device_id="mac-1"),
        span=SpanCoord(bbox=None),
        t_ms=t_ms,
    )
    return Claim(
        text=text, coord=coord,
        provenance=Provenance(perceiver="file-indexer", modality=Modality.SENSOR,
                              klass=ProvenanceClass.OWNED),
        grade=Grade.SPECIALIST, confidence=Confidence(0.9),
        bidder="test", bid_id="test:%d" % t_ms,
        bid_predicted_value=0.0, actual_cost=0.0, scope="memory",
    )


def _lived_claim(text="hello", frame="f-1", t_ms=1000):
    coord = CoordinateStack(
        world=WorldCoord(place_key="wifi:aaaa"),
        capture=CaptureCoord.lived(frame_id=frame, device_id="mac-1", app="Safari"),
        span=SpanCoord(bbox=(0.1, 0.1, 0.5, 0.2)),
        t_ms=t_ms,
    )
    return Claim(
        text=text, coord=coord,
        provenance=Provenance(perceiver="ocr", modality=Modality.SCREEN),
        grade=Grade.VERBATIM, confidence=Confidence(0.9),
        bidder="test", bid_id="test:%d" % t_ms,
        bid_predicted_value=0.0, actual_cost=0.0, scope="frame",
    )


# -- the two-world coordinate ------------------------------------------------

def test_owned_fact_is_representable():
    """The whole point: a file fact — no frame, no bbox — is a legal Claim."""
    c = _owned_claim()
    assert c.provenance.klass is ProvenanceClass.OWNED
    assert c.coord.capture.source == "/Users/x/Documents/notes.md"
    assert c.coord.capture.frame_id is None
    assert c.coord.span.bbox is None


def test_lived_defaults_to_lived_class():
    c = _lived_claim()
    assert c.provenance.klass is ProvenanceClass.LIVED    # honest default
    assert c.coord.capture.frame_id == "f-1"
    assert c.coord.capture.source is None


def test_capture_from_nowhere_is_rejected():
    with pytest.raises(ValueError, match="exactly one of frame_id"):
        CaptureCoord(device_id="mac-1")                    # neither frame nor source


def test_capture_cannot_be_both_worlds():
    with pytest.raises(ValueError, match="exactly one of frame_id"):
        CaptureCoord(device_id="mac-1", frame_id="f", source="/p")


# -- tier + world honesty (a plan never poses as an observation) -------------

def test_raw_claim_cannot_be_belief():
    coord = CoordinateStack(
        world=WorldCoord(), capture=CaptureCoord.lived("f", "mac-1"),
        span=SpanCoord(), t_ms=1)
    with pytest.raises(ValueError, match="cannot be BELIEF"):
        Claim(text="x", coord=coord,
              provenance=Provenance("p", Modality.SCREEN,
                                    klass=ProvenanceClass.BELIEF),
              grade=Grade.GIST, confidence=Confidence(0.5),
              bidder="t", bid_id="t:1", bid_predicted_value=0.0,
              actual_cost=0.0, scope="frame")


def test_owned_class_requires_a_source():
    """OWNED provenance over a LIVED coordinate (a frame) is a contradiction."""
    coord = CoordinateStack(
        world=WorldCoord(), capture=CaptureCoord.lived("f", "mac-1"),
        span=SpanCoord(), t_ms=1)
    with pytest.raises(ValueError, match="OWNED claim must carry"):
        Claim(text="x", coord=coord,
              provenance=Provenance("p", Modality.SENSOR,
                                    klass=ProvenanceClass.OWNED),
              grade=Grade.GIST, confidence=Confidence(0.5),
              bidder="t", bid_id="t:1", bid_predicted_value=0.0,
              actual_cost=0.0, scope="memory")


def test_lived_class_requires_a_frame():
    """LIVED provenance over an OWNED coordinate (a source) is a contradiction."""
    coord = CoordinateStack(
        world=WorldCoord(), capture=CaptureCoord.owned("/p", "mac-1"),
        span=SpanCoord(), t_ms=1)
    with pytest.raises(ValueError, match="LIVED claim must carry"):
        Claim(text="x", coord=coord,
              provenance=Provenance("p", Modality.SCREEN,
                                    klass=ProvenanceClass.LIVED),
              grade=Grade.GIST, confidence=Confidence(0.5),
              bidder="t", bid_id="t:1", bid_predicted_value=0.0,
              actual_cost=0.0, scope="frame")


# -- persistence: klass + source survive the immutable log -------------------

def test_owned_claim_round_trips_through_the_log():
    with tempfile.TemporaryDirectory() as d:
        log = ClaimLog(os.path.join(d, "m.db"))
        c = _owned_claim()
        log.append(c)
        back = log.get(c.id)
        assert back is not None
        assert back.provenance.klass is ProvenanceClass.OWNED
        assert back.coord.capture.source == "/Users/x/Documents/notes.md"
        assert back.coord.capture.frame_id is None


def test_klass_is_a_queryable_column():
    """The class is plaintext-indexed like modality/grade, so 'what did I OWN
    vs LIVE' is answerable without decrypting every row."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "m.db")
        log = ClaimLog(path)
        log.append(_lived_claim(text="a", frame="f-1", t_ms=1))
        log.append(_owned_claim(text="b", t_ms=2))
        con = sqlite3.connect(path)
        rows = dict(con.execute(
            "SELECT klass, COUNT(*) FROM claim GROUP BY klass").fetchall())
        con.close()
        assert rows == {"lived": 1, "owned": 1}


def test_migration_adds_klass_to_a_preexisting_store():
    """A store written before this column exists gains it, defaulting 'lived'
    — every pre-existing claim was a live screen observation."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "old.db")
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE claim (id TEXT PRIMARY KEY, t_ms INTEGER, "
                    "device_id TEXT, modality TEXT, grade INTEGER)")
        con.execute("INSERT INTO claim VALUES ('c1', 1, 'mac-1', 'screen', 4)")
        con.commit()
        added = migrate_claim_table(con)
        assert "klass" in added
        val = con.execute("SELECT klass FROM claim WHERE id='c1'").fetchone()[0]
        con.close()
        assert val == "lived"
