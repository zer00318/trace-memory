"""OWNS: recurrence detection — pure counting, ZERO LLM (spec 04 §4).

A value recurring across >= MIN_EPISODES distinct episodes on a node's
attribute set is a pattern. Cluster-wide: the node plus its 1-hop neighbors.
"""
from __future__ import annotations

from collections import defaultdict
from typing import List

from tracemem.nodes.combination.proposal import NONE, Proposal

MIN_EPISODES = 3

# HOW A PATTERN SAYS ITSELF — one table, in English.
#
# This module used to render every pattern as `recurring: %s=%s observed in
# %d distinct moments`, printing the raw attribute key and value. MEASURED
# 2026-08-25 on the founder's graph, that is what the whole belief tier looks
# like:
#
#   recurring: page=/watch observed in 230 distinct moments
#   recurring: page=/ observed in 13 distinct moments
#
# True, and useless. It is a database row read aloud — the memory describing
# its own index rather than anything about a life. It is also what made the
# morning surface unusable, and it is the reason a person reading their own
# beliefs would not recognise a single one of them.
#
# SOME KEYS CANNOT BE SAID AT ALL, and phrasing does not save them. `page`
# holds URL path fragments; the founder's real values are '/tou', '/reel/DY'
# and '/reel/DYQ5NmxN' — the last two being OCR-truncated copies of one URL.
# A pattern over those is noise no matter how it is worded, so no pattern is
# minted. That is the same rule the birth layer already applies to grammars
# fed by a sense that cannot support them: the detector is not wrong, the key
# is unsayable.
# PLACE KEYS THAT ARE NOT PLACES.
#
# `binder/cross_pillar.place_key` returns "dev:<device>" at 0.0 confidence
# when no SSID has been vouched for and there is no GPS — the honest answer
# for "this machine, somewhere". It is a correct value and it is not a PLACE
# a person has ever been.
#
# WITNESSED 2026-08-26 on the real graph, in the morning surface, unprompted:
#
#     you are often at system — in 72 moments
#
# That is the memory telling someone where they spend their life and naming
# an internal token. It is true, useless, and slightly unsettling — the exact
# register a product like this cannot afford. `around 07:00 you are usually
# at dev:mac-1` has the same problem one layer down.
#
# The rule is the same one that keeps `page=` out: a value that cannot be
# said mints nothing. When the founder names home and office, or the phone
# pillar supplies GPS, real place keys appear and this turns itself off with
# no code change.
_UNSAYABLE_PLACES = ("system", "unknown", "wifi:unknown")


def _is_a_real_place(value: str) -> bool:
    low = str(value or "").strip().lower()
    return bool(low) and low not in _UNSAYABLE_PLACES and not low.startswith("dev:")


_SAYABLE = {
    "site": "you keep going back to %s",
    "app": "you keep using %s",
    "seen_as": "%s keeps coming up",
    "place": "you are often at %s",
    "routine": "%s",          # schemas.py already writes these as sentences
}


def detect(node, graph_store, min_episodes: int = MIN_EPISODES) -> List[Proposal]:
    # depth-0 only: a meta node's "claim" attribute recurring across its OWN
    # source episodes is not new evidence — counting it would mint
    # patterns-of-patterns from the same facts every night (witnessed runaway).
    cluster = [n for n in [node] + list(graph_store.neighbors(node.id, hops=1))
               if n.derivation_depth == 0]
    by_value = defaultdict(set)          # (key, value) -> distinct episode ids
    owners = defaultdict(set)            # (key, value) -> contributing node ids
    for n in cluster:
        for a in n.attributes:
            by_value[(a.key, a.value)].update(a.source_episode_ids)
            owners[(a.key, a.value)].add(n.id)
    out: List[Proposal] = []
    for (key, value), episodes in by_value.items():
        template = _SAYABLE.get(key)
        if template is None:
            # See _SAYABLE. An unsayable key mints nothing rather than a
            # database row worded as a belief.
            continue
        if key == "place" and not _is_a_real_place(value):
            # See _UNSAYABLE_PLACES. "You are often at system" is true,
            # useless, and not a sentence about anyone's life.
            continue
        if len(episodes) >= min_episodes:
            said = template % value
            out.append(Proposal(
                kind="pattern",
                claim_text="%s — in %d moments" % (said, len(episodes)),
                parent_node_ids=tuple(sorted(owners[(key, value)])),
                supported_by=tuple(sorted(episodes)),
                extras={"count": len(episodes), "key": key, "value": value}))
    return out or [NONE]
