"""OWNED-first, end to end on the REAL pipeline (commitment 2).

A corpus flows through the actual Hub — log → bind → birth — and its OWNED facts
become nodes exactly as LIVED ones would. The deep pass then reaches a fact the
broad headline never saw. This is the cold-start win, executable.
"""
from __future__ import annotations

import os

from tests.fakes import ContextAnswerReasoner, FakeClock, HashEmbedder
from tracemem.app.wiring import build
from tracemem.domain import NodeKind
from tracemem.nodes.sqlite_graph_store import SqliteGraphStore
from tracemem.owned.ingest import deepen_corpus, ingest_and_deepen, ingest_corpus
from tracemem.text.claim_log import ClaimLog

MS_DAY = 86_400_000
_BODY = ("# Roadmap at https://chess.com/games\n\n"
         "met with Priya about https://budget.example.com\n")


def _hub():
    return build({"embedder": HashEmbedder(),
                  "reasoner": ContextAnswerReasoner(),
                  "clock": FakeClock(400 * MS_DAY)})


def _sites(hub):
    return {a.value for n in hub.w.graph_store.nodes.values()
            if n.kind is NodeKind.SITE for a in n.attributes if a.key == "site"}


def test_owned_corpus_births_nodes_through_the_real_hub(tmp_path):
    with open(os.path.join(str(tmp_path), "notes.md"), "w") as f:
        f.write(_BODY)

    hub = _hub()
    stats = ingest_corpus(hub, str(tmp_path), device_id="mac-1")
    assert stats["files_indexed"] == 1
    assert stats["appended"] >= 1

    # the headline's URL birthed a site node through the ordinary birth path
    assert "chess.com" in _sites(hub)
    # broad is headline-only: the body's URL is NOT in yet
    assert "budget.example.com" not in _sites(hub)


def test_deep_pass_reaches_a_fact_the_headline_missed(tmp_path):
    with open(os.path.join(str(tmp_path), "notes.md"), "w") as f:
        f.write(_BODY)

    hub = _hub()
    ingest_corpus(hub, str(tmp_path), device_id="mac-1")
    deepen_corpus(hub, device_id="mac-1")               # re-read the body

    sites = _sites(hub)
    assert "chess.com" in sites
    assert "budget.example.com" in sites                # the body fact, now known


def test_full_owned_pass_in_one_call(tmp_path):
    """ingest_and_deepen is the complete cold-start ingestion over a root — the
    runnable operation that needs only the user's corpus path."""
    with open(os.path.join(str(tmp_path), "notes.md"), "w") as f:
        f.write(_BODY)

    hub = _hub()
    out = ingest_and_deepen(hub, str(tmp_path), device_id="mac-1")
    assert out["broad"]["files_indexed"] == 1
    sites = _sites(hub)
    assert {"chess.com", "budget.example.com"} <= sites    # broad + deep, one call


def _durable_hub(raw_path, graph_path):
    return build({"claim_log": ClaimLog(str(raw_path)),
                  "graph_store": SqliteGraphStore(str(graph_path)),
                  "embedder": HashEmbedder(),
                  "reasoner": ContextAnswerReasoner(),
                  "clock": FakeClock(400 * MS_DAY),
                  "verify_answers": False})


def _close(hub):
    hub.w.claim_log._db.close()
    hub.w.graph_store.close()


def test_cold_start_resumes_by_exact_content_after_process_restart(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    source = corpus / "notes.md"
    source.write_text(_BODY)
    raw, graph = tmp_path / "raw.sqlite3", tmp_path / "graph.sqlite3"

    first = _durable_hub(raw, graph)
    initial = ingest_corpus(first, str(corpus), "mac-1")
    count = first.w.claim_log.count()
    assert initial["files_indexed"] == 1
    assert first.w.claim_log.owned_source_receipts() == 1
    _close(first)

    second = _durable_hub(raw, graph)
    resumed = ingest_corpus(second, str(corpus), "mac-1")
    assert resumed["files_indexed"] == 0
    assert resumed["files_resumed"] == 1
    assert second.w.claim_log.count() == count

    # Content, not mtime, owns the receipt.  Preserve the old timestamp while
    # changing bytes: this must be indexed as new evidence, never skipped.
    old_times = (source.stat().st_atime_ns, source.stat().st_mtime_ns)
    source.write_text(_BODY + "changed without a timestamp signal\n")
    os.utime(source, ns=old_times)
    changed = ingest_corpus(second, str(corpus), "mac-1")
    assert changed["files_indexed"] == 1
    assert second.w.claim_log.owned_source_receipts() == 2
    _close(second)


def test_restart_receipts_do_not_store_the_source_path_in_plaintext(tmp_path):
    corpus = tmp_path / "private-project-name"
    corpus.mkdir()
    (corpus / "secret-filename.md").write_text("private title")
    raw, graph = tmp_path / "raw.sqlite3", tmp_path / "graph.sqlite3"
    hub = _durable_hub(raw, graph)
    ingest_corpus(hub, str(corpus), "mac-1")

    rows = hub.w.claim_log._db.execute(
        "select source_key,fingerprint,reader_version,status "
        "from owned_source_receipt").fetchall()
    assert len(rows) == 1
    assert "private-project-name" not in repr(rows)
    assert "secret-filename" not in repr(rows)
    _close(hub)


def test_hungry_deep_source_survives_a_real_graph_restart(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "notes.md").write_text(_BODY)
    raw, graph = tmp_path / "raw.sqlite3", tmp_path / "graph.sqlite3"

    first = _durable_hub(raw, graph)
    ingest_corpus(first, str(corpus), "mac-1")
    assert "budget.example.com" not in _sites(first)
    _close(first)

    second = _durable_hub(raw, graph)
    out = deepen_corpus(second, "mac-1")
    assert out["deep_claims"] > 0
    assert "budget.example.com" in _sites(second)
    _close(second)
