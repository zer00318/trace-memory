"""A belief a person can recognise as being about them.

MEASURED 2026-08-25 on the founder's real graph. This is what the entire
belief tier looked like:

    recurring: page=/watch observed in 230 distinct moments
    recurring: page=/ observed in 13 distinct moments
    recurring: page=/search observed in 9 distinct moments

Every one true, and every one a database row read aloud — the memory
describing its own index rather than anything about a life. It is why the
morning surface had nothing sayable to offer, and why someone reading their
own beliefs would not recognise a single one of them.

SOME KEYS CANNOT BE SAID AT ALL. `page` holds URL path fragments; the real
values in that graph were '/tou', '/reel/DY' and '/reel/DYQ5NmxN' — the last
two OCR-truncated copies of one URL. A pattern over those is noise however it
is worded, so none is minted.
"""
from __future__ import annotations

from tracemem.domain import Confidence, Grade
from tracemem.domain.node import Attribute, Node, NodeKind
from tracemem.nodes.combination.kinds.pattern import MIN_EPISODES, detect

EPS = tuple("ep%d" % i for i in range(MIN_EPISODES))


def _attr(key, value, eps=EPS):
    return Attribute(key=key, value=value, grade=Grade.VERBATIM,
                     confidence=Confidence(0.9), source_episode_ids=eps,
                     t_ms=1000)


def _node(*attrs):
    return Node(id="n1", kind=NodeKind.ENTITY, attributes=list(attrs))


class Store:
    def neighbors(self, node_id, hops=1):
        return []


def _texts(node):
    return [p.claim_text for p in detect(node, Store())
            if getattr(p, "kind", None) == "pattern"]


def test_a_pattern_is_said_in_english():
    got = _texts(_node(_attr("site", "lichess.org")))
    assert got == ["you keep going back to lichess.org — in 3 moments"]


def test_no_belief_carries_a_raw_key():
    for key, value in (("site", "lichess.org"), ("app", "Brave"),
                       ("seen_as", "Juno"), ("place", "wifi:home")):
        for text in _texts(_node(_attr(key, value))):
            assert "=" not in text, text
            assert "recurring:" not in text, text
            assert value in text


def test_a_url_path_mints_nothing():
    """'/reel/DY' and '/reel/DYQ5NmxN' are OCR-truncated halves of one URL.
    No phrasing rescues a pattern over them."""
    assert _texts(_node(_attr("page", "/watch"))) == []
    assert _texts(_node(_attr("page", "/"))) == []


def test_an_unknown_key_mints_nothing_rather_than_a_row():
    """The failure direction matters: a key nobody has taught this module to
    say must produce silence, not `newkey=value observed in N moments`."""
    assert _texts(_node(_attr("some_new_key", "whatever"))) == []


def test_a_routine_is_already_a_sentence():
    """schemas.py writes these in English already; wrapping them would give
    'you keep going back to around 07:00 you are usually at...'."""
    got = _texts(_node(_attr("routine", "around 07:00 you are usually at home")))
    assert got == ["around 07:00 you are usually at home — in 3 moments"]


def test_it_still_counts():
    """The count is the evidence for calling it a pattern at all; losing it
    would make a thing seen three times read like a law."""
    assert "in 3 moments" in _texts(_node(_attr("app", "Brave")))[0]


def test_too_few_moments_is_still_not_a_pattern():
    few = tuple("ep%d" % i for i in range(MIN_EPISODES - 1))
    assert _texts(_node(_attr("site", "lichess.org", few))) == []


def test_a_machine_token_is_not_a_place_someone_has_been():
    """WITNESSED on the real graph, in the morning surface, unprompted:

        you are often at system — in 72 moments

    `binder/cross_pillar.place_key` returns "dev:<device>" at 0.0 confidence
    when no SSID has been vouched for and there is no GPS — the honest answer
    for "this machine, somewhere". It is a correct value and it is not a
    PLACE anyone has ever been. Telling someone where they spend their life
    and naming an internal token is true, useless, and slightly unsettling.
    """
    assert _texts(_node(_attr("place", "system"))) == []
    assert _texts(_node(_attr("place", "dev:mac-1"))) == []
    assert _texts(_node(_attr("place", "wifi:unknown"))) == []


def test_a_real_place_still_becomes_a_belief():
    """When the founder names home and office, or the phone pillar supplies
    GPS, this turns itself back on with no code change."""
    got = _texts(_node(_attr("place", "wifi:home")))
    assert got == ["you are often at wifi:home — in 3 moments"]
