"""OWNS: the seam from CAPTURED CLAIMS to a memory that can be asked.

THE GAP THIS CLOSES, found 2026-07-23 by reading what the daemon actually
does rather than what the map says it does.

`python -m tracemem --eye` runs this pipeline, in full:

    eye -> ClaimQueue.submit -> (owning thread) -> ClaimLog.append -> done

That is the whole of it. The daemon never constructs a Hub. So in the
shipped process:

  · the BINDER never runs — no episode is ever formed from a real claim
  · NODE CREATION never runs — no entity, app, place or topic is ever born
  · the NIGHT never runs — nothing is ever marinated, combined or pruned
  · the INFORMATION SUPPLIER has nothing to supply — the graph is empty

Three of the napkin's five execution boxes had no production caller at all.
Every one of them is implemented, tested, and green; each sits downstream
of a seam that nothing in the shipped daemon crossed. This is the
campaign's named disease — a gate with zero production callers — at the
scale of the architecture rather than a single function.

SCENE CHANGES, and why they are derived here. `Hub.post_scene_change` also
had no production caller, so even a wired Hub would have projected every
claim into ONE episode spanning all of recorded time. The eye does not
publish a "scene changed" event; what it publishes is claims, each stamped
with the app it was read from. The frontmost app changing IS the scene
change, and the binder already owns the hard part — `collapse_flicker`
refuses to cut on a brief excursion that returns, so alt-tabbing to Slack
for three seconds does not shatter an hour of writing into three moments.
This class derives the switch list and hands it to that tested machinery.
It does not invent a new segmentation rule; it feeds the existing one.

WHY BATCHED. `Hub.post_claims` re-embeds every node in the graph on every
call (05 §7's index refresh). Called once per claim from the drain loop
that is O(nodes) per claim, which on a real store is a laptop fire. Binding
does not need to be sub-second — a moment is minutes long — so claims
accumulate and bind on a slower beat.

WHY absorb() STILL APPENDS IMMEDIATELY. ClaimQueue documents its durability
window precisely: "a claim sitting in the queue when the process dies is
lost... bounded by the drain interval". If binding-batch replaced the
append, that window would silently widen from one tick to one flush and the
documented promise would quietly stop being true. So absorb() appends at
once and flush() binds later; `ClaimLog.append` is idempotent by
construction (T2, content-id primary key), so the re-append inside
`post_claims` is a no-op and costs one indexed lookup.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Callable, List, Optional

from tracemem.binder.boundary_constants import FLICKER_WINDOW_MS
from tracemem.binder.event_segmenter import collapse_flicker, switch_times

# A moment is minutes long; binding on a slower beat than capture costs
# nothing a user can perceive and saves an index rebuild per claim.
DEFAULT_FLUSH_CLAIMS = 64
DEFAULT_FLUSH_SECONDS = 10.0


class BrainNotServing(RuntimeError):
    """Raised to an asker when the owner thread never ran its job."""


class _Job:
    """One unit of store work to run on the owner thread."""

    __slots__ = ("fn", "done", "result", "error")

    def __init__(self, fn) -> None:
        self.fn = fn
        self.done = threading.Event()
        self.result = None
        self.error = None


class Brain:
    """Captured claims in one side, an answerable memory out the other.

    Owned by the daemon's thread. `absorb` runs on that thread (it is the
    ClaimQueue's sink); `ask` runs on the socket thread and takes the lock,
    because the graph must not be re-projected underneath a query.
    """

    def __init__(self, hub, store_append: Callable, now_ms: Callable[[], int],
                 flush_claims: int = DEFAULT_FLUSH_CLAIMS,
                 flush_seconds: float = DEFAULT_FLUSH_SECONDS,
                 helper_runtime=None) -> None:
        self._hub = hub
        self._append = store_append
        self._now_ms = now_ms
        self._flush_claims = flush_claims
        self._flush_ms = int(flush_seconds * 1000)
        self._pending: List = []
        self._last_flush_ms = now_ms()
        # (t_ms, app) of the last SETTLED context: a switch the flicker rule
        # can no longer collapse.  Never the raw last switch -- see
        # `_scene_changes_in`.
        self._seen_switch = None
        self._published = set()           # boundaries already given to the hub
        # Latest claim time already handed to the hub.  A scene change at or
        # behind it can only arrive as a late boundary, so none is derived
        # from there.
        self._frontier_ms = None
        self._lock = threading.RLock()
        # The hand-off: askers on other threads submit jobs the owner runs.
        self._jobs_cv = threading.Condition()
        self._jobs: List[_Job] = []
        self._owner = None                # the one thread that touches the store
        self.bound = 0                    # claims that have reached the binder
        self._last_digest = None          # the report a grade may refer back to
        # EVERY STATEMENT THIS BRAIN HAS ACTUALLY SHOWN SOMEONE, so a grade
        # can name it. Bounded: a daemon that runs for a year must not keep
        # every sentence it has ever uttered in memory. See `remember_answer`.
        self._gradeable = OrderedDict()   # line_id -> DigestLine
        self._helper_runtime = helper_runtime
        self._helper_replay = {"completed": 0, "produced": 0, "failed": 0}

    # -- the write side (daemon thread) ------------------------------------

    def absorb(self, claim) -> None:
        """ClaimQueue sink. Durable NOW; bound on the next flush.

        Also records the writer: whichever thread first hands a claim over
        owns the store from then on, and every other thread must go through
        the flush handshake instead of writing.
        """
        if self._owner is None:
            self._owner = threading.get_ident()
        self._append(claim)
        with self._lock:
            self._pending.append(claim)

    def backfill(self, claims, chunk: int = 256, progress=None) -> dict:
        """Bind claims that were ALREADY in the store when this process
        started. Returns {"bound": n, "episodes": n}.

        FOUND BY USING THE PRODUCT (2026-07-28). With the daemon finally
        running, `tools/ask.py --stats` on the founder's real store reported

            bound claims : 0
            episodes     : 0
            nodes        : 0

        against 604 claims captured on 2026-07-23. `absorb` binds what the
        eye hands over LIVE, and nothing had ever bound what was already on
        disk — so every question about anything captured before the current
        process start was answerable only with "not in memory", which is a
        true sentence and a useless product. A memory that forgets everything
        it recorded before breakfast is not a memory.

        DOES NOT APPEND. These claims are already durable; re-appending would
        be a no-op by content-id (T2) but would still walk the chain tip for
        each one, so it is both pointless and a write path nothing needs.

        Chunked because `Hub.post_claims` refreshes the node index per call
        (05 §7): one 604-claim call is one big index rebuild, which is fine,
        but a year of claims in one call is not. The chunk bound keeps the
        cost per call flat as the store grows.
        """
        total, episodes = 0, 0
        batch: List = []
        # `progress` is called after every chunk with the running total, so a
        # face asking "are you alive?" during a two-minute backfill can be
        # told how far along it is instead of timing out (N3).
        for claim in claims:
            batch.append(claim)
            if len(batch) >= chunk:
                r = self._bind_now(batch)
                total += r["bound"]
                episodes += r.get("episodes", 0)
                batch = []
                if progress is not None:
                    progress(total, episodes)
        if batch:
            r = self._bind_now(batch)
            total += r["bound"]
            episodes += r.get("episodes", 0)
        return {"bound": total, "episodes": episodes}

    def _bind_now(self, batch) -> dict:
        """Bind one batch through the same path flush() uses — scene changes
        first, then the claims — so backfilled memory is segmented by exactly
        the rules live memory is, and not by a second implementation."""
        with self._lock:
            report = self._post(batch, settle=True)
            return {"bound": len(batch),
                    "episodes": report.get("episodes", 0)}

    def _post(self, batch, settle: bool) -> dict:
        """Scene changes first, then claims: the one write path to the hub.

        With ``settle=False`` a final switch the flicker rule cannot decide
        yet is held back WITH every claim at or after it, so the boundary is
        published before anything past it is projected.  Returns the hub's
        report for the claims actually posted; held claims go back on the
        pending list in their original order.
        """
        cuts, hold_from = self._scene_changes_in(batch, settle=settle)
        if hold_from is not None:
            held = [c for c in batch if c.coord.t_ms >= hold_from]
            batch = [c for c in batch if c.coord.t_ms < hold_from]
            self._pending = held + self._pending
        for t_ms in cuts:
            self._hub.post_scene_change(t_ms)
        if not batch:
            return {"episodes": 0}
        report = self._hub.post_claims(batch)
        self.bound += len(batch)
        t_max = max(c.coord.t_ms for c in batch)
        if self._frontier_ms is None or t_max > self._frontier_ms:
            self._frontier_ms = t_max
        report["posted"] = len(batch)
        return report

    def due(self) -> bool:
        with self._lock:
            if not self._pending:
                return False
            return (len(self._pending) >= self._flush_claims
                    or self._now_ms() - self._last_flush_ms >= self._flush_ms)

    def flush(self, force: bool = False) -> dict:
        """Bind and birth everything pending. Safe to call on every tick."""
        with self._lock:
            if not self._pending or not (force or self.due()):
                return {"bound": 0}
            batch, self._pending = self._pending, []
            self._last_flush_ms = self._now_ms()

            report = self._post(batch, settle=force)
            return {"bound": report.get("posted", 0),
                    "episodes": report.get("episodes", 0)}

    def _scene_changes_in(self, batch, settle: bool = True):
        """Frontmost-app switches in this batch, brief excursions collapsed.
        Returns ``(fresh_cuts, hold_from_ms)``.

        The previous batch's last SETTLED context is carried in, so a switch
        that straddles a flush boundary is still one switch rather than the
        start of a new world -- otherwise episode boundaries would depend on
        how capture happened to be CHUNKED rather than on what the founder
        did.

        THREE WAYS THIS USED TO MINT LATE BOUNDARIES (found 2026-10-14 on the
        live store: 636 quarantined in a week, ~70 a day, every one needing
        an offline reconcile):

        1. The carry was the RAW last switch.  After A -> B -> A collapsed
           inside one batch, the carry was the return to A -- a time the
           flicker rule had just ruled not-a-boundary.  The next batch saw
           it as fresh and published it behind claims already projected.
           The carry is now the last item of the COLLAPSED list.
        2. A visual claim can arrive behind what is already bound: the macro
           eye understands a frame minutes after capture and stamps the
           frame's time.  The eye reported that moment's foreground when it
           happened; a late reading is not a new scene, so only claims past
           the bound frontier are read for switches.
        3. Flicker needs to see the return before it can rule.  A final
           switch younger than the window is undecided; when not settling,
           it is held back with every claim at or after it, so whichever way
           it is decided, its boundary is published before anything past it
           is projected.  ``settle=True`` (asks, stops, backfill) decides it
           as it stands.

        Already-published boundaries are remembered and never re-posted, so
        `flush()` is idempotent with respect to segmentation.
        """
        # A scene is something the eye saw on a frame.  Memory-scoped claims
        # also carry a CaptureCoord so every claim remains locatable, but its
        # ``app`` is provenance, not a foreground-window observation.  In
        # particular every system witness says app="trace".  Letting those
        # through made the first START/LOCKED/permission witness after each
        # restart look like an app switch, and because its timestamp could be
        # behind the latest projected screen frame it created a fresh
        # late-boundary reconciliation on every boot.  Founder answers,
        # owned files, and microphone claims are memory-scoped for the same
        # reason and must not split the visual day either.
        # ``scope=frame`` means precision-graded against one sensor sample;
        # it does not mean *visual*.  Ambient ASR is correctly frame-scoped
        # too, and labels its capture source app="ambient".  Only screen and
        # camera evidence can report a foreground visual scene.
        frontier = self._frontier_ms
        if self._seen_switch is not None:
            frontier = max(frontier if frontier is not None else
                           self._seen_switch[0], self._seen_switch[0])
        visual = [c for c in batch
                  if getattr(c, "scope", None) == "frame"
                  and getattr(getattr(c, "provenance", None), "modality", None)
                  is not None
                  and getattr(c.provenance.modality, "value", "")
                  in ("screen", "camera")
                  and getattr(c.coord.capture, "app", None)
                  and (frontier is None or c.coord.t_ms > frontier)]
        switches = []
        if self._seen_switch is not None:
            switches.append(self._seen_switch)
        for c in sorted(visual, key=lambda c: c.coord.t_ms):
            app = c.coord.capture.app
            if not switches or switches[-1][1] != app:
                switches.append((c.coord.t_ms, app))
        collapsed = collapse_flicker(switches)

        hold_from = None
        carried = 1 if self._seen_switch is not None else 0
        if not settle and len(collapsed) > carried and len(collapsed) >= 2:
            t_last = collapsed[-1][0]
            seen_to = max(c.coord.t_ms for c in visual) if visual else t_last
            if (seen_to - t_last < FLICKER_WINDOW_MS
                    and self._now_ms() - t_last < FLICKER_WINDOW_MS):
                hold_from = t_last
                collapsed = collapsed[:-1]
        if collapsed:
            self._seen_switch = collapsed[-1]

        fresh = []
        for t in switch_times(collapsed):
            if t not in self._published:
                self._published.add(t)
                fresh.append(t)
        return fresh, hold_from

    # -- the read side (any thread) ----------------------------------------
    #
    # THE ONE-OWNER LAW, generalised from writes to EVERYTHING (rewritten
    # 2026-07-24 after the first version corrupted itself under the sqlite
    # backend and spun a test suite for nine hours).
    #
    # The first version only handed the FLUSH to the owner thread and then
    # ran hub.ask() on the caller's thread. That was wrong twice over:
    #   · hub.ask READS the graph store, and sqlite connections are bound to
    #     the thread that created them — so under the sqlite graph store
    #     (the conformance backend, and a candidate for production) a
    #     socket-thread read raised ProgrammingError.
    #   · even with the in-memory graph store, the drill can read the CLAIM
    #     LOG (a sqlite db, always) for a time-hinted query whose episode is
    #     not in the batch map — so a "last week" question from the socket
    #     thread would hit the same error in PRODUCTION.
    # ClaimQueue already answered this for writes: one thread touches the
    # store, by construction. A read is no different. So the socket thread
    # touches NOTHING; it submits the whole question and waits for the owner
    # thread to compute the whole answer.

    def claim_owner(self) -> None:
        """Declare the calling thread the store owner. The daemon loop calls
        this once at startup; after it, every ask/stats/night from any other
        thread is handed here to run."""
        self._owner = threading.get_ident()

    def _on_owner(self, fn, timeout_s: float = 120.0):
        """Run fn() on the owner thread and return its result.

        Inline when the caller already IS the owner (or no owner has been
        declared — a single-threaded tool or unit test). Otherwise the job
        is queued and this blocks until `serve()` runs it. If the owner
        never serves within the timeout the job is abandoned and an error is
        raised — NEVER run on the caller's thread as a fallback, because that
        is exactly the cross-thread store touch this exists to prevent.
        """
        if self._owner is None or threading.get_ident() == self._owner:
            with self._lock:
                return fn()

        job = _Job(fn)
        with self._jobs_cv:
            self._jobs.append(job)
            self._jobs_cv.notify_all()
        if not job.done.wait(timeout=timeout_s):
            with self._jobs_cv:
                if job in self._jobs:
                    self._jobs.remove(job)
            where = self._owner_whereabouts()
            raise BrainNotServing(
                "the store owner did not serve this request within %ss — the "
                "daemon loop is not running or is wedged%s"
                % (timeout_s, (" — owner is in: " + where) if where else ""))
        if job.error is not None:
            raise job.error
        return job.result

    def _owner_whereabouts(self, depth: int = 24) -> str:
        """Where the owner thread is right now, innermost frame first.

        WITNESSED 2026-10-07: on the founder's store every question timed out
        at 120s while the screen was locked, and the timeout said only
        "wedged". A native sample showed Python reading files and nothing
        nameable; the faulthandler signal dump that would have named it
        segfaulted the daemon. Reading another thread's frame from this
        (waiting) thread is pure Python and safe, so a timeout now carries
        the answer to "wedged doing WHAT?" — the one fact needed to fix it.
        """
        import sys
        if self._owner is None:
            return ""
        try:
            frame = sys._current_frames().get(self._owner)
        except Exception:                                    # noqa: BLE001
            return ""
        stack = []
        while frame is not None and len(stack) < 40:
            code = frame.f_code
            stack.append("%s:%d %s" % (code.co_filename.rsplit("/", 2)[-1]
                                       if "/" in code.co_filename
                                       else code.co_filename,
                                       frame.f_lineno, code.co_name))
            frame = frame.f_back
        return " < ".join(stack[:depth])

    def serve(self) -> dict:
        """Owner thread ONLY. Runs every queued ask/stats/night on this
        thread, then binds on the beat. Called each daemon tick.

        The answer's model call (seconds) runs here, on the tick thread — so
        a question briefly delays the drain and the supervision beat. That is
        acceptable and, under the one-model law, unavoidable: answering holds
        the GPU, so heavy capture cannot run concurrently anyway.
        """
        while True:
            with self._jobs_cv:
                job = self._jobs.pop(0) if self._jobs else None
            if job is None:
                break
            try:
                with self._lock:
                    job.result = job.fn()
            except Exception as e:                                  # noqa: BLE001
                job.error = e
            finally:
                job.done.set()
        return self.flush()

    def _person_waiting(self) -> None:
        """Tell the night a person is waiting BEFORE queueing behind it.

        `signal_query` used to be raised inside hub.answer, i.e. on the owner
        thread, i.e. only after the night had handed the thread back: the
        flag the night polls for could never be set while it mattered
        (2026-10-15). Raised here, from the asking thread, the night's next
        poll point (or the next chunk of a generation, see Interruptible)
        yields. Machine polls (stats, volume) never call this, or a cockpit
        refreshing every few seconds would starve the night.
        """
        if self._owner is None or threading.get_ident() == self._owner:
            return
        spot = getattr(getattr(self._hub, "w", None), "spotlight", None)
        signal = getattr(spot, "signal_query", None)
        if signal is not None:
            signal()

    def ask(self, question: str):
        """Answer from everything bound. Flushes first so a question about
        something that happened a second ago can see it — on the OWNER
        thread, because both the flush and the read touch the store."""
        self._person_waiting()
        return self._on_owner(
            lambda: self._flush_then(lambda: self._hub.ask(question)))

    def night(self, reasoner=None, lease=None):
        return self._on_owner(
            lambda: self._flush_then(
                lambda: self._night_with_helpers(reasoner, lease)))

    def _night_with_helpers(self, reasoner, lease):
        if self._helper_runtime is not None:
            from tracemem.extraction.helper_replay import replay_pending
            replay = replay_pending(
                self._hub.w.claim_log, self._helper_runtime, self.absorb)
            for key, value in replay.items():
                self._helper_replay[key] += value
            # Claims appended by replay must bind before this same night
            # reasons over the graph, not one night later.
            self.flush(force=True)
        return self._hub.night(reasoner=reasoner, lease=lease)

    def digest(self, t0_ms: int, t1_ms: int):
        """Build the founder's report. Owner thread — it reads both stores.

        The rendered digest is REMEMBERED, because a grade arrives later as a
        line id and must resolve back to the exact statement it judged. A
        digest regenerated between reading and grading would still resolve,
        since DigestLine ids are content-derived rather than positional; the
        cache is a convenience, not the correctness mechanism.
        """
        from tracemem.app.digest import build_digest

        def _build():
            d = build_digest(self._hub.w.graph_store, self._hub.w.claim_log,
                             t0_ms, t1_ms)
            self._last_digest = d
            # Registered as well as kept, so digest lines and answers are
            # graded through ONE lookup rather than two that can disagree.
            for line in d.lines:
                self._remember(line)
            return d

        self._person_waiting()
        return self._on_owner(lambda: self._flush_then(_build))

    # How many shown statements stay gradeable. Generous enough to grade
    # something from earlier in a session; small enough that a daemon running
    # for a year does not hoard every sentence it has ever uttered.
    GRADEABLE_MEMORY = 200

    def _remember(self, line):
        """Keep a statement that was actually SHOWN, so it can be graded.

        The "actually shown" part is the whole guard. A grade must always
        name a statement the founder really saw — grading something he was
        never shown would put words in his mouth at the highest grade in the
        store, which is the one place this product can least afford them.
        """
        self._gradeable[line.id] = line
        self._gradeable.move_to_end(line.id)
        while len(self._gradeable) > self.GRADEABLE_MEMORY:
            self._gradeable.popitem(last=False)
        return line.id

    def remember_answer(self, question: str, answer) -> Optional[str]:
        """Make an answer gradeable, and return the id to grade it by.

        WHY THIS EXISTS. `Ask/'That's wrong'` is a recorded law that had no
        path to reach it. `grade()` resolved a line id against the last
        DIGEST only, so the founder could grade what the night reported and
        NOT the answer he had just read — the surface people actually use was
        the one surface with no way to say "no, that is wrong".

        An abstention is deliberately NOT gradeable. "not in memory" is a
        statement about the store, not about the founder's life; marking it
        wrong would write "the founder says this is WRONG: not in memory"
        into the store as VERBATIM evidence, which is a sentence about
        nothing that then outranks everything.
        """
        from tracemem.app.digest import ANSWER_SECTION, DigestLine

        text = str(getattr(answer, "text", "") or "").strip()
        if not text or getattr(answer, "abstained", False):
            return None
        # The QUESTION is part of the identity, not decoration: the same
        # sentence can be a right answer to one question and a wrong answer
        # to another, and a grade must not migrate between them.
        return self._remember(DigestLine(
            section=ANSWER_SECTION,
            text="%s — asked: %s" % (text, question.strip()),
            citations=tuple(getattr(answer, "citations", ()) or ())))

    def history(self, query: str = "", limit: int = 50) -> str:
        """What you have asked before, rendered. Runs on the owner thread,
        because the store's sqlite connection belongs to it."""
        from tracemem.supplier.history import find_asks, render

        def _read():
            return render(find_asks(self._hub.w.claim_log, query, limit))

        self._person_waiting()
        return self._on_owner(lambda: self._flush_then(_read))

    # WHAT THE HUB KNOWS ABOUT THE LAST ANSWER, REACHABLE FROM OUT HERE.
    #
    # THIS WAS A REAL BUG AND IT SHIPPED. `ask_socket` reads
    # `self._brain.last_refusal`, the value is set on the HUB, and Brain has
    # no delegation — so the socket read an attribute that did not exist and
    # every refusal reported no reason at all. Worse, the socket CLEARS it
    # before each question, which quietly created the attribute on the Brain
    # as "" and kept it there, so the failure looked exactly like "no
    # refusal happened".
    #
    # It passed its tests because those tests used a stub brain carrying the
    # attribute directly. They proved the socket reads a field; they could
    # not prove the real Brain has one. Properties, and a test through the
    # real object, are the fix.
    @property
    def last_refusal(self) -> str:
        return getattr(self._hub, "last_refusal", "")

    @last_refusal.setter
    def last_refusal(self, value) -> None:
        self._hub.last_refusal = value

    @property
    def last_citation_grades(self) -> dict:
        return dict(getattr(self._hub, "last_citation_grades", {}) or {})

    def questions(self):
        """What the memory cannot work out alone, or None if nothing."""
        from tracemem.supplier.questions import render

        self._person_waiting()
        return self._on_owner(
            lambda: render(self._hub.pending_questions,
                           self._hub.w.graph_store))

    def answer_question(self, qid: str, said: str):
        """Settle one of the memory's own questions, in your words.

        Recorded as evidence at VERBATIM and the night re-derives against it.
        Nothing is edited and no threshold moves — F-GRADE, the same law that
        governs a correction.
        """
        from tracemem.app.founder import answer_claim
        from tracemem.domain.question import QuestionStatus
        from tracemem.supplier.questions import find

        said = str(said or "").strip()
        if not said:
            raise ValueError("an empty answer settles nothing")

        def _record():
            q = find(self._hub.pending_questions, qid)
            if q is None:
                raise KeyError(
                    "no open question %r — a verdict must always name a "
                    "question that was actually put to you" % qid)
            claim = answer_claim(q.text, said, t_ms=self._now_ms(),
                                 question_id=str(q.id))
            self.absorb(claim)
            self.flush(force=True)
            # THE LIFECYCLE CLOSES. A question that stays QUEUED after being
            # answered gets asked again, which teaches a person that
            # answering does nothing.
            try:
                q.status = QuestionStatus.ANSWERED
            except Exception:                                # noqa: BLE001
                pass
            return claim

        return self._on_owner(_record)

    def morning(self, since_ms: int = 0, limit: int = 3):
        """What it would say unprompted, or None when it has nothing.

        None, never an empty page: a quiet night is not a degraded state and
        must not render as a blank panel that reads like a failure.
        """
        from tracemem.supplier.morning import morning as _morning

        self._person_waiting()
        return self._on_owner(
            lambda: _morning(self._hub.w.graph_store, since_ms, limit))

    def moment(self, cite: str) -> str:
        """The moment behind a citation, rendered. Owner thread: the store's
        sqlite connection belongs to it."""
        from tracemem.supplier.moment import supply_moment

        self._person_waiting()
        return self._on_owner(
            lambda: self._flush_then(
                lambda: supply_moment(self._hub.w.claim_log, cite,
                                      self._hub.w.graph_store)))

    def live_map(self, query: str = "", include_retired: bool = True,
                 limit: int = 160) -> dict:
        """The browsable graph projection. Owner-thread like every store read."""
        from tracemem.app.live_map import snapshot
        self._person_waiting()
        return self._on_owner(lambda: self._flush_then(
            lambda: snapshot(self._hub.w.graph_store, query,
                             include_retired, limit)))

    def volume(self, sample: int = 4000) -> dict:
        """How much of the recent store is re-read text. Read-only."""
        from tracemem.text.volume import noise_report
        return self._on_owner(
            lambda: noise_report(self._hub.w.claim_log, sample))

    def belief(self, node_id: str) -> dict:
        """Drill from a map node to relationships and exact raw evidence."""
        from tracemem.app.live_map import detail
        self._person_waiting()
        return self._on_owner(lambda: self._flush_then(
            lambda: detail(self._hub.w.graph_store,
                           self._hub.w.claim_log, node_id)))

    def record_ask(self, question: str, answer=None) -> None:
        """Write down that this was asked. Questions are memory too.

        What someone wondered on a Tuesday is a real fact about that Tuesday,
        and often a better index into the day than anything that was on
        screen. It is also what `Follow-ups` will resolve against and what
        search will find.

        The model's ANSWER is carried in `structured`, never in the claim's
        text — see app/founder.ask_claim for why that boundary is a safety
        property and not a formatting preference.
        """
        from tracemem.app.founder import ask_claim

        q = str(question or "").strip()
        if not q:
            return

        def _record():
            self.absorb(ask_claim(q, answer, t_ms=self._now_ms()))
            self.flush(force=True)

        self._on_owner(_record)

    def grade(self, line_id: str, verdict: str, correction: str = "",
              t_ms: Optional[int] = None):
        """Record the founder's judgement AS EVIDENCE (F-GRADE).

        Appends one OWNED claim at VERBATIM grade and nothing else: no node is
        edited, no threshold moves. The night then re-derives against the
        highest-graded statement in the store, which is how a correction takes
        effect without the brain ever learning to please.
        """
        from tracemem.app.founder import grade_claim

        def _record():
            line = self._gradeable.get(line_id)
            if line is None:
                # Fallback for a digest built before this brain started
                # registering lines. One lookup path is the goal; two that
                # agree is better than dropping a grade on the floor.
                digest = getattr(self, "_last_digest", None)
                line = digest.by_id(line_id) if digest is not None else None
            if line is None:
                raise KeyError(
                    "nothing shown with id %r — a verdict must always name a "
                    "statement that was actually put in front of you, or a "
                    "grade puts words in your mouth at the highest grade in "
                    "the store" % line_id)
            claim = grade_claim(line, verdict, correction,
                                t_ms=self._now_ms() if t_ms is None else t_ms)
            self.absorb(claim)
            self.flush(force=True)
            return claim

        return self._on_owner(_record)

    def stats(self) -> dict:
        return self._on_owner(self._stats)

    def prepare_index(self) -> None:
        """Build the complete retrieval door while the daemon is WAKING.

        The first vector build after every process start is mandatory state,
        not optional night reflection. Running it from the ten-second night
        slice made the daemon advertise itself as awake and then stop serving
        every request for minutes. The entrypoint invokes this on the owner
        thread before opening the waking gate, so stats remain responsive and
        no question can receive a false absence from a partial index.
        """
        self._hub._ensure_index()

    def _flush_then(self, fn):
        self.flush(force=True)
        return fn()

    def _stats(self) -> dict:
        g = self._hub.w.graph_store
        return {
            "bound": self.bound,
            "pending": len(self._pending),
            "episodes": len(self._hub.episodes),
            "nodes": len(g.nodes),
            "helper_replay": dict(self._helper_replay),
        }
