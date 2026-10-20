"""OWNS: mention → node identity across time, with honest confidence
(spec 04 birth, rebuilt v2 — the identity engine).

The four laws of identity here, each fixing a real corruption class:

1. PERSON NODES ARE INSTANCES, NOT NAMES. A person's node id is minted from
   (surface, first-episode) — never from the name alone — so two different
   Sams are two nodes forever. Resolution chooses BETWEEN candidates by
   context; it never assumes a name is unique. (The old natural-key scheme
   made every Sam on Earth one node: silent lifelong corruption.)

2. ANONYMOUS PEOPLE ARE PER-CONTEXT, NOT GLOBAL. "a person" births or
   rejoins an anonymous node only through shared context (same places,
   same co-occurrences). One global anonymous-person bucket would blend
   every stranger into a single ghost. Better a thousand small ghosts that
   questions can later name.

3. UNCERTAINTY NEVER MERGES. Below the confidence bar the resolver births a
   new node and lets question_gen ask — a wrong split costs a duplicate
   (repairable: SAME_AS is meta); a wrong merge poisons every future
   sighting (unrepairable in place). Splits are cheap; merges are forever.

4. DETERMINISTIC KINDS STAY DETERMINISTIC. Apps, sites, places, topics,
   objects resolve by natural key — their identity IS their name. Only
   humans get the expensive machinery, because only humans deserve it.

Context = the episode's already-resolved deterministic node ids (places,
apps, sites, topics). Person resolution runs AFTER those, so every person
mention arrives with a context fingerprint to match against candidates'
co-occurrence neighborhoods.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Set, Tuple

from tracemem.domain import Node, NodeKind, node_id
from tracemem.nodes.accrual import names_of

_KIND_MAP = {"app": NodeKind.APP, "site": NodeKind.SITE, "place": NodeKind.PLACE,
             "topic": NodeKind.TOPIC, "object": NodeKind.OBJECT}

RESOLVE_BAR = 0.6          # below this: new node + a question, never a merge
AMBIGUITY_MARGIN = 0.12    # two candidates this close = genuinely ambiguous


@dataclass(frozen=True)
class Resolution:
    node: Optional[Node]              # None = DEFERRED: attach nothing, ask instead
    confidence: float
    is_new: bool
    mention: object = None
    candidate_ids: Tuple[str, ...] = ()   # the fork a deferred question must settle

    @property
    def runner_up(self) -> Optional[str]:
        return self.candidate_ids[1] if len(self.candidate_ids) > 1 else None


# ---------------------------------------------------------------- helpers

def _name_match(surface: str, node: Node) -> bool:
    s = surface.lower()
    for name in names_of(node):
        n = name.lower()
        if s == n or (len(s) >= 3 and (n.startswith(s) or s.startswith(n))):
            return True                      # Sam / Samuel / Sammy
    return False


def _neighbor_ids(graph, nid: str) -> Set[str]:
    out = set()
    for e in graph.edges_of(nid):
        out.add(e.dst if e.src == nid else e.src)
    return out


def _discrimination(graph, nid: str) -> float:
    """A context node's identity signal is inverse to its ubiquity: the app
    you use for everything and the wifi you always sit on co-occur with the
    whole graph and therefore identify nobody (IDF for the social graph)."""
    return 1.0 / (1.0 + len(_neighbor_ids(graph, nid)))


def _context_overlap(graph, cand: Node, ctx_ids: Set[str]) -> float:
    if not ctx_ids:
        return 0.0
    hood = _neighbor_ids(graph, cand.id)
    if not hood:
        return 0.0
    total = sum(_discrimination(graph, n) for n in ctx_ids)
    if total <= 0:
        return 0.0
    shared = sum(_discrimination(graph, n) for n in hood & ctx_ids)
    return shared / total


def _person_candidates(graph) -> List[Node]:
    return [n for n in graph.nodes.values()
            if n.kind == NodeKind.ENTITY and n.derivation_depth == 0]


# ---------------------------------------------------------------- resolve

def resolve(mention, graph, ctx_ids: Optional[Iterable[str]] = None,
            ) -> Resolution:
    ctx: Set[str] = set(ctx_ids or ())

    if mention.kind != "person":
        nid = node_id(mention.kind, mention.surface.lower())
        existing = graph.get_node(nid)
        if existing is not None:
            return Resolution(existing, 0.95, False, mention)
        return Resolution(Node(id=nid, kind=_KIND_MAP[mention.kind]),
                          0.85, True, mention)

    if mention.anonymous:
        return _resolve_anonymous(mention, graph, ctx)
    return _resolve_named(mention, graph, ctx)


def _new_person(mention, confidence: float) -> Resolution:
    fresh = Node(id=node_id("person", "%s#%s" % (mention.surface.lower(),
                                                 mention.episode_id)),
                 kind=NodeKind.ENTITY)
    return Resolution(fresh, confidence, True, mention)


def _resolve_named(mention, graph, ctx: Set[str]) -> Resolution:
    scored: List[Tuple[float, Node]] = []
    for cand in _person_candidates(graph):
        if not _name_match(mention.surface, cand):
            continue
        hood = _neighbor_ids(graph, cand.id)
        if not ctx or not hood:
            score = 0.65                  # no evidence either way: lean attach
        else:
            overlap = _context_overlap(graph, cand, ctx)
            # zero overlap against an ESTABLISHED context is negative evidence:
            # this is probably a different Sam (law 1).
            score = 0.45 if overlap == 0.0 else 0.65 + 0.35 * overlap
        scored.append((score, cand))

    if not scored:
        return _new_person(mention, 0.75)

    scored.sort(key=lambda t: (t[0], t[1].id), reverse=True)
    best_score, best = scored[0]

    if best_score >= RESOLVE_BAR:
        if len(scored) > 1 and best_score - scored[1][0] < AMBIGUITY_MARGIN:
            # two plausible owners: law 3 — attach NOTHING, let the answer settle it
            return Resolution(None, 0.45, False, mention,
                              candidate_ids=(best.id, scored[1][1].id))
        return Resolution(best, min(0.95, best_score), False, mention)

    if len(scored) > 1:
        # several known Sams, none of them plausibly this one: minting a third
        # on no evidence would breed dupes — defer to a question instead.
        return Resolution(None, 0.45, False, mention,
                          candidate_ids=tuple(c.id for _, c in scored[:2]))
    # exactly one known Sam, and the context says stranger: a new instance.
    # A split is cheap (SAME_AS repair is meta); a merge is forever.
    return _new_person(mention, 0.7)


def _resolve_anonymous(mention, graph, ctx: Set[str]) -> Resolution:
    # rejoin a KNOWN ghost only through context (law 2)
    best, best_overlap = None, 0.0
    for cand in _person_candidates(graph):
        if names_of(cand):
            continue                          # named people are not ghosts
        overlap = _context_overlap(graph, cand, ctx)
        if overlap > best_overlap:
            best, best_overlap = cand, overlap
    if best is not None and best_overlap >= 0.3:
        return Resolution(best, 0.5, False, mention)   # below bar → question fires

    fresh = Node(id=node_id("person", "anon#%s" % mention.episode_id),
                 kind=NodeKind.ENTITY)
    return Resolution(fresh, 0.4, True, mention)
