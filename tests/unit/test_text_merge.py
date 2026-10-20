"""Cross-frame merge: 40 sightings, one claim, best read, honest provenance."""
from __future__ import annotations

from tracemem.extraction.text_merge import TextMerger, merge_frames

BOX = (0.1, 0.1, 0.5, 0.15)


def test_forty_sightings_become_one_with_best_read():
    tm = TextMerger()
    for i in range(40):
        conf = 0.6 if i != 25 else 0.98          # one sharp frame mid-stream
        tm.observe("The quarterly report is due Friday", BOX, conf, t_ms=i * 1000)
    (m,) = tm.merged()
    assert m.sightings == 40 and m.confidence == 0.98
    assert m.first_t_ms == 0 and m.last_t_ms == 39_000
    assert len(m.all_readings) == 40


def test_same_text_different_place_stays_distinct():
    tm = TextMerger()
    tm.observe("Submit", BOX, 0.9, 1)
    tm.observe("Submit", (0.7, 0.8, 0.9, 0.85), 0.9, 2)   # another button
    assert len(tm.merged()) == 2


def test_whitespace_and_case_do_not_split_identity():
    tm = TextMerger()
    tm.observe("Hello  World", BOX, 0.7, 1)
    tm.observe("hello world", BOX, 0.95, 2)
    (m,) = tm.merged()
    assert m.text == "hello world" and m.confidence == 0.95


def test_moved_text_beyond_iou_is_a_new_sighting_place():
    tm = TextMerger()
    tm.observe("chapter one", (0.1, 0.1, 0.5, 0.15), 0.9, 1)
    tm.observe("chapter one", (0.1, 0.6, 0.5, 0.65), 0.9, 2)   # scrolled far
    assert len(tm.merged()) == 2


def test_merge_frames_convenience():
    frames = [[("a", BOX, 0.5, 1)], [("a", BOX, 0.8, 2)], [("b", BOX, 0.9, 3)]]
    got = {m.text: m for m in merge_frames(frames)}
    assert got["a"].sightings == 2 and got["a"].confidence == 0.8
    assert got["b"].sightings == 1
