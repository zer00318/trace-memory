"""OWNS: the single-writer ingest + ask path (spec 08 §4, O5).

One Hub instance owns all writes: claims -> log -> bind -> upkeep in one
call, so there is no writer race to reason about. ask() is the whole answer
path: cue -> supply -> one guarded turn -> grounded answer. Health is
heartbeat-based: a daemon that stops posting is DEAD within HEARTBEAT_S —
silence never masquerades as health (the capture lesson, spec 07).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

from tracemem.app.scheduler import QUERY_PRIORITY, SpotlightBusy
from tracemem.binder.event_segmenter import MAX_EPISODE_MS, boundaries
from tracemem.binder.projection import project
from tracemem.llm.answer import Answer, parse_answer
from tracemem.llm.bluff_check import guard as bluff_guard
from tracemem.llm.prompt import (render_dossier_prompt, render_prompt,
                                 render_ranking_prompt, render_window_prompt)
from tracemem.nodes.marination.background import upkeep
from tracemem.supplier.corrections import find_corrections
from tracemem.supplier.corrections import render as render_corrections
from tracemem.supplier.cue import parse_cue
from tracemem.supplier.followup import carry_window
from tracemem.supplier.supplier import supply
from tracemem.supplier.dossier import supply_subject
from tracemem.supplier.ranking import supply_ranking
from tracemem.supplier.window import supply_window

HEARTBEAT_S = 120
BIND_WATERMARK_SEQ = "bind_watermark_seq"


class _EpisodeClaimMap(dict):
    """Lazy durable episode evidence with the mapping interface consumers use."""

    def __init__(self, graph_store, claim_log) -> None:
        super().__init__()
        self._graph = graph_store
        self._log = claim_log
        ids = getattr(graph_store, "episode_ids", None)
        self._known = set(ids() if ids is not None else ())

    def remember(self, episode_id: str) -> None:
        self._known.add(episode_id)

    def evidence_version(self):
        """Token for "membership is unchanged since you last walked it", or
        None when the graph cannot say (then nothing may be memoized)."""
        version = getattr(self._graph, "membership_version", None)
        if version is None:
            return None
        return (len(self._known), version())

    def claim_ids(self, episode_id: str) -> list:
        """An episode's raw claim ids, successors resolved, WITHOUT
        decrypting anything — what mining fingerprints before it decides
        whether any raw needs to be read at all."""
        if episode_id not in self._known:
            return []
        getter = getattr(self._graph, "claim_ids_for_episode", None)
        if getter is None:
            return [c.id for c in (self.get(episode_id) or ())]
        resolver = getattr(self._graph, "resolve_episode_ids", None)
        episode_ids = (resolver(episode_id) if resolver is not None
                       else [episode_id])
        out, seen = [], set()
        for current_id in episode_ids:
            for claim_id in getter(current_id):
                if claim_id not in seen:
                    seen.add(claim_id)
                    out.append(claim_id)
        return out

    def claims_for(self, claim_ids) -> list:
        """Decrypt exactly these claims, nothing else."""
        if self._log is None:
            return []
        out = []
        for claim_id in claim_ids:
            claim = self._log.get(claim_id)
            if claim is not None:
                out.append(claim)
        return out

    def bounded_text(self, episode_id: str, max_chars: int, seen=None,
                     max_claims: int = 8) -> str:
        """Decrypt only enough durable evidence to fill a search summary.

        Loading the mapping value is intentionally complete because answer
        and night consumers may need the whole episode. Vector indexing needs
        at most 600 characters; routing it through ``get`` decrypted every
        claim in every cited episode before slicing, which held the sole
        owner thread for 29 minutes on the live store.
        """
        if (max_chars <= 0 or max_claims <= 0 or
                episode_id not in self._known or
                self._log is None):
            return ""
        getter = getattr(self._graph, "claim_ids_for_episode", None)
        if getter is None:
            return ""
        resolver = getattr(self._graph, "resolve_episode_ids", None)
        episode_ids = (resolver(episode_id) if resolver is not None
                       else [episode_id])
        already = seen if seen is not None else set()
        out, used = [], 0
        bounded_ids = getattr(self._graph, "evidence_claim_ids", None)
        if bounded_ids is not None:
            # Apply LIMIT in SQLite. Asking `claim_ids_for_episode` and then
            # slicing still materialised a 600k-row Python list; the live
            # stack proved that merely enumerating ids could outlive the
            # nominal ten-second night slice even after decryption was bound.
            batches = [bounded_ids(
                episode_ids, limit=max(1, min(int(max_claims), 8)))]
        else:
            batches = [getter(current_id) for current_id in episode_ids]
        for claim_ids in batches:
            for claim_id in claim_ids:
                if claim_id in already:
                    continue
                if len(out) >= max_claims:
                    return " ".join(out)
                already.add(claim_id)
                claim = self._log.get(claim_id)
                if claim is None:
                    continue
                room = max_chars - used
                if room <= 0:
                    return " ".join(out)
                piece = claim.text[:room]
                if piece:
                    out.append(piece)
                    used += len(piece) + 1
                if used >= max_chars:
                    return " ".join(out)
        return " ".join(out)

    def _load(self, episode_id: str):
        if episode_id not in self._known or self._log is None:
            return None
        getter = getattr(self._graph, "claim_ids_for_episode", None)
        if getter is None:
            return None
        resolver = getattr(self._graph, "resolve_episode_ids", None)
        episode_ids = (resolver(episode_id) if resolver is not None
                       else [episode_id])
        claims = []
        seen = set()
        for current_id in episode_ids:
            for claim_id in getter(current_id):
                if claim_id in seen:
                    continue
                seen.add(claim_id)
                claim = self._log.get(claim_id)
                if claim is not None:
                    claims.append(claim)
        dict.__setitem__(self, episode_id, claims)
        return claims

    def get(self, key, default=None):
        if key not in self:
            loaded = self._load(key)
            if loaded is None:
                return default
        return dict.get(self, key, default)

    def __getitem__(self, key):
        if key not in self:
            loaded = self._load(key)
            if loaded is None:
                raise KeyError(key)
        return dict.__getitem__(self, key)

    def setdefault(self, key, default=None):
        if key not in self:
            self._load(key)
        return dict.setdefault(self, key, default)

    def __bool__(self):
        return bool(self._known or dict.__len__(self))


@dataclass
class Wiring:
    claim_log: object
    graph_store: object
    vector_index: object
    reasoner: object
    spotlight: object
    clock: object
    # The embedder port, held so the night can wire MEANING into the graph
    # (semantic edges). Optional: without it the night simply skips that pass.
    embedder: Optional[object] = None
    token_ledger: Optional[object] = None
    interruptions: Optional[object] = None
    surprise_fn: object = None
    routine_model: Optional[object] = None
    answer_model: str = "local-ollama"
    answer_budget_tokens: int = 8000
    # The never-lie guard is ON by default: the promise is the product's
    # spine, and a defence you have to remember to enable is one that is off
    # when it matters. Tests that use a fake reasoner with no verify replies
    # set this False explicitly, which documents at the call site that they
    # are opting out of the check.
    verify_answers: bool = True


class Hub:
    def __init__(self, wiring: Wiring) -> None:
        self.w = wiring
        load_boundaries = getattr(wiring.graph_store, "load_boundaries", None)
        self._scene_changes: List[int] = list(
            load_boundaries("scene") if load_boundaries is not None else ())
        load_episodes = getattr(wiring.graph_store, "load_episodes", None)
        self._episodes: Dict = dict(
            load_episodes() if load_episodes is not None else {})
        version = getattr(wiring.graph_store, "boundary_version", None)
        self._boundary_version = int(version() if version is not None else 0)
        self._claims_by_episode: Dict[str, List] = _EpisodeClaimMap(
            wiring.graph_store, wiring.claim_log)
        self._heartbeats: Dict[str, int] = {}
        self.pending_questions: List[object] = []
        # THE INDEX IS REBUILT WHEN IT IS READ, NOT WHEN IT IS DIRTIED.
        # `post_claims` used to call `vector_index.rebuild_from` on every
        # batch. Live that is 764 embeddings per 64-claim flush; on boot
        # backfill it is ~1,800 chunks x 764 nodes, and the summaries do NOT
        # cache because a node's evidence text grows as claims bind — so the
        # embedder re-runs on almost every one. That single line is why
        # binding the founder's 470,322 claims was an hours-long wall, which
        # in turn is why the daemon had to be run without a brain at all.
        # Nothing downstream reads the index except ask() and night().
        self._index_dirty = True
        # WHY THE LAST ANSWER WAS REFUSED, IN THE CHECKER'S OWN WORDS.
        # Empty until a guard refusal happens. See bluff_check.guard's `note`.
        self.last_refusal = ""
        # {source_id: int(Grade)} for the last answer. See ask().
        self.last_citation_grades = {}
        # (question, window, t_ms) — what a follow-up leans on. See
        # supplier/followup; it expires, so this is not a growing thing.
        self._last_asked = None
        # THE EPISODE GRID, CACHED. See _bounds_for.
        self._bounds: List[int] = []
        self._bounds_scenes = 0
        # The index learns what its nodes are MADE of, not just what they are
        # called (see VectorIndex._summary). Bound once, here, because the Hub
        # is the only object that holds both the graph and the raw material.
        bind = getattr(self.w.vector_index, "bind_evidence", None)
        if bind is not None:
            bind(self._node_evidence)

    def _node_evidence(self, node) -> str:
        """The raw material behind a node: the text of the claims in the
        episodes its attributes cite. Deduped and order-stable so the index
        is a pure function of the store (same store -> same vectors)."""
        seen, out = set(), []
        limit = int(getattr(self.w.vector_index, "EVIDENCE_CHARS", 600))
        claim_limit = int(getattr(
            self.w.vector_index, "EVIDENCE_CLAIMS", 8))
        used = 0
        bounded = getattr(self._claims_by_episode, "bounded_text", None)
        for a in node.attributes:
            for ep_id in a.source_episode_ids:
                room = limit - used
                claims_left = claim_limit - len(seen)
                if room <= 0 or claims_left <= 0:
                    return " ".join(out)[:limit]
                if bounded is not None:
                    piece = bounded(ep_id, room, seen, claims_left)
                    if piece:
                        out.append(piece)
                        used += len(piece) + 1
                    continue
                for c in self._claims_by_episode.get(ep_id, ()):
                    if c.id in seen:
                        continue
                    seen.add(c.id)
                    piece = c.text[:limit - used]
                    if piece:
                        out.append(piece)
                        used += len(piece) + 1
                    if used >= limit:
                        return " ".join(out)[:limit]
                    if len(seen) >= claim_limit:
                        return " ".join(out)[:limit]
        return " ".join(out)[:limit]

    # -- ingest (the single writer) --

    def post_scene_change(self, t_ms: int) -> None:
        t_ms = int(t_ms)
        # Backfill and restart may rediscover a boundary already made active.
        # It is not late work and must not manufacture a fresh quarantine row.
        if t_ms in self._scene_changes:
            return
        latest_projected = max(
            (ep.t_range.end_ms for ep in self._episodes.values()), default=-1)
        if t_ms < latest_projected:
            # A late boundary changes the ordinal key of every later episode.
            # It must go through versioned reconciliation; applying it to the
            # live dict can attach a new claim to an existing key whose ID
            # names a different moment.  Quarantine it durably and keep the
            # current projection stable until the reconciler consumes it.
            persist = getattr(self.w.graph_store, "add_boundary", None)
            if persist is not None:
                persist(t_ms, "late_scene_pending")
            return
        self._scene_changes.append(t_ms)
        self._scene_changes.sort()
        persist = getattr(self.w.graph_store, "add_boundary", None)
        if persist is not None:
            persist(t_ms, "scene")

    def post_claims(self, claims) -> Dict[str, int]:
        claims = list(claims)
        if not claims:
            return {"appended": 0, "episodes": len(self._episodes)}
        # Volition (the actions dimension, §1.9) is stamped at its true seam —
        # the extraction pipeline, from FRAME-to-frame cadence — before claims
        # ever reach here. The hub does not re-infer it: a batch of one frame's
        # spans has no cadence, and guessing from it would overwrite the real
        # signal. OWNED claims (files) carry no cadence and stay unstamped.
        for c in claims:
            self.w.claim_log.append(c)
        t1 = max(c.coord.t_ms for c in claims)
        bounds = self._bounds_for(t1)
        self._episodes = project(
            claims, bounds, existing=self._episodes,
            # Once a late-boundary version exists, every episode minted under
            # that active boundary set carries the same version salt.  An
            # unsalted new key could otherwise reuse a retired v0 ID.
            salt=("b%d" % self._boundary_version
                  if self._boundary_version else ""))

        # upkeep per touched episode (cheap, CPU-only)
        touched = {}
        for key, ep in self._episodes.items():
            members = [c for c in claims if c.id in ep.claim_ids]
            if not members:
                # A durable graph may hold hundreds of thousands of claim
                # memberships.  Loading every historical episode for a
                # four-claim live batch made each flush O(lifetime raw) and
                # peaked above a gigabyte.  Untouched evidence stays lazy.
                continue
            self._claims_by_episode.remember(ep.id)
            held = self._claims_by_episode.setdefault(ep.id, [])
            known = {c.id for c in held}
            new_members = [c for c in members if c.id not in known]
            if new_members:
                held.extend(new_members)
                persist = getattr(self.w.graph_store, "persist_episode", None)
                if persist is not None:
                    persist(key[1], ep, [c.id for c in new_members])
            # Episode membership can survive a crash that happened before
            # upkeep.  Membership therefore must never be used as the work
            # receipt; only upkeep_seen may suppress the derivation.
            processed = self.w.graph_store.upkeep_seen(ep.id)
            fresh = [c for c in members if c.id not in processed]
            if fresh:
                touched[ep.id] = (ep, fresh)
        completed = []
        for ep, fresh in touched.values():
            qs = upkeep(ep, self._claims_by_episode[ep.id], self.w.graph_store,
                        surprise_fn=self.w.surprise_fn,
                        interruption_budget=self.w.interruptions)
            self.pending_questions.extend(qs)
            # This receipt is later than upkeep_seen on purpose: it means the
            # whole call returned, not merely that work began.
            completed.extend(c.id for c in fresh)
        marker = getattr(self.w.graph_store, "binding_mark", None)
        if completed and marker is not None:
            marker(completed)
        self._advance_bind_watermark()
        # THE INDEX IS REBUILT WHEN READ, NOT WHEN DIRTIED. This line used
        # to rebuild it here: 764 embeddings per 64-claim flush live, and
        # ~1,800 chunks x 764 nodes during boot backfill — and the summaries
        # do NOT cache, because a node's evidence text grows as claims bind,
        # so the embedder re-ran on nearly every one. Nothing reads the index
        # except ask() and night().
        self._index_dirty = True
        self.heartbeat("ingest")
        return {"appended": len(claims), "episodes": len(self._episodes),
                "questions": len(self.pending_questions)}

    def _advance_bind_watermark(self) -> int:
        """Advance only across a contiguous run of completed append receipts.

        A later screen claim may be complete while an earlier founder claim
        is still pending.  Taking ``max(seq)`` would strand the earlier claim;
        stopping at the first missing receipt makes that impossible.
        """
        graph, raw = self.w.graph_store, self.w.claim_log
        getter = getattr(graph, "get_meta", None)
        setter = getattr(graph, "set_meta", None)
        positions = getattr(raw, "positions", None)
        seen = getattr(graph, "binding_seen", None)
        if None in (getter, setter, positions, seen):
            return 0
        try:
            current = int(getter(BIND_WATERMARK_SEQ, 0) or 0)
        except (TypeError, ValueError):
            current = 0
        while True:
            page = positions(after_seq=current, limit=2048)
            if not page:
                break
            complete = seen([claim_id for claim_id, _seq in page])
            advanced = current
            for claim_id, seq in page:
                if claim_id not in complete:
                    break
                advanced = seq
            if advanced == current:
                break
            current = advanced
            setter(BIND_WATERMARK_SEQ, current)
            if len(page) < 2048:
                break
        return current

    # -- ask (the whole answer path) --

    # How much raw evidence to show when the guard refuses the prose. Enough
    # to be useful, bounded so a day's timeline does not become the answer.
    EVIDENCE_FALLBACK_LINES = 18

    def _with_corrections(self, context, question: str):
        """Prepend the founder's own words when they bear on the question."""
        from tracemem.supplier.packer import Context, CtxBlock

        try:
            said = find_corrections(self.w.claim_log, question)
            text = render_corrections(said)
        except Exception:                                    # noqa: BLE001
            return context
        if not text:
            return context
        # FIRST IN THE PACK. The founder's testimony outranks the screen's,
        # and packing order decides what a small model reads most carefully.
        #
        # The grade is TAKEN FROM THE CLAIMS, never written here. There is one
        # grade table (domain/grading.py) and a test forbids builders from
        # holding a private opinion about their own trustworthiness — it
        # caught this line stating VERBATIM outright, which would have been
        # correct today and a silent lie the day the founder perceiver's
        # grade changed.
        block = CtxBlock(kind="claim", text=text,
                         grade=max(c.grade for c in said),
                         source_ids=tuple(c.id for c in said),
                         t_ms=max(c.coord.t_ms for c in said))
        return Context((block,) + tuple(context.blocks), context.tokens)

    @classmethod
    def _show_directly(cls, context, guard_mode: str) -> bool:
        """Is there so little here that asking the model is pointless?

        SYNTHESIS ONLY. A needle question over two lines is a perfectly
        ordinary needle question — the answer is a sentence pulled out of
        those lines, and handing the founder the lines instead would be a
        regression on the path that works best. The rule is about summaries,
        which need something to summarise.
        """
        return (guard_mode == "synthesis"
                and cls._evidence_lines(context) <= cls.THIN_DOSSIER_LINES)

    @staticmethod
    def _evidence_lines(context) -> int:
        return sum(1 for b in context.blocks
                   for line in str(b.text).splitlines() if line.strip())

    def _note_refusal(self, why: str) -> None:
        """Keep the checker's sentence so the refusal can be looked at.

        A refusal used to be anonymous: the answer vanished and the reason
        died inside `is_grounded`'s bool. Every one investigated since has
        been the guard correctly objecting to something upstream had handed
        it, and each took a manual re-run with an instrumented checker to
        find. One string, no extra model call.
        """
        self.last_refusal = why[:400]
        # And said where it can be read later: stderr is the daemon log.
        import sys
        print("[tracemem] answer refused: %s" % self.last_refusal,
              file=sys.stderr, flush=True)

    # HOW FEW FACTS MAKE PROSE POINTLESS.
    #
    # Synthesis earns its place by compressing or connecting several facts.
    # Over one or two lines it can only restate them or embellish them, and
    # embellishing is exactly what the guard is there to catch — so a thin
    # dossier spends two model calls to produce something likely to be thrown
    # away, then apologises for it.
    #
    # A typical thin dossier is two lines: "Hana — a person, seen in N
    # moment(s) on <date>" and the one on-screen line that mentioned her.
    #
    # There is no summary of that which is not either those two lines again
    # or an invention. Showing them IS the best available answer.
    THIN_DOSSIER_LINES = 2

    def _evidence_only(self, context, refused: bool = True) -> Answer:
        """What the memory holds, rendered rather than described.

        `refused` distinguishes two situations that look identical on the
        wire and are not the same thing at all:

          True  — the model wrote prose and the guard rejected it. Something
                  went wrong; the evidence is a fallback.
          False — there was never enough here to summarise. Nothing went
                  wrong; the evidence is the answer.

        The founder was being shown "I could not summarise this without
        risking a claim the evidence does not support" for the second case,
        which reads as a malfunction when the honest reading is "this is all
        I know about Hana". Same lines, opposite meaning.
        """
        lines = []
        for block in context.blocks:
            for line in str(block.text).splitlines():
                if line.strip():
                    lines.append(line)
        shown = lines[:self.EVIDENCE_FALLBACK_LINES]
        more = len(lines) - len(shown)
        body = "\n".join(shown)
        if more > 0:
            body += "\n… and %d more line(s)." % more
        cites = tuple(dict.fromkeys(
            sid for b in context.blocks for sid in (b.source_ids or ())))
        head = ("I could not summarise this without risking a claim the "
                "evidence does not support, so here is the evidence:"
                if refused else
                "There is not enough here to summarise, so here is "
                "everything memory holds:")
        return Answer(text=head + "\n\n" + body,
                      citations=cites, abstained=False)

    def _bounds_for(self, t_end_ms: int) -> List[int]:
        """The episode grid up to `t_end_ms`, built once and extended.

        `boundaries(scene_changes, min(t0, 0), t1 + 1)` anchors the grid at
        EPOCH ZERO, and that anchor is load-bearing: `episode_key` is
        (place, epoch_index(t, bounds)), so the absolute index INTO this list
        is the episode's identity. Move the origin and every episode id ever
        cited changes.

        What is not load-bearing is rebuilding it on every batch. Measured
        2026-08-23: 992,889 entries, 0.10s and ~8MB of garbage per call, at a
        flush every ten seconds — about fourteen minutes of CPU a day spent
        counting to a million — and it grows by one entry every thirty
        minutes for as long as the memory exists.

        The list only ever grows at the tail while the scene-change list is
        unchanged, so that case extends. Anything else rebuilds, because a
        scene change arriving out of order shifts indices and silently
        renames episodes; getting that wrong is worse than being slow.
        """
        if len(self._scene_changes) != self._bounds_scenes or not self._bounds:
            self._bounds = boundaries(self._scene_changes, 0, t_end_ms + 1)
            self._bounds_scenes = len(self._scene_changes)
            return self._bounds
        end = t_end_ms + 1
        last = self._bounds[-1]
        if end == last:
            return self._bounds
        if end < last:
            # A batch reaching further back than the cache: rebuild rather
            # than guess. Indices must not shift.
            self._bounds = boundaries(self._scene_changes, 0, end)
            return self._bounds
        # THE LAST ELEMENT IS THE END SENTINEL, not a grid point —
        # `boundaries` always appends t_end last. Extending therefore
        # REPLACES it and resumes the grid from the point before it.
        # Mutated in place: `self._bounds[:-1]` copies a 992,889-element list
        # on every flush, which is most of what the cache was meant to save.
        out = self._bounds
        out.pop()                      # drop the old end sentinel
        prev = out[-1] if out else 0
        while end - prev > MAX_EPISODE_MS:
            prev += MAX_EPISODE_MS
            out.append(prev)
        out.append(end)
        return out

    def _ensure_index(self) -> None:
        if self._index_dirty:
            self.w.vector_index.rebuild_from(self.w.graph_store)
            self._index_dirty = False

    def ask(self, question: str) -> Answer:
        cue = parse_cue(question, clock=self.w.clock)
        # A QUESTION THAT NAMES A TIME IS A DIFFERENT QUESTION, and it is
        # routed before the graph is consulted at all. The graph holds no
        # time, so entering through it turns "what was I doing on August 3"
        # into a cosine match against node LABELS: the packed context for
        # that question led with ENTITY `Aug`, ENTITY `Day`, ENTITY `Year`,
        # and the model correctly refused. Measured 2026-08-20 on the real
        # store, against a day holding 29,250 claims.
        # THE SECOND QUESTION IN A ROW. "What did I do yesterday?" then "who
        # was I with?" — the second names no time, no subject and no place,
        # and everything it needs was said one sentence ago. Without this it
        # parses as a question about all of history and answers about the
        # wrong day. Only the WINDOW is inherited; see supplier/followup for
        # why carrying a subject would be dangerous.
        carried = carry_window(question, cue, self._last_asked,
                               self.w.clock.now_ms())
        if carried is not None:
            cue = replace(cue, window=carried)
        self._last_asked = (question, cue.window, self.w.clock.now_ms())

        guard_mode = "needle"
        if cue.window is not None:
            context = supply_window(self.w.claim_log, cue.window,
                                    self.w.answer_budget_tokens)
            render = render_window_prompt
            guard_mode = "synthesis"
        else:
            # A QUESTION THAT NAMES A THING the graph holds gets everything
            # attached to that thing, not a cosine ranking of labels. Without
            # this, "who is Juno?" packed `[ENTITY 1a2b3c] seen_as=Juno`
            # and the model refused — correctly, since a name restated
            # answers nothing. Measured 2026-08-23: three of three entity
            # questions returned "not in memory" over contexts that held the
            # answer.
            # AN ORDER IS NOT A FACT. "What do I use most" is answered by
            # counting, and nothing in the supplier had ever counted —
            # retrieval ranks by cosine similarity to the question, and
            # "most" is not a similarity. Tried before the dossier because
            # "who do I talk to most" names no subject.
            ranking_ctx = supply_ranking(question, self.w.graph_store,
                                         self.w.answer_budget_tokens)
            subject_ctx = None if ranking_ctx is not None else supply_subject(
                question, self.w.graph_store, self.w.answer_budget_tokens,
                claims_by_episode=self._claims_by_episode,
                claim_log=self.w.claim_log)
            if ranking_ctx is not None:
                context, render = ranking_ctx, render_ranking_prompt
                guard_mode = "synthesis"
            elif subject_ctx is not None:
                context, render = subject_ctx, render_dossier_prompt
                guard_mode = "synthesis"
            else:
                self._ensure_index()
                context = supply(cue, self.w.graph_store, self.w.claim_log,
                                 self.w.vector_index,
                                 self.w.answer_budget_tokens,
                                 claims_by_episode=self._claims_by_episode)
                render = render_prompt
        if not context.blocks:
            return Answer(text="not in memory", citations=(), abstained=True)
        # DON'T ASK THE MODEL TO SUMMARISE ONE FACT. See THIN_DOSSIER_LINES.
        # Only for synthesis: a needle question over two lines is a normal
        # needle question, and the answer is a sentence pulled out of them,
        # not the lines themselves.
        # WHAT THE FOUNDER SAID HIMSELF, PUT BACK IN FRONT OF THE MODEL.
        #
        # A correction is an OWNED claim at VERBATIM — the highest grade in
        # the store — and until 2026-08-25 the answer path could not see one.
        # It carries no app, no place and no frame, so it births no node, and
        # retrieval walks nodes. The most authoritative statement anywhere in
        # the store was the only one no question could reach, while the face
        # told the founder it would re-derive from it tonight.
        #
        # Added to the context rather than substituted into it: the law says
        # BOTH testimonies, neither deleted. See supplier/corrections.py.
        context = self._with_corrections(context, question)

        # A STATEMENT ABOUT ITS OWN PERCEPTION IS ALREADY THE ANSWER.
        #
        # MEASURED on a cold store 2026-08-25 — the day-one experience, which
        # nobody had ever actually run. "What did I do yesterday?" returned:
        #
        #   There is not enough here to summarise, so here is everything
        #   memory holds:
        #
        #   I have no perception at all for yesterday. No gap witness
        #   explains it, which means the eye was not running.
        #
        # The second half is right and well said. The first half is a wrapper
        # bolted onto a sentence that IS the answer, and on day one it is the
        # first thing anyone reads. A coverage block is prose this system
        # wrote about itself for a person; summarising it adds nothing and
        # framing it as raw evidence makes an honest sentence sound like a
        # malfunction.
        if context.blocks and all(getattr(b, "kind", "") == "coverage"
                                  for b in context.blocks):
            return Answer(text="\n".join(str(b.text) for b in context.blocks),
                          citations=(), abstained=False)

        if self._show_directly(context, guard_mode):
            return self._evidence_only(context, refused=False)
        prompt = render(context, question)
        self.w.spotlight.signal_query()
        try:
            with self.w.spotlight.acquire(self.w.answer_model, role="ANSWER",
                                          priority=QUERY_PRIORITY):
                raw = self.w.reasoner.complete(prompt)
        except SpotlightBusy as e:
            return Answer(text="busy: %s" % e, citations=(), abstained=True)

        # THE NEVER-LIE GUARD, in the product and not just the eval tool.
        # lie_catch measured that even the strengthened prompt lets one cited
        # fabrication through per ~21 unanswerable questions; the verification
        # pass caught it, reaching zero. So a cited answer is not returned
        # until its citation is checked — the product would rather say "not
        # in memory" than hand back a fabrication wearing a real id. The
        # guard only ever REMOVES an unsupported answer; it never invents one.
        answer = parse_answer(raw, context)
        # HOW MUCH TO TRUST EACH RECEIPT. The grade already travels with
        # every block; nothing ever showed it to a person, so an answer built
        # on a VLM's glance looked exactly like one built on text the machine
        # actually read. Attached here rather than re-derived at a face,
        # because the face does not have the context.
        self.last_citation_grades = {
            sid: int(b.grade) for b in context.blocks
            for sid in (b.source_ids or ())}
        # AN UNCITED ANSWER IS NOT AN ANSWER.
        #
        # MEASURED 2026-08-26, and it is a hole straight through the middle
        # of the never-lie promise. The guard's condition required
        # `answer.citations` — so a model that cited NOTHING skipped
        # verification entirely. One model call, no check, and this came
        # back as a normal answer with abstained=False:
        #
        #     "You had a dentist appointment and it went badly."
        #
        # against a store containing nothing but "kiln firing moved to
        # Thursday". Pure fabrication, served as memory.
        #
        # It survived because the prompt asks the model to cite and a
        # cooperative model does. "The prompt asks nicely" is precisely the
        # kind of guarantee the guard exists to BACKSTOP — a defence that
        # only works while the thing it defends against is behaving is not a
        # defence. Any model that drifts, any prompt edit, any smaller model
        # swapped in under Settings/Model choice, and the product starts
        # inventing a person's life with no check at all.
        #
        # A claim about someone's life with no receipt is the exact thing
        # this product refuses. So it is never served: a synthesis falls back
        # to the evidence it was given, and a needle abstains.
        if (self.w.verify_answers and not answer.abstained
                and not answer.citations and context.blocks):
            self._note_refusal("the answer cited nothing, so nothing about "
                               "it could be checked")
            if guard_mode == "synthesis":
                return self._evidence_only(context)
            return Answer(text="not in memory", citations=(), abstained=True)

        if self.w.verify_answers and answer.citations and not answer.abstained:
            blocks_by_id = {sid: b.text for b in context.blocks
                            for sid in b.source_ids}
            with self.w.spotlight.acquire(self.w.answer_model, role="VERIFY",
                                          priority=QUERY_PRIORITY):
                checked = bluff_guard(answer, question, blocks_by_id,
                                      self.w.reasoner, mode=guard_mode,
                                      note=self._note_refusal)
                if checked.abstained and guard_mode == "synthesis":
                    # THE GUARD REFUSED THE PROSE, NOT THE EVIDENCE.
                    #
                    # For a needle question "not in memory" is the right
                    # answer: there was nothing there. For a SUMMARY it is a
                    # lie of a different kind — the memory is holding a
                    # dossier with three sightings, two neighbours and three
                    # derived beliefs, and telling the founder it holds
                    # nothing, because one model call about a sentence went
                    # the other way. "Who is Juno?" answered on one run and
                    # refused on the next from the same graph, twice.
                    #
                    # So the fallback is to show what it has. This cannot
                    # fabricate: the text is the CONTEXT ITSELF, rendered, not
                    # generated — the same lines the model was given. The
                    # product gets less fluent, never less true.
                    answer = self._evidence_only(context)
                else:
                    answer = checked
        return answer

    def recall_history(self, question: str) -> List[Tuple[object, int]]:
        """'You used to think X' — retired beliefs matching the question, with
        when each died. A distinct surface from ask(): the live answer path
        speaks only current memory; this one speaks the moat's history, and
        never revives a retired belief into the live graph."""
        from tracemem.nodes.retention.recall import recall_retired
        terms = [w.strip(".,?!;:'\"").lower() for w in question.split()]
        return recall_retired(self.w.graph_store, terms)

    # -- the night: one call runs the whole marination economy --

    def night(self, reasoner=None, lease=None):
        """`lease` overrides the arbiter for THIS night only.

        The night watchman passes a proxy that reports "preempted" once a
        time slice is spent (app/night_watchman._SlicedLease), which is how a
        night becomes abandonable without run_night growing a second notion
        of when to stop. Default stays the real Spotlight, so every existing
        caller and test is unchanged.
        """
        from tracemem.nodes.marination.nightly import run_night
        self._ensure_index()
        report = run_night(
            self.w.graph_store, self.w.claim_log, self.w.token_ledger,
            lease if lease is not None else self.w.spotlight, self.w.clock,
            reasoner=reasoner if reasoner is not None else self.w.reasoner,
            new_episodes=self.episodes,
            claims_by_episode=self._claims_by_episode,
            routine_model=self.w.routine_model,
            embedder=self.w.embedder)
        self.w.vector_index.rebuild_from(self.w.graph_store)   # 05 §7
        return report

    # -- health: absence of signal IS the signal --

    def heartbeat(self, daemon: str) -> None:
        self._heartbeats[daemon] = self.w.clock.now_ms()

    def health(self) -> Dict[str, Tuple[str, int]]:
        now = self.w.clock.now_ms()
        return {name: ("DEAD" if now - t > HEARTBEAT_S * 1000 else "OK",
                       now - t)
                for name, t in self._heartbeats.items()}

    @property
    def episodes(self):
        return sorted(self._episodes.values(), key=lambda e: (e.t_range.start_ms, e.id))

    @property
    def claims_by_episode(self):
        return self._claims_by_episode
