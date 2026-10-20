"""Everything the graph knows about ONE thing, assembled and cited.

THE FAILURE THIS FIXES, measured on the live daemon 2026-08-23:

    $ ./trace "who is Juno?"
      (not in memory)

Juno is a node. She was judged a person by the model, seen in seven
episodes, wired to Ben and lichess.org, and three derived beliefs name her.
The memory held all of it and answered "not in memory", because the packer
renders a node as:

    [ENTITY 1a2b3c4d] seen_as=Juno

and the answering prompt — correctly, for a needle question — refuses
anything a block does not DIRECTLY state. A name restated is not an answer to
"who is this", so the model abstained over a context that contained the
question and none of the answer.

Nothing was missing from the memory. The rendering was missing from the
supplier. A node's VALUE is not its label, it is everything attached to it:
how often, over what span, next to what, and in whose words.

SAME SHAPE AS `window.py`, deliberately. A question about a period gets the
claims in that period; a question about a thing gets what is attached to that
thing. Both bypass the cosine-over-labels retrieval that can only rank names,
and both hand the model something it can honestly summarise instead of
something it must honestly refuse.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from tracemem.domain import EdgeType
from tracemem.nodes.accrual import episodes_of, label_of, names_of, sightings

# Words that name nothing and would match half the graph if a question
# happened to contain them.
_STOP = frozenset("""
a an the is are was were do does did i me my mine you your we us our it its
what which who whom whose when where why how about of on in at to from for
with and or but not no yes most often usually much many more last this that
these those been being have has had can could would should will shall
""".split())

# How much of the surrounding evidence to quote per node. Enough to recognise
# what was going on, small enough that several subjects still fit a budget.
EVIDENCE_PER_SUBJECT = 5
MAX_EVIDENCE_CHARS = 160
MAX_NEIGHBOURS = 8
MAX_BELIEFS = 3

# THE SCHEMA IS NOT KNOWLEDGE. `NodeKind.ENTITY` is a database type; rendering
# it as "Juno — an entity" put the system's own vocabulary into the
# evidence as though it were something observed, the model restated it
# ("Juno is an entity"), and the never-lie checker objected — correctly,
# and DETERMINISTICALLY: three checker runs named that exact sentence as the
# unsupported one, which is why "who is Juno?" kept falling back to raw
# evidence.
#
# A person is a person. The kinds that ARE informative to a human keep their
# word, because "a site" and "an app" tell a reader what kind of thing they
# are looking at.
# One table for the whole product — see domain/node.human_kind.
from tracemem.domain.node import human_kind as _kind_word

# Co-occurrence hypotheses ("X and Y: both repeatedly present with Z") are
# the cheapest thing the night can derive and the least worth reading. On the
# live dossier for Juno they filled the answer:
#
#   "The night concluded with Juno and window_stack api repeatedly
#    present with youtube.com... and recall... and www.google.com..."
#
# All true, none informative, and they crowded out the part a person wanted
# — that she was seen in three moments alongside Adele and lichess.org.
# Ranked LAST rather than dropped: they are real derivations and a dossier
# with nothing else to say should still say them.
_WEAK_BELIEF = "both repeatedly present with"


def _belief_rank(text: str) -> int:
    return 1 if _WEAK_BELIEF in text else 0


@dataclass
class Dossier:
    node: object
    label: str
    kind: str
    sightings: int
    first_t_ms: int
    last_t_ms: int
    attributes: List[Tuple[str, str]] = field(default_factory=list)
    neighbours: List[Tuple[str, str, float]] = field(default_factory=list)
    beliefs: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    cite: str = ""


def _tokens(text: str) -> List[str]:
    return [w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9.'-]*", text.lower())
            if w not in _STOP and len(w) > 2]


def _labels_of(node) -> List[str]:
    out = [label_of(node)]
    out.extend(names_of(node))
    for a in node.attributes:
        if a.key in ("site", "app", "place", "topic", "object", "seen_as"):
            out.append(str(a.value))
    return [x for x in {str(o).strip() for o in out} if x]


def find_subjects(question: str, graph_store, limit: int = 3) -> List[object]:
    """Nodes the question actually names.

    Matched on WORDS, not substrings: "who is Eli" must not also drag in
    every node whose label contains "kim". Depth-0 only — a question names a
    thing, and the beliefs ABOUT that thing are gathered by `build`.
    """
    wanted = set(_tokens(question))
    if not wanted:
        return []
    scored = []
    for node in graph_store.nodes.values():
        if getattr(node, "derivation_depth", 0) != 0:
            continue
        for lab in _labels_of(node):
            lab_tokens = set(_tokens(lab))
            if lab_tokens and lab_tokens <= wanted:
                scored.append((len(lab_tokens), sightings(node), node))
                break
    scored.sort(key=lambda t: (-t[0], -t[1]))
    return [n for _l, _s, n in scored[:limit]]


def _evidence_from_log(node, claim_log, label: str) -> List[str]:
    """Lines that were on screen around this node's own timestamps.

    The in-memory `claims_by_episode` map only holds what the CURRENT process
    bound. Since boot became O(new claims) — which it had to, an all-claims
    bind took two hours and blocked the eye — that map is nearly empty on a
    fresh daemon, so a dossier had attributes and neighbours but never a
    quote. This reads the claim log directly around the node's attribute
    times, which is what `ix_claim_time` is for.
    """
    if claim_log is None:
        return []
    window = 90_000
    out, seen = [], set()
    times = sorted({a.t_ms for a in node.attributes if a.t_ms})[:6]
    low = label.lower()
    for t in times:
        try:
            claims = claim_log.read_range(t - window, t + window)
        except Exception:                                     # noqa: BLE001
            return out
        for c in claims:
            text = " ".join(str(c.text).split())
            if len(text) < 12 or low not in text.lower() or text in seen:
                continue
            seen.add(text)
            out.append(text[:MAX_EVIDENCE_CHARS])
            if len(out) >= EVIDENCE_PER_SUBJECT:
                return out
    return out


def build(node, graph_store, claims_by_episode: Optional[Dict] = None,
          claim_log=None) -> Dossier:
    eps = episodes_of(node)
    times = [a.t_ms for a in node.attributes if a.t_ms]
    d = Dossier(
        node=node,
        label=label_of(node) or node.id[:8],
        kind=node.kind.value,
        sightings=len(eps),
        first_t_ms=min(times) if times else 0,
        last_t_ms=max(times) if times else 0,
        cite=node.id[:8],
    )
    seen_attr = set()
    for a in node.attributes:
        pair = (a.key, str(a.value))
        if pair not in seen_attr:
            seen_attr.add(pair)
            d.attributes.append(pair)

    # WHO IT SITS NEXT TO, strongest first. Co-occurrence is the graph's own
    # answer to "what is this thing part of", and it is the half a label can
    # never carry.
    # UBIQUITOUS NODES ARE NOT COMPANY. `dev:mac-1` is in 76.8% of episodes
    # and `Brave Browser` in 76.2% — being "alongside" them says nothing, and
    # the combination tier already refuses to reason about them
    # (candidate_selector.ubiquitous). A dossier that lists them is telling a
    # person their friend was seen near their own computer.
    from tracemem.nodes.combination.candidate_selector import ubiquitous

    everywhere = ubiquitous(graph_store)
    edges = sorted(graph_store.edges_of(node.id),
                   key=lambda e: -e.weight)[:MAX_NEIGHBOURS * 4]
    seen_labels = {d.label.lower()}
    for e in edges:
        other_id = e.dst if e.src == node.id else e.src
        if other_id in everywhere:
            continue
        other = graph_store.get_node(other_id)
        if other is None or getattr(other, "derivation_depth", 0) != 0:
            continue
        lab = label_of(other)
        # Deduplicated: two nodes can carry the same label (an OCR variant
        # and the real one), and a dossier reading "alongside dev:mac-1,
        # dev:mac-1" is the memory stuttering.
        # "a person" is the ANONYMOUS placeholder, not a name. Listing it as
        # company — "Hana was seen alongside a person" — says nothing and
        # reads as though the memory knows a second individual it cannot name.
        if lab and lab.lower() not in seen_labels and lab.lower() != "a person":
            seen_labels.add(lab.lower())
            d.neighbours.append((lab, e.type.value, e.weight))
        if len(d.neighbours) >= MAX_NEIGHBOURS:
            break

    # WHAT THE NIGHT CONCLUDED about it — the derived tier, which is the
    # whole point of having one. Strongest kinds first; see _belief_rank.
    found: List[str] = []
    for other in graph_store.nodes.values():
        if getattr(other, "derivation_depth", 0) < 1:
            continue
        if node.id in tuple(getattr(other, "parent_ids", ())):
            text = label_of(other)
            if text:
                found.append(text)
    found.sort(key=_belief_rank)
    d.beliefs = found[:MAX_BELIEFS]

    # IN WHOSE WORDS. The claims that produced it, so an answer can quote the
    # screen rather than paraphrase a label.
    if claims_by_episode:
        seen = set()
        for ep in list(eps)[:12]:
            for c in claims_by_episode.get(ep, ())[:40]:
                text = " ".join(str(c.text).split())
                if len(text) < 12 or text in seen:
                    continue
                if d.label.lower() not in text.lower():
                    continue
                seen.add(text)
                d.evidence.append(text[:MAX_EVIDENCE_CHARS])
                if len(d.evidence) >= EVIDENCE_PER_SUBJECT:
                    break
            if len(d.evidence) >= EVIDENCE_PER_SUBJECT:
                break
    if not d.evidence:
        d.evidence = _evidence_from_log(node, claim_log, d.label)
    return d


def render(dossiers: Sequence[Dossier], clock_fn=None) -> str:
    import time as _t
    clock_fn = clock_fn or (lambda ms: _t.strftime("%Y-%m-%d",
                                                  _t.localtime(ms / 1000.0)))
    out = []
    for d in dossiers:
        span = ""
        if d.first_t_ms and d.last_t_ms:
            first, last = clock_fn(d.first_t_ms), clock_fn(d.last_t_ms)
            span = (" between %s and %s" % (first, last) if first != last
                    else " on %s" % first)
        word = _kind_word(d.kind)
        article = "an" if word[:1] in "aeiou" else "a"
        out.append("[%s] %s — %s %s, seen in %d moment(s)%s"
                   % (d.cite, d.label, article, word, d.sightings, span))
        known = [("%s=%s" % (k, v)) for k, v in d.attributes
                 if v and v != d.label]
        if known:
            out.append("        known: %s" % "; ".join(known[:8]))
        if d.neighbours:
            out.append("        alongside: %s"
                       % ", ".join("%s" % lab for lab, _t2, _w in d.neighbours))
        for b in d.beliefs:
            out.append("        the night concluded: %s" % b)
        for e in d.evidence:
            out.append("        on screen: %s" % e)
    return "\n".join(out)


def supply_subject(question: str, graph_store, budget_tokens: int,
                   claims_by_episode: Optional[Dict] = None,
                   tokenizer=None, claim_log=None):
    """The question's subjects → a packed, cited Context, or None.

    None when the question names nothing the graph holds — the caller then
    falls through to ordinary retrieval, because "this is not my question"
    and "I have nothing for your question" are different and only the second
    is an answer.
    """
    from tracemem.domain.grading import grade_for
    from tracemem.supplier.packer import Context, CtxBlock

    tokenizer = tokenizer or (lambda s: max(1, len(s) // 4))
    subjects = find_subjects(question, graph_store)
    if not subjects:
        return None
    blocks, spent = [], 0
    for node in subjects:
        d = build(node, graph_store, claims_by_episode, claim_log=claim_log)
        text = render([d])
        cost = tokenizer(text)
        if spent + cost > budget_tokens:
            break
        blocks.append(CtxBlock(
            kind="dossier", text=text,
            # The dossier's grade is the strongest witness among the
            # attributes it is built from — never a literal.
            grade=max((a.grade for a in node.attributes),
                      default=grade_for("gist")),
            source_ids=(d.cite,), t_ms=d.last_t_ms))
        spent += cost
    if not blocks:
        return None
    return Context(tuple(blocks), spent)
