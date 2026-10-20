"""OWNS: deep OWNED mining — re-reading a file's BODY where a node is hungry
(analysis gap E / commitment 2; the founder's 'broad index → mine deep').

The broad pass (reader.py) takes only a file's headline, cheaply, over the whole
disk. This is the second pass: when a node born from an OWNED source still has
open questions (its agenda), go BACK to that source — which, unlike a discarded
screen pixel, still exists — and read the body, emitting the finer OWNED claims
the headline skipped. Same economy as the re-marination loop over raw claims,
turned on the corpus.

Agenda-driven and source-bounded: only hungry nodes trigger a read, and each
source is read once per pass. The new claims re-enter the log and the binder
exactly like broad ones (same atom, same birth), so a deep read of 'met with
Priya about the budget' can finally answer the 'who is this?' a headline could
not.
"""
from __future__ import annotations

import os
from typing import Dict, List, Set

from tracemem.domain import Claim, ProvenanceClass
from tracemem.nodes.accrual import agenda_keys, episodes_of
from tracemem.owned.formats import SourceUnreadable, read_source
from tracemem.owned.reader import _owned_claim

_MIN_LINE = 4                  # ignore trivially short lines (bullets, separators)


def deep_read(source: str, device_id: str) -> List[Claim]:
    """Every meaningful line of a source as its own OWNED claim — the body the
    broad headline pass left on the table. Idempotent through the log's content
    id (re-reading an unchanged file re-mints identical, deduped claims)."""
    try:
        decoded = read_source(source, broad=False)
        t_ms = int(os.path.getmtime(source) * 1000)
    except (OSError, SourceUnreadable):
        return []

    out: List[Claim] = []
    for line in decoded.text.splitlines():
        s = line.strip().lstrip("#").strip()
        if len(s) >= _MIN_LINE:
            out.append(_owned_claim(s, os.path.abspath(source), device_id, t_ms))
    return out


def sources_for_node(node, claims_by_episode: Dict[str, list]) -> Set[str]:
    """The OWNED source files a node's evidence came from — where a deeper read
    could answer its open questions."""
    out: Set[str] = set()
    for ep in episodes_of(node):
        for c in claims_by_episode.get(ep, []):
            if (c.provenance.klass is ProvenanceClass.OWNED
                    and c.coord.capture.source):
                out.add(c.coord.capture.source)
    return out


def deep_mine_owned(graph_store, claims_by_episode: Dict[str, list],
                    device_id: str) -> List[Claim]:
    """For every HUNGRY node (open agenda) born from an OWNED source, re-read
    that source's body. Returns the new claims (to append + bind). Each source
    read once per pass."""
    claims_by_episode = claims_by_episode or {}
    new_claims: List[Claim] = []
    read: Set[str] = set()
    for node in list(graph_store.nodes.values()):
        if not agenda_keys(node):                   # not hungry → leave the body unread
            continue
        for src in sources_for_node(node, claims_by_episode):
            if src in read:
                continue
            read.add(src)
            new_claims.extend(deep_read(src, device_id))
    return new_claims
