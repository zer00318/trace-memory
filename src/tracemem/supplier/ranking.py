"""How often — the question a graph of names cannot answer without counting.

    $ ./trace "what websites do I use the most?"
      (not in memory)

There are 130 site nodes. The memory knows every one of them and how many
moments each appeared in, and it answered "not in memory", because retrieval
ranks nodes by COSINE SIMILARITY TO THE QUESTION and then renders each as a
label. "Most" is not a similarity; it is an order over counts, and nothing in
the supplier had ever counted anything.

WHY UBIQUITY IS **NOT** APPLIED HERE, and this is the whole point of the file.
`candidate_selector.ubiquitous` keeps `Brave Browser` (76.2% of episodes) out
of the combination tier, because "both repeatedly present with X" is empty
when X is present with everything. But when the question IS "what do I use
most", Brave Browser is not noise — it is THE ANSWER. The same fact
disqualifies a node as the subject of a derived belief and qualifies it as
the answer to a frequency question. A gate copied from one place to the other
would delete the answer.

WHAT THE NUMBER MEANS, said in the render rather than left to be assumed: a
"moment" is an episode the thing appeared in, and episodes are bounded at
thirty minutes. So this counts OCCASIONS, not minutes. A site open in a
background tab all day scores lower than one revisited across the day, which
is usually what a person means by "use the most" — but it is a proxy, and
calling it "hours" would be a lie the data cannot support.
"""
from __future__ import annotations

import re
from typing import List, Optional, Sequence, Tuple

from tracemem.domain import NodeKind
from tracemem.domain.node import human_kind
from tracemem.nodes.accrual import episodes_of, label_of

# Asking for an ORDER, not a fact.
_AGGREGATE_RE = re.compile(
    r"\b(most|mostly|often|oftenest|usually|frequently|frequent|top|main|"
    r"commonly|typically|favourite|favorite|how many|how much|how often|"
    r"spend|rank|ranked|list)\b", re.I)

# ...over a KIND of thing.
_KINDS = (
    (re.compile(r"\b(websites?|sites?|urls?|domains?|pages?)\b", re.I),
     NodeKind.SITE),
    (re.compile(r"\b(apps?|applications?|programs?|software)\b", re.I),
     NodeKind.APP),
    (re.compile(r"\b(people|persons?|who)\b", re.I), NodeKind.ENTITY),
    (re.compile(r"\b(places?|locations?)\b", re.I), NodeKind.PLACE),
    (re.compile(r"\b(topics?|subjects?)\b", re.I), NodeKind.TOPIC),
    (re.compile(r"\b(objects?|things?)\b", re.I), NodeKind.OBJECT),
)

DEFAULT_LIMIT = 12


def _fold_key(kind: NodeKind, label: str) -> str:
    """What counts as the SAME thing for the purpose of counting occasions.

    Measured on the live ranking: `youtube.com` scored 242 moments and
    `www.youtube.com` 68, listed as first and second — one habit presented as
    two, with the total understated in both rows. A person asking what they
    use most is not asking about hostname prefixes.

    Only `www.` is folded, deliberately. `m.` and `mail.` and `docs.` are
    genuinely different surfaces of a service and folding them would answer a
    question nobody asked.
    """
    key = label.strip().lower()
    if kind is NodeKind.SITE and key.startswith("www."):
        key = key[4:]
    return key


def wants_ranking(question: str) -> Optional[NodeKind]:
    """The kind being ranked, or None if this is not that question."""
    if not _AGGREGATE_RE.search(question):
        return None
    for pattern, kind in _KINDS:
        if pattern.search(question):
            return kind
    return None


def _rank_entries(graph_store, kind: NodeKind, limit: int = DEFAULT_LIMIT):
    rows = []
    for node in graph_store.nodes.values():
        if node.kind is not kind or getattr(node, "derivation_depth", 0) != 0:
            continue
        lab = label_of(node)
        if not lab or lab.lower() == "a person":
            continue
        rows.append((lab, len(episodes_of(node)),
                     max((a.t_ms for a in node.attributes), default=0),
                     node.id))
    # Fold labels that are the same thing seen twice (an OCR variant beside
    # the real one): a ranking that lists `youtube.com` and `youtube.co`
    # separately is counting one habit as two.
    folded = {}
    for lab, n, t, nid in rows:
        key = _fold_key(kind, lab)
        prev = folded.get(key)
        # Keep the SHORTER label as the name — "youtube.com" reads better
        # than "www.youtube.com" and is what the person would say.
        name = lab if prev is None else min(lab, prev[0], key=len)
        ids = ((prev[3] if prev else ()) + (nid,))
        folded[key] = (name, n + (prev[1] if prev else 0),
                       max(t, prev[2] if prev else 0), ids)
    out = sorted(folded.values(), key=lambda r: (-r[1], r[0]))[:limit]
    return out


def rank(graph_store, kind: NodeKind,
         limit: int = DEFAULT_LIMIT) -> List[Tuple[str, int, int]]:
    """[(label, moments, last_seen_t_ms)] for `kind`, commonest first."""
    return [(lab, n, t) for lab, n, t, _ids in
            _rank_entries(graph_store, kind, limit)]


def render(kind: NodeKind, rows: Sequence[Tuple[str, int, int]],
           clock_fn=None, citation_ids=None) -> str:
    import time as _t
    clock_fn = clock_fn or (lambda ms: _t.strftime("%Y-%m-%d",
                                                   _t.localtime(ms / 1000.0)))
    head = ("%s, ordered by how many separate moments each appeared in "
            "(a moment is an episode, capped at 30 minutes — this counts "
            "OCCASIONS, not minutes):" % human_kind(kind).upper())
    lines = [head]
    for i, (lab, n, t) in enumerate(rows, 1):
        when = (" last on %s" % clock_fn(t)) if t else ""
        cite = ""
        if citation_ids is not None and i - 1 < len(citation_ids):
            cite = " [%s]" % ",".join(citation_ids[i - 1])
        lines.append("  %2d. %s — %d moment(s)%s%s" %
                     (i, lab, n, when, cite))
    return "\n".join(lines)


def supply_ranking(question: str, graph_store, budget_tokens: int,
                   tokenizer=None):
    """A ranked Context, or None if the question is not asking for an order."""
    from tracemem.domain.grading import grade_for
    from tracemem.supplier.packer import Context, CtxBlock

    kind = wants_ranking(question)
    if kind is None:
        return None
    entries = _rank_entries(graph_store, kind)
    if not entries:
        return None
    rows = [(lab, n, t) for lab, n, t, _ids in entries]
    citations = [tuple(str(nid)[:8] for nid in ids)
                 for _lab, _n, _t, ids in entries]
    tokenizer = tokenizer or (lambda s: max(1, len(s) // 4))
    text = render(kind, rows, citation_ids=citations)
    # Graded as marination: the counts are DERIVED from the entity tier by
    # this function, not read off a screen by a perceiver.
    sources = tuple(dict.fromkeys(c for row in citations for c in row))
    block = CtxBlock(kind="ranking", text=text, grade=grade_for("marination"),
                     source_ids=sources, t_ms=max(t for _l, _n, t in rows))
    return Context((block,), tokenizer(text))
