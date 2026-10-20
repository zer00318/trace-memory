"""OWNS: OWNED ingestion — the corpus through the single writer.

Ties the OWNED reader to the Hub so the whole OWNED-first cold-start runs end to
end on the REAL pipeline: broad_index → hub.post_claims (log → bind → birth),
then an optional deep pass that re-reads sources where the freshly-born nodes are
hungry. No new writer: OWNED claims go through the exact path LIVED ones do, so
the two hemispheres share segmentation, birth, activation, and the night.
"""
from __future__ import annotations

from typing import Dict

from tracemem.owned.deep import deep_mine_owned
from tracemem.owned.history import broad_history
from tracemem.owned.reader import broad_index


def ingest_corpus(hub, root: str, device_id: str) -> Dict[str, int]:
    """Broad-index `root` and post every OWNED claim through the hub's single
    writer. Returns the reader's + hub's counts."""
    res = broad_index(root, device_id, resume_store=hub.w.claim_log)
    posted = hub.post_claims(res.claims)
    # The receipt is later than append AND binding.  If power fails before
    # this point, a restart reads the file again and the immutable log plus
    # binding receipts make that replay safe.  Marking earlier could strand
    # an unbound source forever.
    mark = getattr(hub.w.claim_log, "finish_owned_source", None)
    if mark is not None:
        for source, fingerprint, version in res.receipts:
            mark(source, fingerprint, version)
    history = broad_history(root, device_id, resume_store=hub.w.claim_log)
    history_posted = hub.post_claims(history.claims)
    mark_cursor = getattr(hub.w.claim_log, "finish_owned_stream", None)
    if mark_cursor is not None:
        for source, version, cursor in history.receipts:
            mark_cursor(source, version, cursor)
    return {"files_indexed": res.files_indexed,
            "files_skipped": res.files_skipped,
            "files_resumed": res.files_resumed,
            "permission_denied": res.skipped_reasons.get("permission-denied", 0),
            "history_visits": history.visits_seen,
            "history_sources": history.sources_read,
            "history_unreadable": history.sources_unreadable,
            "appended": posted.get("appended", 0) +
                        history_posted.get("appended", 0),
            "episodes": history_posted.get("episodes", posted.get("episodes", 0)),
            "questions": posted.get("questions", 0) +
                         history_posted.get("questions", 0)}


def deepen_corpus(hub, device_id: str) -> Dict[str, int]:
    """Second pass: where hub nodes born from OWNED sources are still hungry,
    re-read those sources' bodies and post the deeper claims through the same
    writer. Idempotent through the log's content id — an unchanged file adds
    nothing on a re-run."""
    # The hub is the only holder of episode→claims; this orchestration helper
    # reads it deliberately (same package), never writes it.
    new = deep_mine_owned(hub.w.graph_store, hub._claims_by_episode, device_id)
    posted = hub.post_claims(new)
    return {"deep_claims": len(new), **posted}


def ingest_and_deepen(hub, root: str, device_id: str) -> Dict[str, dict]:
    """The FULL OWNED pass over one corpus root: broad-index every file, then
    deep-mine the sources behind any node still hungry. This is the complete,
    runnable cold-start ingestion — the only thing it needs that this code does
    not supply is `root`, which is the user's own consent decision (never a
    default; the reader is never pointed at a disk on its own)."""
    return {"broad": ingest_corpus(hub, root, device_id),
            "deep": deepen_corpus(hub, device_id)}
