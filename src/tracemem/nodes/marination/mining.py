"""OWNS: THE RE-MARINATION LOOP — the napkin's actual marination, missing
until now: agenda-driven re-reading of RAW claims a node already touched,
extracting what the first pass could not know to look for.

Why this is the load-bearing novelty: on day 1 the system saw "a person at
the kitchen table" and could only birth a ghost. On day 4 someone SAID
"this is my neighbor Priya" — a claim that meant nothing to day-1 extraction
because no ghost existed yet to be hungry for it. Mining runs the other
direction: the ghost's hunger ("who is this?") sends the night BACK into the
raw claims of the ghost's own episodes, with grammars aimed by what is being
asked. Raw is kept precisely so this pass exists (law #9's purpose, cashed).

Economy: a node's mining priority = its activation × its recurrence — the
cells that are alive and persistent eat first. Deterministic grammars are
free; the reasoner is spent only when they fail and budget allows.
Idempotent per (node, key, evidence): unchanged raw is never re-chewed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple

from tracemem.domain import Grade, Node
from tracemem.domain.activation import activation_now
from tracemem.nodes.accrual import accrue, agenda_keys, episodes_of, sightings
from tracemem.nodes.birth.entity_extractor import _NAME_STOPLIST

_INTRO_RE = re.compile(
    r"\b(?:this is|meet|i'?m|i am|say hi to)\s+(?:my\s+([a-z]+)\s+)?"
    r"([A-Z][a-z]{2,15})\b")
_COLORS = ("red", "blue", "green", "black", "white", "grey", "gray", "brown",
           "yellow", "orange", "purple", "silver")

_KEY_WEIGHT = {"seen_as": 1.0, "relationship": 0.8, "color": 0.5,
               "located_at": 0.5, "title": 0.4, "label": 0.4}

MINING_TOKENS = 600
_MAX_MODEL_CLAIMS = 12
_MAX_MODEL_CHARS = 240

_PROMPT = (
    "You are re-reading retained raw TEXT for one missing memory attribute. "
    "Extract; do not infer. The requested attribute key is %(key)s. "
    "Return strict JSON {\"value\": string|null, \"claim_id\": string|null}. "
    "The value must occur verbatim in the one claim you cite. If the evidence "
    "does not literally answer the key, return null.\n\n"
    "NODE: %(node)s\nEVIDENCE:\n%(evidence)s"
)


def _grammar_fingerprint() -> str:
    """Version the bytes that decide what retained TEXT can yield.

    A hand-maintained version is another stale switch waiting to happen. The
    mining rules and the birth grammars jointly define the interpretation of
    retained text, so a change to either invalidates a completed mining
    receipt and gives old evidence another turn.
    """
    h = hashlib.sha256()
    here = os.path.dirname(os.path.abspath(__file__))
    package = os.path.dirname(os.path.dirname(here))
    for path in (__file__, os.path.join(package, "nodes", "birth",
                                        "entity_extractor.py")):
        h.update(os.path.relpath(path, package).encode())
        try:
            with open(path, "rb") as fh:
                h.update(fh.read())
        except OSError:
            h.update(b"<unreadable>")
    return h.hexdigest()[:12]


# Public so the night and cockpit can record/compare the exact interpreter
# currently chewing retained text. Recomputed on process restart, which is
# when deployed code can have changed.
GRAMMAR_VERSION = _grammar_fingerprint()


@dataclass(frozen=True)
class MiningTask:
    node_id: str
    key: str
    priority: float
    episode_ids: Tuple[str, ...]


def _evidence_fingerprint(claim_ids: List[str]) -> str:
    material = GRAMMAR_VERSION + "|" + "|".join(sorted(claim_ids))
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def mining_tasks(graph_store, t_ms: int) -> List[MiningTask]:
    tasks: List[MiningTask] = []
    for node in graph_store.nodes.values():
        keys = agenda_keys(node)
        if not keys:
            continue
        act = activation_now(node.activation, node.last_active_t_ms, t_ms)
        recur = 1.0 + 0.2 * min(sightings(node), 5)
        eps = tuple(sorted(episodes_of(node)))
        for key in keys:
            tasks.append(MiningTask(node.id, key,
                                    act * recur * _KEY_WEIGHT.get(key, 0.3),
                                    eps))
    tasks.sort(key=lambda t: (-t.priority, t.node_id, t.key))
    return tasks


# HOW MUCH RAW ONE MINING CALL MAY DECRYPT (2026-10-07).
#
# A mining task used to read every claim of every episode its node appears
# in. For an app node on the founder's store that is hundreds of thousands
# of claims, decrypted one by one on the daemon's single owner thread; the
# night polls its deadline only BETWEEN tasks, so one task held the thread
# that answers questions for many minutes (every ask timed out at 120s
# whenever the screen locked). A call now reads one chunk; a dry chunk saves
# its position durably and the next slice resumes there, so bounding costs
# latency, never recall. Only a complete sweep (or a find) writes the
# receipt that says this raw has been chewed.
MINING_CHUNK_CLAIMS = 400


def _evidence_ids(task: MiningTask, claims_by_episode) -> List[Tuple[str, str]]:
    """(episode_id, claim_id) for the task's raw, without decrypting."""
    ids_of = getattr(claims_by_episode, "claim_ids", None)
    out, seen = [], set()
    for ep in task.episode_ids:
        ids = (ids_of(ep) if ids_of is not None
               else [c.id for c in claims_by_episode.get(ep, [])])
        for cid in ids:
            if cid not in seen:
                seen.add(cid)
                out.append((ep, cid))
    return out


# THE EVIDENCE MEMO (2026-10-14). Walking a hot node's membership is the
# expensive part of mining, not decrypting its chunk: on the live store one
# walk plus its fingerprint held the owner thread for most of a 10s slice,
# and it was repeated for every 400-claim chunk. The walk is a pure function
# of membership, so it is kept on the evidence map itself (it dies with the
# map) and reused while `evidence_version()` is unchanged. One entry: the
# night stays on one task until it finishes, so one is all that is reused,
# and a hot node's id list is the one thing here big enough to matter.
def _memo(claims_by_episode):
    version = getattr(claims_by_episode, "evidence_version", None)
    token = version() if version is not None else None
    if token is None:
        return None, None
    memo = getattr(claims_by_episode, "_mining_memo", None)
    if memo is None or memo.get("token") != token:
        memo = {"token": token, "walk": None, "settled": set()}
        try:
            claims_by_episode._mining_memo = memo
        except AttributeError:                 # a plain dict: no memo
            return None, None
    return memo, token


def _walk(task: MiningTask, claims_by_episode, memo):
    """(pairs, fingerprint) for the task, walked at most once per token."""
    key = (task.node_id, task.key, task.episode_ids)
    if memo is not None and memo["walk"] is not None \
            and memo["walk"][0] == key:
        return memo["walk"][1], memo["walk"][2]
    pairs = _evidence_ids(task, claims_by_episode)
    fp = _evidence_fingerprint([cid for _ep, cid in pairs]) if pairs else None
    if memo is not None:
        memo["walk"] = (key, pairs, fp)
    return pairs, fp


def _decrypt(claims_by_episode, pairs) -> List[Tuple[str, object]]:
    """(episode_id, claim) for exactly these ids."""
    fetch = getattr(claims_by_episode, "claims_for", None)
    if fetch is not None:
        by_id = {c.id: c for c in fetch([cid for _ep, cid in pairs])}
    else:
        by_id = {}
        for ep in {ep for ep, _cid in pairs}:
            for c in claims_by_episode.get(ep, []):
                by_id[c.id] = c
    return [(ep, by_id[cid]) for ep, cid in pairs if cid in by_id]


def _cursor_key(task: MiningTask) -> str:
    return "mine_cursor:%s:%s" % (task.node_id, task.key)


def mine(task: MiningTask, graph_store, claims_by_episode: Dict[str, list],
         reasoner=None, on_reasoner_call=None, stop=None,
         on_chunk=None) -> List[Tuple[str, str]]:
    """Run one task against the node's OWN raw claims. Returns accrued
    (key, value) pairs. Read-only on raw; write-only on the node (meta).

    Bounded: at most MINING_CHUNK_CLAIMS claims are decrypted between polls
    (see the note above it). Without ``stop`` one call reads one chunk; with
    it, the call keeps sweeping chunk after chunk until the sweep ends or
    ``stop()`` says the slice is over, so a long sweep pays for walking its
    evidence once instead of once per chunk. ``on_chunk`` is told about each
    dry chunk whose durable cursor advanced."""
    node = graph_store.get_node(task.node_id)
    if node is None:
        return []
    memo, _token = _memo(claims_by_episode)
    settled_key = (task.node_id, task.key, task.episode_ids)
    if memo is not None and settled_key in memo["settled"]:
        return []                       # checked on this exact membership
    pairs, fp = _walk(task, claims_by_episode, memo)
    if not pairs:
        return []
    # Durable on a durable store (see nodes/graph_store.py): a night that
    # already chewed this raw must not chew it again after a restart. The
    # fingerprint is over the ids, so deciding costs no decryption at all.
    if graph_store.mining_fingerprint(task.node_id, task.key) == fp:
        if memo is not None:
            memo["settled"].add(settled_key)
        return []                                   # unchanged raw: never re-chewed
    getm = getattr(graph_store, "get_meta", None)
    setm = getattr(graph_store, "set_meta", None)
    try:
        offset = int((getm(_cursor_key(task), 0) if getm else 0) or 0)
    except (TypeError, ValueError):
        offset = 0
    while True:
        # New raw keeps arriving for a live node, so the id list moves under
        # the cursor; the offset is kept (not reset) so a busy node's sweep
        # still advances rather than re-reading its first chunk forever.
        if offset >= len(pairs):
            offset = 0
        window = pairs[offset:offset + MINING_CHUNK_CLAIMS]
        swept_all = offset + len(window) >= len(pairs)
        chunk = _decrypt(claims_by_episode, window)
        claims = [c for _ep, c in chunk]
        episode_of = {c.id: ep for ep, c in chunk}
        found = _mine_deterministic(task.key, node, claims) if claims else []
        if found or swept_all:
            break
        offset += len(window)
        if setm is not None:
            setm(_cursor_key(task), offset)
        if on_chunk is not None:
            on_chunk()
        if stop is None or stop():
            return []                               # resume here next slice
    if setm is not None:
        setm(_cursor_key(task), 0)
    completed = bool(found)
    # Reasoner-backed mining is the fallback, one guarded call, only when the
    # grammars came up dry AND the night granted a reasoner through its token
    # gate. Without that grant the task stays pending: recording a dry
    # deterministic receipt would silently prevent the funded pass forever.
    if not found and reasoner is not None:
        if on_reasoner_call is not None:
            on_reasoner_call()
        completed, found = _mine_reasoner(task, node, claims, reasoner)
    accrued: List[Tuple[str, str]] = []
    for key, value, claim in found:
        if accrue(node, key, value, claim.grade, claim.confidence,
                  episode_of.get(claim.id) or
                  _episode_of(claim, task.episode_ids, claims_by_episode),
                  claim.coord.t_ms):
            accrued.append((key, value))
    if accrued:
        graph_store.upsert_node(node)
    # COMPLETION COMES LAST.  The old order wrote this receipt before the
    # node update, so a crash or failed graph write could permanently label
    # lost work as complete.  A dry deterministic pass is still complete for
    # this grammar; a changed grammar changes fp and reopens it.
    if completed:
        graph_store.set_mining_fingerprint(task.node_id, task.key, fp)
        if memo is not None and not accrued:
            memo["settled"].add(settled_key)
    return accrued


def _mine_reasoner(task, node, claims, reasoner):
    """Return (completed, findings), defaulting to retry on any uncertainty.

    A valid null is a completed abstention. Malformed output, an unavailable
    model, a made-up claim id, or a value absent from the cited raw text is
    not cached; the next funded night gets another turn.
    """
    evidence = list(claims)[:_MAX_MODEL_CLAIMS]
    rendered = "\n".join(
        "[%s] %s" % (c.id, " ".join(c.text.split())[:_MAX_MODEL_CHARS])
        for c in evidence)
    node_facts = {a.key: a.value for a in node.attributes}
    try:
        raw = reasoner.complete(_PROMPT % {
            "key": task.key, "node": json.dumps(node_facts, sort_keys=True),
            "evidence": rendered})
        answer = json.loads(str(raw))
    except Exception:                                      # noqa: BLE001
        return False, []
    value = answer.get("value") if isinstance(answer, dict) else None
    claim_id = answer.get("claim_id") if isinstance(answer, dict) else None
    if value is None:
        return True, []
    value = str(value).strip()
    cited = next((c for c in evidence if c.id == claim_id), None)
    if not value or cited is None or value.casefold() not in cited.text.casefold():
        return False, []
    return True, [(task.key, value, cited)]


def _episode_of(claim, episode_ids, claims_by_episode) -> str:
    for ep in episode_ids:
        if any(c.id == claim.id for c in claims_by_episode.get(ep, [])):
            return ep
    return episode_ids[0] if episode_ids else "?"


def _mine_deterministic(key: str, node: Node, claims) -> List[tuple]:
    out = []
    if key in ("seen_as", "relationship"):
        for c in claims:
            for relation, name in _INTRO_RE.findall(c.text):
                if name.lower() in _NAME_STOPLIST:
                    continue
                out.append(("seen_as", name, c))
                if relation:
                    out.append(("relationship", relation.lower(), c))
    elif key == "color":
        for a in node.attributes:
            if a.key == "object":
                first = a.value.split()[0].lower()
                if first in _COLORS:
                    ref = claims[0]
                    out.append(("color", first, ref))
    return out
