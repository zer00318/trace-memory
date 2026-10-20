"""The OWNED broad-index reader (commitment 2: OWNED-first).

Runs against a SYNTHETIC fixture directory — never the user's real files. Proves
the reader emits honest OWNED claims, that they survive the immutable log, and
that an OWNED claim births a node through the SAME binder as a LIVED one (the
two hemispheres share machinery).
"""
from __future__ import annotations

import os
import zipfile

from tracemem.domain import (
    CaptureCoord, Claim, Confidence, CoordinateStack, Grade, Modality,
    NodeKind, Provenance, ProvenanceClass, SpanCoord, WorldCoord,
)
from tracemem.domain.episode import Episode
from tracemem.domain.time import TimeRange
from tracemem.nodes.marination.background import upkeep
from tracemem.nodes.graph_store import MemoryGraphStore
from tracemem.owned.reader import broad_index
from tracemem.text.claim_log import ClaimLog


def _corpus(root):
    with open(os.path.join(root, "notes.md"), "w") as f:
        f.write("# Project Apollo roadmap\n\nsome body text\n")
    with open(os.path.join(root, "empty.txt"), "w") as f:
        f.write("")
    sub = os.path.join(root, "sub")
    os.mkdir(sub)
    with open(os.path.join(sub, "todo.txt"), "w") as f:
        f.write("buy milk\ncall the bakery\n")
    with open(os.path.join(root, "photo.png"), "wb") as f:
        f.write(b"\x89PNG\x00\x00binary")            # must be skipped (not text)


def test_broad_index_emits_owned_headline_claims(tmp_path):
    _corpus(str(tmp_path))
    res = broad_index(str(tmp_path), device_id="mac-1")

    assert res.files_indexed == 3                    # notes.md, empty.txt, todo.txt
    assert res.skipped_reasons.get("not-text") == 1  # the .png
    texts = {c.text for c in res.claims}
    assert "Project Apollo roadmap" in texts         # markdown heading → headline
    assert "buy milk" in texts                        # first line of todo.txt
    assert "empty.txt" in texts                       # empty file → its name

    for c in res.claims:
        assert c.provenance.klass is ProvenanceClass.OWNED
        assert c.coord.capture.source and os.path.isabs(c.coord.capture.source)
        assert c.coord.capture.frame_id is None       # never a fake frame
        assert c.scope == "memory"


def test_owned_claims_survive_the_immutable_log(tmp_path):
    _corpus(str(tmp_path))
    res = broad_index(str(tmp_path), device_id="mac-1")
    log = ClaimLog(os.path.join(str(tmp_path), "m.db"))
    for c in res.claims:
        log.append(c)
    back = [log.get(c.id) for c in res.claims]
    assert all(b is not None for b in back)
    assert all(b.provenance.klass is ProvenanceClass.OWNED for b in back)
    # queryable as OWNED without decryption
    import sqlite3
    con = sqlite3.connect(os.path.join(str(tmp_path), "m.db"))
    n_owned = con.execute("SELECT COUNT(*) FROM claim WHERE klass='owned'").fetchone()[0]
    con.close()
    assert n_owned == len(res.claims)


def test_an_owned_claim_births_a_node_through_the_same_binder():
    """An OWNED claim mentioning a site must birth a site node exactly as a
    LIVED one would — proving the hemispheres share the birth machinery."""
    coord = CoordinateStack(
        world=WorldCoord(place_key=None),
        capture=CaptureCoord.owned(source="/Users/x/notes.md", device_id="mac-1"),
        span=SpanCoord(bbox=None), t_ms=1000)
    owned = Claim(
        text="roadmap notes: see https://chess.com/games later",
        coord=coord,
        provenance=Provenance("owned:broad_index", Modality.SENSOR,
                              klass=ProvenanceClass.OWNED),
        grade=Grade.SPECIALIST, confidence=Confidence(0.9),
        bidder="owned:broad_index", bid_id="owned:1", bid_predicted_value=0.0,
        actual_cost=0.0, scope="memory")

    g = MemoryGraphStore()
    ep = Episode(id="e1", t_range=TimeRange(1000, 1001), place_key=None,
                 claim_ids=(owned.id,))
    upkeep(ep, [owned], g)

    assert any(n.kind is NodeKind.SITE for n in g.nodes.values()), \
        "an OWNED claim with a URL should birth a site node like any LIVED one"


def test_broad_index_reads_mail_and_office_documents_locally(tmp_path):
    (tmp_path / "letter.eml").write_bytes(
        b"Subject: Lunch with Maya\nContent-Type: text/plain; charset=utf-8\n\n"
        b"book the table on Friday\n")
    with zipfile.ZipFile(tmp_path / "plan.docx", "w") as zf:
        zf.writestr(
            "word/document.xml",
            "<w:document xmlns:w='urn:w'><w:body><w:p><w:r>"
            "<w:t>Project Juniper roadmap</w:t></w:r></w:p></w:body>"
            "</w:document>")

    res = broad_index(str(tmp_path), device_id="mac-1")

    assert {"Lunch with Maya", "Project Juniper roadmap"} <= {
        c.text for c in res.claims}
    assert all(c.provenance.klass is ProvenanceClass.OWNED for c in res.claims)
