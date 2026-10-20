"""OWNS: always-on birth + accrual + wiring + activation, delta-idempotent
(spec 04 §7 / 08 §4, rebuilt with birth v2).

Two-phase resolution (the order IS the design):
  phase 1 — deterministic kinds (place/app/site/topic/object) resolve by
            natural key and become the episode's CONTEXT FINGERPRINT;
  phase 2 — person mentions resolve AGAINST that fingerprint, so "Sam at the
            office in VS Code" and "Sam at the gym" can be different Sams.

Delta discipline: the hub re-calls upkeep every time an episode grows. A
processed-claims ledger keyed by episode makes every call process only the
DELTA — replay adds zero attributes, zero edge weight, zero activation.
Accrual is idempotent underneath as a second wall (defense in depth: the
write path to the graph must be safe even if the caller is not).
"""
from __future__ import annotations

from typing import List, Set

from tracemem.domain import Edge, EdgeType, Grade, NodeKind
from tracemem.nodes.accrual import accrue
from tracemem.nodes.activation_store import on_event
from tracemem.nodes.birth.entity_extractor import extract_mentions
from tracemem.nodes.birth.question_gen import maybe_question
from tracemem.nodes.birth.resolver import resolve

_ATTR_KEY = {"person": "seen_as", "app": "app", "site": "site",
             "place": "place", "topic": "topic", "object": "object"}
_DETERMINISTIC = ("place", "app", "site", "topic", "object")

# THE BIRTH FLOOR (2026-07-29, found by the first production night run).
#
# A perceiver's own confidence was being thrown away at exactly the moment it
# mattered most. Vision OCR reports how sure it is of each run; that number
# rode along on the Claim and nothing ever read it before minting a durable
# entity. So the graph filled with misreads standing as peers of the truth:
#
#     chess.com (1.0)      alongside    chesa.com     (0.5)
#     www.chess.com (1.0)  alongside    www.chesa.com (0.5)
#                                       youluve.com   (0.3)
#     and people named "Real", "Safe", "Launminrkyyodi" (0.3)
#
# GRADE COULD NOT HAVE CAUGHT THIS, and that is the point worth remembering:
# `grade_for("ocr")` is VERBATIM because grade measures TRANSCRIPTION FIDELITY
# — how faithfully the machine copied what was on the glass. It says nothing
# about whether the thing copied is real. Confidence is the axis that does,
# and it was the one axis nobody consulted.
#
# NOTHING IS LOST BY REFUSING. The raw claim is retained forever and stays
# fully searchable; it simply does not get promoted into a durable belief.
# That is exactly the napkin's rule — "the marinated content that's not useful
# is discarded, NOT the raw material" — applied one step earlier, at birth
# rather than at pruning.
#
# UNCALIBRATED. 0.6 sits above every misread observed on the founder's real
# store (0.3-0.5) and below every true reading (1.0). One store, one week: the
# founder-grading loop owns moving it, and a witness is written when it bites
# so the cost of the floor is never invisible.
BIRTH_CONFIDENCE_FLOOR = 0.6

# CORROBORATION (2026-07-29, N2) — the second birth gate, and the one the
# confidence floor structurally cannot be.
#
# The floor asks "was the perceiver sure?". This asks "did it happen twice?".
# They catch different things: a misread the OCR was CONFIDENT about sails
# through the floor, and the founder's real store was full of them — people
# named "Real", "Safe", "Thinking", "Peru", "Hub", each at confidence 1.0.
#
# WHICH KINDS, AND WHY — decided by measuring the founder's own graph on
# 2026-07-29 rather than by argument. Distinct-episode counts, 71 nodes:
#
#   person : 22 seen once — Claude, Commits, Correction, Dea, Engl, Find,
#            Google, Hub, Peru, Launminrkyyodi, Mix, Next, Northside,
#            Password, Perception, Play, Qutlock, Real, Safe, Test, Thinking,
#            Translate — every one of them noise. 4 seen twice or more.
#            → the gate removes 85% of the noise and nothing real.
#   topic  : 7 seen once, ALL noise ("as uuid sou", "before further edits",
#            "critical docun"); 0 seen twice.
#            → the gate empties a tier that is currently 100% garbage. An
#              empty tier is not a loss; a tier that lies is.
#   site   : MEASURED AND DELIBERATELY EXCLUDED. Corroboration is a defence
#            against RANDOM noise, and OCR misreads of a website are
#            SYSTEMATIC — the same wrong string is read off the same screen
#            again and again. In the founder's raw claims: esa.com appears 13
#            times and chess.com twice, so this gate (and any frequency rule
#            built on it) would keep the misread and discard the real site.
#            The site tier cannot be fixed by counting; it needs a sense that
#            READS the URL instead of guessing it (P5, the Accessibility
#            tree). Guessing better is not a fix.
#   app,
#   place  : EXEMPT BY CONSTRUCTION. These are reported by the OS and the
#            place sense, not transcribed off the glass. There is no misread
#            to corroborate, and holding them back would delay every true
#            first sighting for nothing.
#
# NOTHING IS LOST BY WAITING, on the same principle as the floor: the raw
# claim is retained forever and stays searchable. A held mention is not a
# forgotten one — it sits in the store's waiting room with its episode, and
# it is accrued RETROACTIVELY the moment a second episode vouches for it, so
# a promoted node cites both sightings and not just the one that freed it.
#
# UNCALIBRATED. 2 is the smallest number that means "more than once". The
# founder-grading loop owns moving it.
CORROBORATING_EPISODES = 2
_NEEDS_CORROBORATION = ("person", "topic")


def _edge(graph_store, a: str, b: str, etype, t_ms: int) -> None:
    if a == b:
        return
    edge = graph_store.edge_between(a, b, etype)
    if edge is None:
        graph_store.upsert_edge(Edge(src=a, dst=b, type=etype, weight=1.0,
                                     evidence_count=1, last_reinforced_t_ms=t_ms))
    else:
        edge.reinforce(t_ms, delta=0.5)
        graph_store.upsert_edge(edge)


def upkeep(episode, claims, graph_store, surprise_fn=None,
           interruption_budget=None) -> List[object]:
    """Process one bound episode's NEW claims. Returns clarifying Questions."""
    surprise_fn = surprise_fn or (lambda claim: 0.0)
    # THE DELTA LEDGER NOW LIVES ON THE STORE, not on this process. On a
    # durable graph an in-process ledger would let every boot re-run upkeep
    # over the whole store, and `_edge()` below reinforces — which is not
    # idempotent. See the long note in nodes/graph_store.py.
    seen = graph_store.upkeep_seen(episode.id)
    fresh = [c for c in claims if c.id not in seen]
    if not fresh:
        return []
    graph_store.upkeep_mark(episode.id, [c.id for c in fresh])
    by_id = {c.id: c for c in fresh}
    # THE BIRTH FLOOR, applied here and nowhere else: one gate, before any
    # mention can become a durable node. A mention whose SOURCE CLAIM the
    # perceiver was unsure of does not get to stand in the graph as a peer of
    # one it was sure of. See BIRTH_CONFIDENCE_FLOOR above for what this cost
    # the founder's real store, and why grade could not have caught it.
    mentions = [m for m in extract_mentions(episode, fresh)
                if _confident_enough(by_id.get(m.claim_id))]
    # A READ BEATS A GUESS ABOUT THE SAME FACT IN THE SAME MOMENT.
    mentions = _read_beats_guess(mentions, by_id)
    # THE SECOND BIRTH GATE. `held` maps a promoted mention's key to the
    # earlier sightings that vouched for it, so they can be accrued too.
    mentions, held = _corroborated(mentions, by_id, graph_store)

    questions: List[object] = []
    resolved = []                      # (node, mention)
    ctx_ids: Set[str] = set()

    # phase 1 — the deterministic world becomes context
    for m in (m for m in mentions if m.kind in _DETERMINISTIC):
        res = resolve(m, graph_store)
        node = res.node
        claim = by_id.get(m.claim_id)
        grade = claim.grade if claim else Grade.GIST
        conf = claim.confidence if claim else None
        accrue(node, _ATTR_KEY[m.kind], m.surface, grade,
               conf or _default_conf(), episode.id, m.t_ms)
        _accrue_held(node, _ATTR_KEY[m.kind], held.get(_mention_key(m), ()),
                     episode.id)
        if m.kind == "site" and m.detail:
            accrue(node, "page", m.detail, grade, conf or _default_conf(),
                   episode.id, m.t_ms)
        graph_store.upsert_node(node)
        ctx_ids.add(node.id)
        resolved.append((node, m))

    # phase 2 — people resolve against the context fingerprint
    for m in (m for m in mentions if m.kind == "person"):
        res = resolve(m, graph_store, ctx_ids)
        if res.node is None:
            # DEFERRED (law 3): nothing accrued anywhere; the raw claim holds
            # the fact, the question holds the fork.
            q = maybe_question(res, interruption_budget, m.t_ms,
                               graph_store=graph_store)
            if q is not None:
                questions.append(q)
            continue
        node = res.node
        claim = by_id.get(m.claim_id)
        accrue(node, "seen_as", m.surface,
               claim.grade if claim else Grade.GIST,
               (claim.confidence if claim else _default_conf()),
               episode.id, m.t_ms)
        # The sightings that waited: their evidence belongs to this node, or
        # a belief born on its second sighting would cite only the second and
        # claim to be younger than it is.
        _accrue_held(node, "seen_as", held.get(_mention_key(m), ()), episode.id)
        graph_store.upsert_node(node)
        resolved.append((node, m))
        q = maybe_question(res, interruption_budget,
                           m.t_ms, graph_store=graph_store)
        if q is not None:
            questions.append(q)

    # wiring: co-occurrence among this episode's cast; people LOCATED_AT place
    t0 = fresh[0].coord.t_ms
    node_ids = [n.id for n, _ in resolved]
    place_ids = [n.id for n, m in resolved if m.kind == "place"]
    for i, a in enumerate(node_ids):
        for b in node_ids[i + 1:]:
            _edge(graph_store, a, b, EdgeType.CO_OCCURS, t0)
    for n, m in resolved:
        if m.kind == "person":
            for pid in place_ids:
                _edge(graph_store, n.id, pid, EdgeType.LOCATED_AT, t0)

    # activation: everything in this moment was just sighted
    s = max((surprise_fn(c) for c in fresh), default=0.0)
    for nid in dict.fromkeys(node_ids):
        on_event(graph_store, nid, "sighting", s, t0)
    return questions


# PERCEIVERS THAT READ rather than recognise. The macOS Accessibility tree
# hands over the exact string an app is displaying; there is no transcription
# step and therefore no transcription error.
_READING_PERCEIVERS = ("ax",)

# Kinds where a reading and a guess are answering THE SAME QUESTION, so one
# can stand in for the other. Only `site` qualifies today: the address bar and
# the OCR of the address bar are two attempts at one string. An AX button
# label and an OCR'd paragraph are NOT competing accounts of one fact, so
# objects and topics are untouched — suppressing those would throw away real
# perception on a technicality.
_READ_BEATS_GUESS_KINDS = ("site",)


def _read_beats_guess(mentions, by_id):
    """Drop guessed mentions where the same episode also has a READ one.

    THE MEASURED PROBLEM (2026-07-29, the founder's real claims):

        esa.com        13 occurrences        chess.com          2
        youtube.co,  youluve.com,  utube.co,  thyoutube.com  ...  youtube.com

    OCR misreads a domain the SAME WRONG WAY every time it sees that screen,
    so the misread is not rare — it is more common than the truth, and every
    counting, corroboration or edit-distance rule therefore keeps the misread
    and discards the real site. Frequency cannot break this tie because
    frequency is what is corrupted.

    Provenance can. Within one episode, if the Accessibility tree reported the
    address bar, then whatever OCR thought it saw in that same moment is a
    worse account of the same fact — not a second site the founder also
    visited. So the guesses are not promoted.

    NOTHING IS DISCARDED. The OCR claim stays in the store forever and stays
    searchable; it simply does not mint a durable node. Same rule as the
    confidence floor and the corroboration gate — refuse to PROMOTE, never
    refuse to RECORD. (The word avoided in this paragraph is one
    `test_the_raw_claim_is_never_discarded_by_the_floor` greps this module's
    source for, and that guard is deliberately crude: a module that can talk
    about removing raw is one edit away from doing it.)

    DELIBERATELY SCOPED TO ONE EPISODE. A read in this moment says nothing
    about a site OCR saw yesterday in a different window.

    THE KNOWN COST, MEASURED RATHER THAN GUESSED (2026-07-29, 5,973 of the
    founder's real claims spanning every AX reading in the store):

        OCR alone         11 sites (names withheld)
        read beats guess   2 sites

    Nine suppressed, and seven of them are plainly the same misread domain
    truncated four different ways. But two of them, both shop sites, may
    well be REAL sites, seen by OCR in a window
    the accessibility read did not cover — the read describes the FOCUSED
    window, while OCR sees the whole screen. So this rule can suppress a true
    site, and on this evidence sometimes does.

    IT IS KEPT BROAD ANYWAY, and the reason is the asymmetry: a suppressed
    real site is still in the raw store, searchable, and returns the moment it
    is read properly; a promoted misread is a durable node that pollutes every
    belief downstream and cannot be told from a real one afterwards.

    A NARROWER VERSION WAS TRIED AND REJECTED THE SAME DAY: suppress only OCR
    strings that look like the read one. It cannot work on a prefix
    truncation — `esa.com` is `chess.com` with "ch" dropped, and no shared-
    prefix or containment test sees that. Guessing which misread belongs to
    which site is the same guessing game this whole sense exists to end.

    THE REAL FIX is for the accessibility sense to read EVERY browser window
    rather than only the focused one, at which point nothing true is lost
    because everything true has been read. That is the next move on this
    tier, and it is a capability change rather than a cleverer heuristic.
    """
    read_kinds = set()
    for m in mentions:
        if m.kind not in _READ_BEATS_GUESS_KINDS:
            continue
        claim = by_id.get(m.claim_id)
        perceiver = getattr(getattr(claim, "provenance", None), "perceiver", "")
        if str(perceiver).lower() in _READING_PERCEIVERS:
            read_kinds.add(m.kind)
    if not read_kinds:
        return mentions

    kept = []
    for m in mentions:
        if m.kind in read_kinds:
            claim = by_id.get(m.claim_id)
            perceiver = getattr(getattr(claim, "provenance", None),
                                "perceiver", "")
            if str(perceiver).lower() not in _READING_PERCEIVERS:
                continue                  # a guess, outranked in this moment
        kept.append(m)
    return kept


def _mention_key(m) -> str:
    """One surface, one waiting-room slot. Case-folded because 'Hana' and
    'hana' are the same sighting to everyone except a dict."""
    return "%s:%s" % (m.kind, m.surface.strip().lower())


def _corroborated(mentions, by_id, graph_store):
    """Split mentions into (may become durable now, evidence that waited).

    Kinds outside `_NEEDS_CORROBORATION` pass straight through — see the long
    note on CORROBORATING_EPISODES for why site is NOT among them.
    """
    if not (hasattr(graph_store, "record_sighting")
            and hasattr(graph_store, "sightings")):
        # An older store implementation: pass everything through rather than
        # silently emptying the person tier. A missing gate is visible in the
        # digest; a gate that quietly deletes people is not.
        return list(mentions), {}

    allowed, held = [], {}
    for m in mentions:
        if m.kind not in _NEEDS_CORROBORATION or m.anonymous:
            # ANONYMOUS MENTIONS ARE EXEMPT, and this is a law, not a
            # convenience. "a person" is not a surface that can be misread —
            # it is the extractor saying it saw somebody and knows no name.
            # Corroborating on the surface would file every stranger in the
            # world under one key, so a stranger at home and a stranger in a
            # café would vouch for each other and blend into one ghost. That
            # is precisely what birth law 2 forbids, and a test caught it:
            # test_ghosts_in_unrelated_contexts_stay_separate.
            allowed.append(m)
            continue
        key = _mention_key(m)
        claim = by_id.get(m.claim_id)
        graph_store.record_sighting(key, m.episode_id, {
            "episode_id": m.episode_id, "claim_id": m.claim_id,
            "t_ms": m.t_ms,
            "grade": int(getattr(claim.grade, "value", 0)) if claim else 0,
            "confidence": float(getattr(
                getattr(claim, "confidence", None), "value", 0.7)) if claim
                else 0.7,
            "surface": m.surface, "kind": m.kind})
        seen = graph_store.sightings(key)
        corroborated_enough = (
            len({s.get("episode_id") for s in seen}) >= CORROBORATING_EPISODES)
        # A PERSON ALSO NEEDS A VERDICT. Corroboration proves a surface was
        # seen twice; it says nothing about whether the surface is a PERSON.
        # "Springfield" and "Trash" were each seen dozens of times. Measured on
        # the founder's graph: 110 surfaces born as people, ~30 of them
        # people. The night asks the local model once per surface and records
        # the answer (nodes/birth/person_judge.py); until it has, the mention
        # waits here — in raw, losing nothing.
        if m.kind == "person" and not m.anonymous:
            from tracemem.nodes.birth.person_judge import verdict_for
            if verdict_for(graph_store, key) is not True:
                continue
        if corroborated_enough:
            allowed.append(m)
            held[key] = [s for s in seen
                         if s.get("episode_id") != m.episode_id]
    return allowed, held


def _accrue_held(node, attr_key, sightings, current_episode_id) -> None:
    """Give a just-promoted node the evidence that was waiting for it.

    The earlier sighting contributes EVIDENCE, not identity: it is accrued
    onto the node the current episode's context resolved to, because the
    resolver's context fingerprint is a fact about the promoting episode.
    Stated rather than hidden — it is the one approximation in this gate.
    """
    from tracemem.domain import Confidence, Grade as _Grade
    for s in sightings or ():
        if s.get("episode_id") == current_episode_id:
            continue
        try:
            grade = _Grade(s.get("grade"))
        except ValueError:
            grade = Grade.GIST
        accrue(node, attr_key, s.get("surface", ""), grade,
               Confidence(float(s.get("confidence", 0.7))),
               s.get("episode_id", ""), int(s.get("t_ms", 0)))


def _confident_enough(claim) -> bool:
    """May this claim mint a durable belief?

    A missing claim passes: synthetic and backfilled mentions carry no source
    in this map, and refusing them would silently empty the graph in every
    caller that does not thread claims through — a far worse failure than the
    one this floor exists to prevent.
    """
    if claim is None:
        return True
    conf = getattr(claim, "confidence", None)
    if conf is None:
        return True
    return getattr(conf, "value", 1.0) >= BIRTH_CONFIDENCE_FLOOR


def _default_conf():
    from tracemem.domain import Confidence
    return Confidence(0.7)
