"""A website needs a scheme or a real web TLD — a filename is not a site.

Learned from the first live run on ~/Documents, where 'board.md' and 'crawl.mp4'
were misread as websites and flooded the graph.
"""
from __future__ import annotations

from tracemem.domain import Episode, TimeRange
from tracemem.nodes.birth.entity_extractor import extract_mentions
from tests.helpers import make_claim


def _sites(text):
    ep = Episode(id="e1", t_range=TimeRange(0, 1), place_key=None,
                 claim_ids=("c",))
    # perceiver="ax": since 2026-08-24 only a sense that READS a string may
    # name a site (entity_extractor._reads_exactly). This file is about the
    # GRAMMAR — domain vs filename — so it uses a sense allowed to apply it.
    ms = extract_mentions(ep, [make_claim(text=text, frame="f",
                                          perceiver="ax")])
    return {m.surface for m in ms if m.kind == "site"}


def test_real_domains_are_sites():
    assert "github.com" in _sites("pushed to github.com today")
    assert "chess.com" in _sites("played on chess.com")
    assert "techcrunch.com" in _sites("https://techcrunch.com/article")


def test_filenames_are_not_sites():
    assert _sites("see board.md and roadmap.md") == set()
    assert _sites("rendered crawl.mp4 and test.mov") == set()
    assert _sites("opened events.db and memory.json") == set()
