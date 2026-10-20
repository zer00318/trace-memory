"""WHICH SENSE MAY MINT WHICH KIND — the whole birth layer, in one table.

Three separate bugs this session had ONE cause: a grammar written for a body,
fed a screen.

    _OBJECT_RE      "a red mug"      -> green clay, blue and, white cropped
    _READING_RE     "reading the X"  -> "their accessibility contexts and m"
    _ANON_PERSON_RE "a person"       -> "someone commented on your post"
    _URL_RE         "chess.com"      -> esa.com, agram.com, ail.com

Each was found by noticing its output in an answer, one at a time, over four
iterations. This test exists so the FOURTH one cannot be found that way: a new
grammar added without a gate changes this matrix and fails here, before it
reaches anyone's memory.

The bait string trips every grammar at once. What comes out is only what the
sense in question is entitled to produce.
"""
import pytest

from tests.helpers import make_claim
from tracemem.domain import Modality
from tracemem.domain.episode import Episode
from tracemem.domain.time import TimeRange
from tracemem.nodes.birth.entity_extractor import extract_mentions

BAIT = ("a red mug beside someone reading the Economist daily at chess.com "
        "with Priya — another person said hello")


def _kinds(modality, perceiver):
    c = make_claim(text=BAIT, t_ms=1000, modality=modality,
                   perceiver=perceiver)
    ep = Episode(id="e1", t_range=TimeRange(1000, 1001), place_key="system",
                 claim_ids=(c.id,))
    return {m.kind for m in extract_mentions(ep, [c])}


@pytest.mark.parametrize("modality,perceiver,expected", [
    # A SCREEN read by OCR: it can say which app and where the machine is,
    # and it may PROPOSE a name (the model judges that separately, at night —
    # see nodes/birth/person_judge.py). It may not name a site, because OCR
    # turns chess.com into esa.com; nor an object or a topic, because those
    # grammars describe a room.
    (Modality.SCREEN, "ocr", {"app", "person", "place"}),
    # The accessibility tree reads exact strings, so it may name a site.
    (Modality.SCREEN, "ax", {"app", "person", "place", "site"}),
    # A camera has a body: objects and topics become real.
    (Modality.CAMERA, "ocr", {"app", "object", "person", "place", "topic"}),
    (Modality.CAMERA, "ax",
     {"app", "object", "person", "place", "site", "topic"}),
])
def test_the_gating_matrix(modality, perceiver, expected):
    assert _kinds(modality, perceiver) == expected


def test_a_screen_can_never_mint_a_body_kind():
    """The one-line version of the table, stated so a reader does not have to
    diff four parameter sets to see the rule."""
    for perceiver in ("ocr", "ax", "vlm", "gist"):
        got = _kinds(Modality.SCREEN, perceiver)
        assert "object" not in got and "topic" not in got, (perceiver, got)


def test_only_a_reader_names_a_site():
    """OCR does not fail loudly on a domain, it fails PLAUSIBLY: `esa.com` is
    a real domain somebody owns. 25 of 144 site nodes were head-truncations of
    another site already present, and that is a lower bound."""
    for guesser in ("ocr", "vlm", "asr"):
        assert "site" not in _kinds(Modality.SCREEN, guesser), guesser
    for reader in ("ax", "owned", "founder"):
        assert "site" in _kinds(Modality.SCREEN, reader), reader
