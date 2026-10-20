"""OWNS: preference derivation — understanding the person through their
VOLITIONAL actions (analysis gap: understand-through-actions; the founder's
'we're trying to understand the person through their actions').

The honest form of 'you like croissants': not a single choice (counterfactuals
are rarely observable) but the statistical signature of REPEATED VOLITIONAL
engagement. Two guards keep it from lying:
  · VOLITION — an act the user INITIATED counts; one that ARRIVED (a
    notification, an assigned task, an ad) does not. This is the 'loves inbox'
    guard: opening work mail 100× is obligation, not preference.
  · CORRIGIBILITY — a preference is a HYPOTHESIS: graded, cited to the very
    moments of choice, born warm, and pruned/decayed like any dream if the
    engagement stops. It is never asserted as fact.

Volition is read from the extractor's structured signal (`claim.structured
['initiated']`); absent today means UNKNOWN, and unknown never counts as
choice — the derivation is silent rather than guessing.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from tracemem import __version__
from tracemem.domain import Confidence, Grade, Node, NodeKind, node_id
from tracemem.domain.node import Attribute
from tracemem.nodes.accrual import episodes_of
from tracemem.nodes.marination.semantic import label_of

MIN_CHOICES = 3          # a preference needs a signature, not an anecdote
VOLITION_RATIO = 0.5     # more chosen than imposed, or it is not a preference
_TARGET_KINDS = (NodeKind.OBJECT, NodeKind.TOPIC, NodeKind.SITE)


def is_volitional(claim) -> Optional[bool]:
    """True = the user initiated it (chose), False = it arrived (imposed),
    None = unknown. Unknown is not choice."""
    structured = getattr(claim, "structured", None)
    if not structured:
        return None
    v = structured.get("initiated")
    return v if isinstance(v, bool) else None


def _episode_volition(claims_by_episode) -> Dict[str, bool]:
    """episode -> True if it contains an initiated act, False if it contains an
    imposed one and no initiated one; absent if unknown."""
    out: Dict[str, bool] = {}
    for ep_id, claims in claims_by_episode.items():
        vals = [is_volitional(c) for c in claims]
        if any(v is True for v in vals):
            out[ep_id] = True
        elif any(v is False for v in vals):
            out[ep_id] = False
    return out


def preference_pass(graph_store, claims_by_episode, t_ms: int,
                    min_choices: int = MIN_CHOICES) -> List[str]:
    """Mint corrigible 'prefers X' hypotheses. Returns their ids."""
    ep_volition = _episode_volition(claims_by_episode or {})
    if not ep_volition:
        return []                                  # no volition data → say nothing

    minted: List[str] = []
    for node in list(graph_store.nodes.values()):
        if node.kind not in _TARGET_KINDS:
            continue
        eps = episodes_of(node)
        chosen = sum(1 for e in eps if ep_volition.get(e) is True)
        imposed = sum(1 for e in eps if ep_volition.get(e) is False)
        known = chosen + imposed
        if chosen < min_choices or known == 0:
            continue
        if chosen / known < VOLITION_RATIO:
            continue                               # mostly imposed — the inbox guard

        label = label_of(node) or node.id
        sid = node_id("prefers", label)
        if graph_store.get_node(sid) is not None:
            continue                               # idempotent per target

        parent_grade = max((a.grade for a in node.attributes), default=Grade.GIST)
        grade = Grade(max(int(Grade.FLOOR), int(parent_grade) - 1))   # derived: one down
        cites = tuple(sorted(e for e in eps if ep_volition.get(e) is True))
        pref = Node(
            id=sid, kind=NodeKind.HYPOTHESIS,
            derivation_depth=node.derivation_depth + 1,
            parent_ids=(node.id,), builder_version=__version__,
            attributes=[Attribute(
                key="claim",
                value="you seem to prefer %s (chosen in %d moments)" % (label, chosen),
                grade=grade,
                confidence=Confidence(min(0.9, 0.4 + 0.1 * chosen)),
                source_episode_ids=cites, t_ms=t_ms)])
        pref.activation = 0.6                       # born warm (avoid eat-your-newborns)
        pref.last_active_t_ms = t_ms
        graph_store.upsert_node(pref)
        minted.append(sid)
    return minted
