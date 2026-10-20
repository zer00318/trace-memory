"""Semantic newness: paraphrase suppressed, new nuance passes, per-scene."""
from __future__ import annotations

from tests.fakes import HashEmbedder
from tracemem.extraction.newness import SaidThings


def make():
    return SaidThings(HashEmbedder())


def test_first_utterance_is_new_and_recorded():
    s = make()
    assert s.check_and_record("scene1", "the jar is 75 percent empty")
    assert s.said_count("scene1") == 1


def test_near_duplicate_stays_silent():
    s = make()
    s.record("scene1", "the jar is 75 percent empty")
    novel, sim = s.is_new("scene1", "jar is 75 percent empty")
    assert not novel and sim >= 0.9


def test_new_nuance_passes():
    s = make()
    s.record("scene1", "the jar is 75 percent empty")
    novel, _ = s.is_new("scene1", "the lid is sitting ajar")
    assert novel


def test_scenes_do_not_share_memory():
    s = make()
    s.record("scene1", "deploy failed with OOM")
    novel, _ = s.is_new("scene2", "deploy failed with OOM")
    assert novel                       # said THERE, not HERE


def test_refused_claim_is_not_marked_said():
    s = make()
    s.record("k", "meeting moved to thursday afternoon")
    assert not s.check_and_record("k", "meeting moved to thursday afternoon")
    assert s.said_count("k") == 1      # deciding ≠ remembering


def test_partial_change_chat_fixture():
    """THE Nutella edge: one new bubble in a still chat — old messages
    stay known, only the changed region's utterances are forgotten."""
    s = make()
    s.record("chat", "alice: see you at nine", bbox=(0.1, 0.10, 0.9, 0.15))
    s.record("chat", "bob: bringing the slides", bbox=(0.1, 0.20, 0.9, 0.25))
    s.record("chat", "WhatsApp — Team Standup", bbox=None)   # scene-wide header
    dropped = s.invalidate_region("chat", (0.0, 0.18, 1.0, 0.30))
    assert dropped == 1
    novel_old, _ = s.is_new("chat", "alice: see you at nine")
    assert not novel_old                       # the still 90% stays known
    novel_changed, _ = s.is_new("chat", "bob: bringing the slides")
    assert novel_changed                       # the changed region is news again
    novel_header, _ = s.is_new("chat", "WhatsApp — Team Standup")
    assert not novel_header                    # scene-wide survives region change
