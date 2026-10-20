"""OWNS: the broad-index reader — the cheap first pass over a corpus.

The founder's day-one choice (2026-07-24 brainstorm): read the user's OWN
files broadly and shallowly first, THEN let hungry nodes mine deep where the
map is dense (the existing re-marination loop does the deep pass). This module
owns ONLY the broad pass: skim each text file, emit ONE headline OWNED claim
per file, carrying its re-readable `source` so a later expedition can go back.

Laws it keeps:
- READ-ONLY: opens files for reading, writes nothing. On-device by construction
  (it only takes a path).
- HONEST WORLD: every claim is klass=OWNED with capture.source = the absolute
  path — never masquerading as a lived screen observation (the atom enforces
  this; here we simply never fabricate a frame).
- SHALLOW ON PURPOSE: the headline only (first meaningful line). Reading bodies,
  extracting people/events/times, is the DEEP pass and belongs to mining, not
  here — so this stays cheap enough to run over a whole disk.
- SKIPS WHAT IT CANNOT HONESTLY READ: binaries, oversized files, and unreadable
  bytes are counted and skipped, never half-decoded into a plausible lie.
"""
from __future__ import annotations

import os
import errno
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from tracemem.domain import (
    CaptureCoord, Claim, Confidence, CoordinateStack, Modality,
    Provenance, ProvenanceClass, SpanCoord, WorldCoord,
)
from tracemem.domain.grading import grade_for

_PERCEIVER = "owned:broad_index"

from tracemem.owned.formats import (
    MAX_TEXT_BYTES as MAX_BYTES, SUPPORTED_EXTS, TEXT_EXTS, SourceUnreadable,
    read_source,
)

# Backwards-compatible name used by callers that deliberately narrow the pass.
# The default now covers durable documents and mail as well as plain text.
OWNED_EXTS = SUPPORTED_EXTS
READER_VERSION = "owned-broad-v2"
HEADLINE_MAX = 200              # truncate a headline to this many chars

# Directories the broad pass never descends into: vendored code, caches, VCS
# internals, virtualenvs. They are machinery, not the person, and crawling them
# buries real signal under thousands of generated files.
EXCLUDE_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__",
    "site-packages", "dist-info", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".cache", "build", "dist", ".ralph", ".trace_supervisor", ".secrets",
    ".egg-info", "vendor", ".codex", ".claude",
}


@dataclass
class BroadIndexResult:
    claims: List[Claim] = field(default_factory=list)
    files_seen: int = 0
    files_indexed: int = 0
    files_skipped: int = 0
    files_resumed: int = 0
    skipped_reasons: dict = field(default_factory=dict)   # reason -> count
    # (absolute source, exact content digest, reader version), recorded only
    # after the corresponding claims have made it through the single writer.
    receipts: List[Tuple[str, str, str]] = field(default_factory=list)

    def _skip(self, reason: str) -> None:
        self.files_skipped += 1
        self.skipped_reasons[reason] = self.skipped_reasons.get(reason, 0) + 1


def _headline(text: str, fallback: str) -> str:
    for line in text.splitlines():
        s = line.strip().lstrip("#").strip()        # markdown heading → bare title
        if s:
            return s[:HEADLINE_MAX]
    return fallback                                 # empty file → its name


def broad_index(root: str, device_id: str, now_ms: Optional[int] = None,
                exts=None, exclude_dirs=None, resume_store=None) -> BroadIndexResult:
    """Walk `root`, emit one headline OWNED claim per readable text file.

    `exts` narrows which extensions to index (default: all TEXT_EXTS).
    `exclude_dirs` names directories never to descend into (default EXCLUDE_DIRS
    plus every dotdir) — machinery, not the person.

    `now_ms` is only a fallback stamp for files whose mtime is unreadable; the
    real stamp is the file's modified time — when the fact last existed in the
    user's world, which is what a rhythm over the corpus needs.
    """
    exts = exts if exts is not None else OWNED_EXTS
    exclude_dirs = exclude_dirs if exclude_dirs is not None else EXCLUDE_DIRS
    result = BroadIndexResult()
    def walk_error(error):
        reason = ("permission-denied" if getattr(error, "errno", None)
                  in (errno.EACCES, errno.EPERM) else "unreadable-directory")
        result._skip(reason)

    for dirpath, dirs, files in os.walk(root, onerror=walk_error):
        dirs[:] = [d for d in dirs                       # prune in place (os.walk)
                   if d not in exclude_dirs and not d.startswith(".")
                   and not d.endswith(".egg-info")]
        for name in files:
            result.files_seen += 1
            path = os.path.join(dirpath, name)
            ext = os.path.splitext(name)[1].lower()
            if ext not in exts:
                result._skip("not-text")
                continue
            absolute = os.path.abspath(path)
            try:
                decoded = read_source(absolute, broad=True)
                mtime_ms = int(os.path.getmtime(absolute) * 1000)
            except SourceUnreadable as exc:
                reason = str(exc)
                result._skip("too-big" if reason == "too big" else "unreadable")
                continue

            seen = getattr(resume_store, "owned_source_complete", None)
            if seen is not None and seen(absolute, decoded.fingerprint,
                                         READER_VERSION):
                result.files_resumed += 1
                continue

            t_ms = mtime_ms if mtime_ms else (now_ms or 0)
            headline = decoded.title or _headline(decoded.text, name)
            claim = _owned_claim(headline, absolute,
                                 device_id, t_ms)
            result.claims.append(claim)
            result.receipts.append((absolute, decoded.fingerprint,
                                    READER_VERSION))
            result.files_indexed += 1
    return result


def _owned_claim(headline: str, source: str, device_id: str, t_ms: int,
                 perceiver: str = _PERCEIVER) -> Claim:
    coord = CoordinateStack(
        world=WorldCoord(place_key=None),           # a file has no gps/place
        capture=CaptureCoord.owned(source=source, device_id=device_id),
        span=SpanCoord(bbox=None),                   # no pixel rectangle
        t_ms=t_ms,
    )
    return Claim(
        text=headline, coord=coord,
        provenance=Provenance(perceiver=perceiver,
                              modality=Modality.SENSOR,
                              klass=ProvenanceClass.OWNED),
        grade=grade_for(perceiver),                  # THE table (domain/grading.py)
        confidence=Confidence(0.9),
        # OWNED reads are not auctioned (no gaze), but the store's birth-stamp
        # gate is unconditional, so we stamp honestly: a zero-cost non-bid.
        bidder=perceiver,
        bid_id="owned:%s:%d" % (source, t_ms),
        bid_predicted_value=0.0, actual_cost=0.0,
        scope="memory",                             # a corpus fact is memory-scoped (A4)
    )
