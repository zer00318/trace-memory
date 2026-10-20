"""OWNS: what the memory says when nobody asked — and when it says nothing.

`The morning surface / The content picker`: at most three items a night,
chosen by what actually moved, and NO FILLER, EVER.

THE EMPTY MORNING IS THE FEATURE. Every product that surfaces something
daily learns to pad, because a blank screen feels like a broken one. Padding
is how a memory starts telling you things it does not believe: the third-best
item on a quiet Tuesday is not a finding, it is a sentence generated to fill
a slot. Once it does that a person cannot tell the difference between the day
it noticed something and the day it had nothing — which destroys the only
reason to read it at all.

So a quiet night returns nothing and the surface stays dark. That is not a
degraded state; it is the honest report of a quiet night.

WHAT COUNTS AS WORTH SAYING. Three currencies, in order, and each must clear
its own bar rather than merely being the best of a weak field:

  a question   the memory noticed a contradiction it cannot settle. The most
               valuable thing it can hand back, because answering one is
               worth more than anything it can derive alone.
  a pattern    something it saw enough times to name, newly confirmed.
  a surprise   a thing whose salience jumped — it expected otherwise.

EVERY ITEM CITES. An unprompted claim with no receipt is the confabulation
this product exists to refuse, and it is worse unprompted than in an answer:
nobody asked, so nobody is checking.
"""
from __future__ import annotations

from typing import List, Optional

MAX_ITEMS = 3

# The bars. Deliberately absolute, not relative — "top 3 of whatever exists"
# is exactly the rule that produces filler on a quiet night.
MIN_SALIENCE = 0.30

# RECURRENCE IS ALREADY GUARANTEED UPSTREAM, and this used to double-check it
# with the wrong quantity.
#
# The bar here was `len(parent_ids) >= 3`, with a comment about a pattern seen
# twice being a coincidence. But `parent_ids` is WHICH NODES contributed the
# attribute — not how many times it was observed. The recurrence count lives
# in combination/kinds/pattern.MIN_EPISODES, which already refuses to mint a
# pattern seen in fewer than three distinct moments.
#
# MEASURED on the founder's real graph: every one of its 162 emergent nodes
# has one or two parents and not one has three. So the bar rejected 100% of
# real patterns, and the morning surface was permanently, silently empty.
#
# That is the worst possible failure for THIS module, because empty is also
# what it is supposed to look like on a quiet night. A broken filter wearing
# the costume of honest silence would never have been questioned — the design
# note about "no filler, ever" would have explained it away for months.
MIN_PATTERN_PARENTS = 1

# WHAT COUNTING CANNOT CATCH.
#
# MEASURED 2026-08-25 against the founder's real graph. The count bars above
# passed all three of these, and every one is worthless to a person:
#
#   recurring: page=/watch observed in 230 distinct moments
#   recurring: page=/ observed in 13 distinct moments
#   recurring: page=/search observed in 9 distinct moments
#
# They are true. They are also the memory reporting ITS OWN INDEXING rather
# than anything about a life — "page=/" is a URL path, not a thing that
# happened. Surfacing them unprompted is precisely the filler this module was
# built to refuse; the bars caught weak-by-count and let weak-by-MEANING
# straight through.
#
# THE ROOT CAUSE IS UPSTREAM, in how nodes/marination/schemas.py labels a
# pattern: it names the raw key it grouped on. Fixing it there is the real
# repair and it is not this module's to make. What IS this module's job is
# the higher bar an unprompted item has to clear — nobody asked for this, so
# it had better be about them.
_MACHINE_SHAPED = ("page=", "app=", "host=", "url=", "site=", "path=",
                   "bundle=", "title=", "place=", "device=")


def _about_a_person_not_an_index(text: str) -> bool:
    """Would a person recognise this as something from their own life?

    A crude test, and deliberately so: it rejects labels carrying raw schema
    keys. A false negative costs one morning item; a false positive spends
    the founder's attention on a URL path and teaches them not to look.
    """
    low = str(text or "").lower()
    return not any(m in low for m in _MACHINE_SHAPED)


class Item:
    __slots__ = ("kind", "text", "citations", "node_id", "why")

    def __init__(self, kind, text, citations, node_id, why):
        self.kind = kind
        self.text = text
        self.citations = tuple(citations)
        self.node_id = node_id
        self.why = why

    def __repr__(self):                                      # pragma: no cover
        return "Item(%s, %r)" % (self.kind, self.text[:40])


def _fresh(node, since_ms: int) -> bool:
    return int(getattr(node, "last_active_t_ms", 0) or 0) >= since_ms


def pick(graph_store, since_ms: int, limit: int = MAX_ITEMS) -> List[Item]:
    """At most `limit` items worth reading, or none at all."""
    from tracemem.app.digest import _citations
    from tracemem.domain.node import NodeKind
    from tracemem.nodes.accrual import label_of

    try:
        nodes = list(graph_store.nodes.values())
    except Exception:                                        # noqa: BLE001
        return []

    questions, patterns, surprises = [], [], []
    for n in nodes:
        if not _fresh(n, since_ms):
            continue
        text = label_of(n)
        if not text:
            continue
        if not _about_a_person_not_an_index(text):
            # See _MACHINE_SHAPED. Nobody asked for this item, so the bar is
            # higher than "technically true".
            continue
        cites = _citations(n)
        if not cites:
            # NO RECEIPT, NO MORNING. Worse unprompted than in an answer:
            # nobody asked, so nobody is checking.
            continue
        # AN OBSERVATION WITH A NAME IS NOT A FINDING.
        #
        # digest.py already states this rule for its belief section — "the
        # entity tier is not a belief, it is an observation with a name" —
        # and this module was breaking it. MEASURED on the real graph, the
        # surprise tier produced exactly this, unprompted:
        #
        #   <a site>      (this stood out more than it expected)
        #   <a site>      (this stood out more than it expected)
        #   <an app>      (this stood out more than it expected)
        #
        # Three of the owner's most-used sites and apps, announced as
        # insights (the names are withheld here on purpose). A
        # noun is not news. Worse, an unprompted list of most-visited domains
        # is a thing a person might very much not want their machine
        # volunteering, and it says nothing they did not already know.
        #
        # So a morning item must be DERIVED — something the night concluded,
        # not something the eye merely saw a lot of.
        if int(getattr(n, "derivation_depth", 0) or 0) < 1:
            continue
        kind = getattr(n, "kind", None)
        if kind is NodeKind.QUESTION:
            questions.append(Item("question", text, cites, n.id,
                                  "it noticed something it cannot settle"))
        elif kind is NodeKind.PATTERN:
            if len(getattr(n, "parent_ids", ()) or ()) >= MIN_PATTERN_PARENTS:
                patterns.append(Item("pattern", text, cites, n.id,
                                     "it saw this enough times to name it"))
        elif float(getattr(n, "salience_ema", 0.0) or 0.0) >= MIN_SALIENCE:
            surprises.append(Item("surprise", text, cites, n.id,
                                  "this stood out more than it expected"))

    surprises.sort(key=lambda i: -_salience(nodes, i.node_id))
    out = (questions[:limit] + patterns[:limit] + surprises[:limit])[:limit]
    return out


def _salience(nodes, node_id) -> float:
    for n in nodes:
        if n.id == node_id:
            return float(getattr(n, "salience_ema", 0.0) or 0.0)
    return 0.0


def render(items: List[Item]) -> Optional[str]:
    """The morning, or None when there is nothing to say.

    None rather than an empty string, so a caller cannot accidentally show a
    blank panel that looks like a failure. Nothing to say means nothing is
    shown at all.
    """
    if not items:
        return None
    out = ["YOU DIDN'T ASK, BUT", "=" * 40]
    for i in items:
        out.append("")
        out.append("  %s" % i.text)
        out.append("     (%s)  [%s]"
                   % (i.why, ",".join(str(c)[:8] for c in i.citations[:2])))
    out.append("")
    out.append("-" * 40)
    out.append("Wrong? Ask about it and say so — it is evidence, not a knob.")
    return "\n".join(out)


def morning(graph_store, since_ms: int, limit: int = MAX_ITEMS) -> Optional[str]:
    return render(pick(graph_store, since_ms, limit))
