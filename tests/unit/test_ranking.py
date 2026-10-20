"""How often — the question a graph of names could not answer.

    $ ./trace "what websites do I use the most?"
      (not in memory)

There are 130 site nodes and the memory knows how many moments each appeared
in. Retrieval ranks nodes by COSINE SIMILARITY to the question and renders
each as a label; "most" is not a similarity, it is an order over counts, and
nothing in the supplier had ever counted anything.
"""
from tracemem.domain import Confidence, Grade, Node, NodeKind
from tracemem.domain.node import Attribute
from tracemem.nodes.graph_store import MemoryGraphStore
from tracemem.supplier.ranking import (rank, render, supply_ranking,
                                       wants_ranking)


def _n(nid, kind, key, value, n_eps, t=1000):
    return Node(id=nid, kind=kind, activation=0.5, last_active_t_ms=t,
                attributes=[Attribute(key, value, Grade.VERBATIM,
                                      Confidence(0.9),
                                      tuple("e%d" % i for i in range(n_eps)),
                                      t)])


def _graph():
    g = MemoryGraphStore()
    g.upsert_node(_n("s1", NodeKind.SITE, "site", "youtube.com", 97))
    g.upsert_node(_n("s2", NodeKind.SITE, "site", "lichess.org", 10))
    g.upsert_node(_n("s3", NodeKind.SITE, "site", "gmail.com", 7))
    g.upsert_node(_n("a1", NodeKind.APP, "app", "Brave Browser", 128))
    return g


def test_it_recognises_an_order_question_and_only_that():
    assert wants_ranking("what websites do I use the most?") is NodeKind.SITE
    assert wants_ranking("which apps do I use most often?") is NodeKind.APP
    assert wants_ranking("who do I talk to most?") is NodeKind.ENTITY
    # Not order questions — these belong to the other paths.
    assert wants_ranking("what was I doing yesterday") is None
    assert wants_ranking("who is Juno?") is None


def test_the_answer_is_ordered_by_count():
    rows = rank(_graph(), NodeKind.SITE)
    assert [lab for lab, _n, _t in rows] == ["youtube.com", "lichess.org",
                                             "gmail.com"]


def test_UBIQUITY_IS_NOT_APPLIED_HERE():
    """THE POINT OF THE FILE. `candidate_selector.ubiquitous` keeps
    `Brave Browser` (76.2% of episodes) out of the COMBINATION tier, because
    "both repeatedly present with X" is empty when X is present with
    everything. When the question is "what do I use most", that same fact
    makes it THE ANSWER. A gate copied from one place to the other would
    delete the answer."""
    rows = rank(_graph(), NodeKind.APP)
    assert rows and rows[0][0] == "Brave Browser"


def test_one_habit_is_not_counted_as_two():
    """A ranking that lists `youtube.com` and `YouTube.com` separately is
    counting one habit twice — OCR variants sit beside the real thing."""
    g = _graph()
    g.upsert_node(_n("s4", NodeKind.SITE, "site", "YouTube.com", 5))
    rows = rank(g, NodeKind.SITE)
    labels = [lab.lower() for lab, _n, _t in rows]
    assert labels.count("youtube.com") == 1
    assert rows[0][1] == 97 + 5


def test_the_render_says_what_the_number_IS():
    """The count is occasions, not minutes. Left unsaid, "used most" becomes
    "used for hours" in the answer, which the data cannot support."""
    text = render(NodeKind.SITE, rank(_graph(), NodeKind.SITE))
    assert "OCCASIONS" in text and "not minutes" in text
    assert "moment(s)" in text


def test_a_non_order_question_supplies_nothing():
    assert supply_ranking("who is Juno?", _graph(), 4000) is None


def test_ranking_cites_real_graph_nodes_not_a_literal_placeholder():
    context = supply_ranking("what sites do I use most?", _graph(), 4000)
    block = context.blocks[0]
    assert "ranking" not in block.source_ids
    assert block.source_ids[:3] == ("s1", "s2", "s3")
    assert "[s1]" in block.text and "[s2]" in block.text


def test_www_is_the_same_habit():
    """MEASURED on the live ranking: `youtube.com` 242 moments and
    `www.youtube.com` 68, listed first and second — one habit shown as two,
    understated in both rows. Only `www.` folds: `m.` and `mail.` and `docs.`
    are genuinely different surfaces of a service."""
    g = _graph()
    g.upsert_node(_n("s5", NodeKind.SITE, "site", "www.youtube.com", 68))
    g.upsert_node(_n("s6", NodeKind.SITE, "site", "mail.google.com", 4))
    g.upsert_node(_n("s7", NodeKind.SITE, "site", "docs.google.com", 3))
    rows = rank(g, NodeKind.SITE)
    top = rows[0]
    assert top[0] == "youtube.com" and top[1] == 97 + 68
    labels = [lab for lab, _n, _t in rows]
    assert "mail.google.com" in labels and "docs.google.com" in labels
