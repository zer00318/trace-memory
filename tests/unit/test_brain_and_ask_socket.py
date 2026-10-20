"""THE SPINE: captured claim -> bound memory -> answered question.

Until 2026-07-23 the shipped daemon's whole pipeline was

    eye -> queue -> ClaimLog.append -> (nothing)

so the binder, node creation and the supplier — three of the napkin's five
execution boxes — had no production caller at all. Every one was
implemented and green. Each sat downstream of a seam nothing crossed.

These tests exercise the seam itself, on fakes, with no hardware: if they
pass, a claim that enters the daemon can be asked about.
"""
from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import pytest

from tests.fakes import ContextAnswerReasoner, HashEmbedder
from tests.helpers import make_claim
from tracemem.app.ask_socket import AskSocket
from tracemem.app.brain import Brain
from tracemem.app.wiring import build

MIN = 60_000


class _Clock:
    def __init__(self, t=0):
        self.t = t

    def now_ms(self):
        return self.t


def _brain(clock=None, **kw):
    clock = clock or _Clock(10 * MIN)
    hub = build({"embedder": HashEmbedder(), "reasoner": ContextAnswerReasoner(),
                 "clock": clock})
    return Brain(hub, hub.w.claim_log.append, clock.now_ms, **kw), hub, clock


def _feed(brain, items):
    """items: (t_ms, app, text)"""
    for t_ms, app, text in items:
        brain.absorb(make_claim(text=text, t_ms=t_ms, app=app,
                                frame="f-%d" % t_ms))


A_DAY = [
    (10 * MIN, "Calendar", "kiln firing rescheduled to thursday afternoon"),
    (11 * MIN, "Calendar", "pottery studio booking confirmation"),
    (20 * MIN, "Safari", "hippocampal replay compresses experience twentyfold"),
    (21 * MIN, "Safari", "the paper argues sleep prunes weak synapses"),
    (30 * MIN, "Mail", "invoice from the glaze supplier due on the ninth"),
]


# -- the brain -------------------------------------------------------------

def test_a_captured_claim_becomes_an_answerable_memory():
    """The whole point. Before this seam existed the answer was always
    'not in memory', because the graph was always empty."""
    brain, hub, _ = _brain()
    _feed(brain, A_DAY)
    brain.flush(force=True)

    assert brain.stats()["episodes"] >= 2, "the day never segmented"
    assert brain.stats()["nodes"] > 0, "no node was ever born from a real claim"

    ans = brain.ask("when was the kiln firing rescheduled to?")
    assert not ans.abstained, "the memory could not answer about its own claim"
    assert "thursday" in ans.text.lower()


def test_claims_are_durable_before_they_are_bound():
    """absorb() appends IMMEDIATELY; binding happens later in a batch.

    If binding replaced the append, ClaimQueue's documented durability
    window ('bounded by the drain interval') would silently widen to the
    flush interval — a promise quietly stopping being true is exactly the
    failure this codebase keeps finding.
    """
    brain, hub, _ = _brain(flush_claims=1000, flush_seconds=1e6)
    _feed(brain, A_DAY)
    assert brain.stats()["pending"] == 5, "nothing should have bound yet"
    assert hub.w.claim_log.count() == 5, \
        "claims must be on disk before they are bound, not after"


def test_binding_is_batched_not_per_claim():
    # The clock sits after the claims, as it does live: a final switch
    # younger than the flicker window is (correctly) held back until the
    # window can rule on it, and that is not what this test is about.
    brain, hub, clock = _brain(_Clock(40 * MIN), flush_claims=3,
                               flush_seconds=1e6)
    _feed(brain, A_DAY[:2])
    assert brain.flush()["bound"] == 0, "flushed before the batch was full"
    _feed(brain, A_DAY[2:3])
    assert brain.flush()["bound"] == 3, "a full batch must bind"


def test_a_slow_trickle_still_binds_on_the_clock():
    """Batch-by-count alone would leave a quiet afternoon unbound forever."""
    brain, hub, clock = _brain(flush_claims=1000, flush_seconds=10)
    _feed(brain, A_DAY[:1])
    assert brain.flush()["bound"] == 0
    clock.t += 11_000
    assert brain.flush()["bound"] == 1


def test_ask_sees_what_was_captured_one_second_ago():
    """A question about something that just happened must not miss it
    because the flush beat had not come round yet — the most recent claims
    are exactly the ones a person is most likely to ask about."""
    brain, hub, _ = _brain(flush_claims=1000, flush_seconds=1e6)
    _feed(brain, A_DAY)
    assert brain.stats()["pending"] == 5      # nothing bound on the normal beat
    ans = brain.ask("when was the kiln firing rescheduled to?")
    assert not ans.abstained and "thursday" in ans.text.lower()


def test_asking_from_another_thread_never_touches_the_store_from_it(tmp_path):
    """THE ONE-OWNER LAW, for reads as well as writes.

    A first version of the ask path handed only the FLUSH to the owner and
    ran hub.ask() on the caller's thread. hub.ask READS the store, and
    sqlite connections are bound to their creating thread — so under the
    sqlite backend (and, for a time-hinted query, the always-sqlite CLAIM
    LOG in production) it raised ProgrammingError from the socket thread and
    briefly corrupted enough state to spin a suite for nine hours.

    Now the whole question is handed to the owner. This test proves it by
    running the store on a dedicated owner thread — the topology the daemon
    uses — and asking from OUTSIDE it, on a real on-disk (sqlite) store, so
    the cross-thread touch would raise if it happened.
    """
    with _OwnerLoop(seed=A_DAY, store_path=str(tmp_path / "c.db"),
                    bind_seed=False, flush_claims=1000,
                    flush_seconds=1e6) as (brain, _hub):
        # stats() is itself handed to the owner; nothing bound yet.
        assert brain.stats()["pending"] == 5

        ans = brain.ask("when was the kiln firing rescheduled to?")
        assert not ans.abstained, "the hand-off lost the answer"
        assert "thursday" in ans.text.lower()

        # the ask flushed ON THE OWNER THREAD — the seed is now bound, and
        # no ProgrammingError was raised doing it.
        assert brain.stats()["pending"] == 0
        assert brain.stats()["bound"] == 5


def test_a_brief_excursion_does_not_shatter_a_moment():
    """The frontmost app IS the scene change, but alt-tabbing to Slack for
    three seconds is not a new moment. The binder's flicker collapse owns
    this rule; this test proves the Brain actually feeds it."""
    brain, hub, _ = _brain()
    _feed(brain, [
        (10 * MIN, "VS Code", "def flush_wal(conn)"),
        (10 * MIN + 1000, "VS Code", "benchmark shows fsync costs 4ms"),
        (10 * MIN + 2000, "Slack", "ping from Priya"),        # 3s excursion...
        (10 * MIN + 5000, "VS Code", "sqlite WAL checkpoint notes"),  # ...and back
    ])
    brain.flush(force=True)
    assert brain.stats()["episodes"] == 1, (
        "a three-second detour to Slack split the moment into %d"
        % brain.stats()["episodes"])


def test_segmentation_does_not_depend_on_how_capture_was_chunked():
    """Episode boundaries must come from what the founder did, not from
    where the flush beat happened to fall."""
    def run(chunks):
        brain, hub, _ = _brain(flush_claims=1000, flush_seconds=1e6)
        for chunk in chunks:
            _feed(brain, chunk)
            brain.flush(force=True)
        return brain.stats()["episodes"]

    whole = run([A_DAY])
    split = run([A_DAY[:2], A_DAY[2:4], A_DAY[4:]])
    assert whole == split, (
        "the same day segmented into %d episodes in one batch and %d across "
        "three" % (whole, split))


def test_memory_scoped_claims_never_manufacture_visual_scene_changes():
    """Restart witnesses, founder words, owned files and ear text are memory,
    not foreground-window observations.

    Every witness carries app="trace" for provenance.  Treating that as a
    frontmost-app switch created a late boundary on every production boot,
    immediately making a just-reconciled graph require reconciliation again.
    """
    from tracemem.app.witness import WitnessKind, witness
    from tracemem.domain import Modality

    brain, hub, _ = _brain()
    _feed(brain, [(20 * MIN, "Editor", "the visible document")])
    brain.flush(force=True)
    before = list(hub._scene_changes)

    brain.absorb(witness(WitnessKind.START, t_ms=10 * MIN))
    # The ambient ear is frame-scoped (one audio sample) and carries an app
    # label for provenance, but that label is not a frontmost window.
    brain.absorb(make_claim("someone said hello", t_ms=11 * MIN,
                            app="ambient", modality=Modality.MIC,
                            perceiver="asr", scope="frame"))
    brain.flush(force=True)

    assert hub._scene_changes == before
    assert not getattr(hub.w.graph_store, "unresolved_boundaries", lambda: [])()


def _late(hub):
    return list(getattr(hub.w.graph_store, "unresolved_boundaries",
                        lambda: [])())


def test_a_collapsed_detour_is_not_re_emitted_by_the_next_batch():
    """FOUND ON THE LIVE STORE (2026-10-14): 636 late boundaries in a week,
    ~70 a day, each one needing an offline reconcile.

    The carried context was the RAW last switch.  After A -> B -> A collapsed
    inside one batch, the carry was the return to A, a timestamp the flicker
    rule had just declared not-a-boundary.  The next batch saw it as fresh,
    published it, and because that batch's claims had already been
    projected past it the hub quarantined it as a late boundary.  The
    boundary was both late and false.
    """
    brain, hub, _ = _brain(flush_claims=1000, flush_seconds=1e6)
    _feed(brain, [
        (10 * MIN, "VS Code", "def flush_wal(conn)"),
        (10 * MIN + 2000, "Slack", "ping from Priya"),
        (10 * MIN + 4000, "VS Code", "sqlite WAL checkpoint notes"),
        (10 * MIN + 9000, "VS Code", "fsync costs 4ms"),
    ])
    brain.flush(force=True)
    _feed(brain, [(11 * MIN, "VS Code", "still writing the same function")])
    brain.flush(force=True)

    assert _late(hub) == []
    assert 10 * MIN + 4000 not in hub._scene_changes


def test_a_detour_straddling_a_flush_still_collapses():
    """The flicker rule needs to see the return before it can decide.  A
    detour that began in one batch and returned in the next must collapse
    exactly as it would have in one batch."""
    def run(chunks):
        # the live beat: an unforced flush, the way the drain loop calls it
        brain, hub, _ = _brain(flush_claims=1, flush_seconds=1e6)
        for chunk in chunks:
            _feed(brain, chunk)
            brain.flush()
        brain.flush(force=True)
        return brain.stats()["episodes"], _late(hub)

    day = [
        (10 * MIN, "VS Code", "def flush_wal(conn)"),
        (10 * MIN + 1000, "VS Code", "benchmark shows fsync costs 4ms"),
        (10 * MIN + 2000, "Slack", "ping from Priya"),
        (10 * MIN + 4000, "VS Code", "sqlite WAL checkpoint notes"),
    ]
    whole = run([day])
    split = run([day[:3], day[3:]])
    assert whole == split == (1, [])


def test_a_visual_claim_behind_the_frontier_is_not_a_new_scene():
    """The macro eye understands frames minutes after they were captured.
    Its claims are screen-modality and frame-scoped, so they used to enter
    the switch list at their old timestamps, out of order with the carried
    context, and every one became a late boundary.  The eye already
    reported the foreground at that moment; a late reading of the same
    frame is not a scene change."""
    brain, hub, _ = _brain(flush_claims=1000, flush_seconds=1e6)
    _feed(brain, [
        (10 * MIN, "VS Code", "def flush_wal(conn)"),
        (20 * MIN, "Safari", "hippocampal replay"),
    ])
    brain.flush(force=True)
    # understood late, stamped with its frame's time and app
    _feed(brain, [(12 * MIN, "Preview", "a diagram of a kiln"),
                  (21 * MIN, "Safari", "the paper argues sleep prunes")])
    brain.flush(force=True)
    assert _late(hub) == []


def test_flush_is_idempotent():
    brain, hub, _ = _brain()
    _feed(brain, A_DAY)
    first = brain.flush(force=True)
    again = brain.flush(force=True)
    assert first["bound"] == 5 and again["bound"] == 0


# -- the socket ------------------------------------------------------------

class _Chat:
    """A face, in eight lines."""

    def __init__(self, path):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.settimeout(10)
        self.s.connect(str(path))

    def send(self, **req):
        self.s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            buf += self.s.recv(4096)
        return json.loads(buf.split(b"\n", 1)[0])

    def close(self):
        self.s.close()


@pytest.fixture
def sockdir():
    """A SHORT directory. pytest's tmp_path is ~90 bytes before the filename,
    and sockaddr_un.sun_path is 104 — so the standard fixture cannot hold a
    socket at all. Learned by bind() refusing it."""
    import shutil
    import tempfile
    d = tempfile.mkdtemp(prefix="/tmp/tm-")
    try:
        yield Path(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


class _OwnerLoop:
    """A Brain run the way the daemon runs it: ONE thread creates the store,
    seeds it, owns it, and serves every queued job. Asker threads (the test,
    a socket handler) talk to the returned brain from outside.

    This topology is not a test nicety — it is mandatory. sqlite connections
    are bound to their creating thread, so a store created on the main thread
    cannot be asked from another. A test that owns the store on main and asks
    from a second thread CANNOT exist under a thread-bound backend; it can
    only look like it works under the in-memory one, which is exactly how the
    original defect hid.
    """

    def __init__(self, seed=(), store_path=":memory:", bind_seed=True,
                 **brain_kw):
        self._seed = list(seed)
        self._store_path = store_path
        self._bind_seed = bind_seed
        self._brain_kw = brain_kw
        self._ready = threading.Event()
        self._stop = threading.Event()
        self.brain = None
        self.hub = None
        self._err = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        try:
            from tracemem.text.claim_log import ClaimLog
            clock = _Clock(10 * MIN)
            log = ClaimLog(self._store_path)
            hub = build({"claim_log": log, "embedder": HashEmbedder(),
                         "reasoner": ContextAnswerReasoner(), "clock": clock})
            brain = Brain(hub, log.append, clock.now_ms, **self._brain_kw)
            for t_ms, app, text in self._seed:
                brain.absorb(make_claim(text=text, t_ms=t_ms, app=app,
                                        frame="f-%d" % t_ms))
            if self._bind_seed:
                brain.flush(force=True)
            brain.claim_owner()
            self.brain, self.hub = brain, hub
        except Exception as e:                                   # noqa: BLE001
            self._err = e
        finally:
            self._ready.set()
        while not self._stop.is_set():
            if self.brain is not None:
                self.brain.serve()
            time.sleep(0.01)
        if self.brain is not None:
            self.brain.serve()          # final drain of any queued job

    def __enter__(self):
        self._thread.start()
        self._ready.wait(30)
        if self._err is not None:
            raise self._err
        return self.brain, self.hub

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=10)


@pytest.fixture
def served(sockdir):
    loop = _OwnerLoop(seed=A_DAY, bind_seed=True)
    brain, hub = loop.__enter__()
    sock = AskSocket(brain, path=sockdir / "ask.sock")
    sock.start()
    try:
        yield sock, brain
    finally:
        sock.stop()
        loop.__exit__(None, None, None)


def test_a_face_can_ask_and_get_a_cited_answer(served):
    sock, _ = served
    c = _Chat(sock._path)
    try:
        r = c.send(op="ask", question="when was the kiln firing rescheduled to?")
        assert r["ok"] and not r["abstained"]
        assert "thursday" in r["text"].lower()
        assert r["citations"], "an answer without citations is a confabulation"
    finally:
        c.close()


def test_the_face_is_told_honestly_when_memory_has_nothing(served):
    sock, _ = served
    c = _Chat(sock._path)
    try:
        r = c.send(op="ask", question="what did zzyzx close at?")
        assert r["ok"] and r["abstained"] is True
    finally:
        c.close()


def test_stats_answer_without_a_model(served):
    sock, _ = served
    c = _Chat(sock._path)
    try:
        r = c.send(op="stats")
        assert r["ok"] and r["bound"] == 5 and r["nodes"] > 0
    finally:
        c.close()


def test_the_socket_is_private_to_this_user(served):
    """0600. A memory of someone's life must not be readable by every
    account on the machine — and the mode is set BEFORE listen(), so there
    is no window in which it exists at the process umask."""
    sock, _ = served
    import stat
    mode = stat.S_IMODE(sock._path.stat().st_mode)
    assert mode == 0o600, "socket mode is %o, not 0600" % mode


def test_a_malformed_request_is_answered_not_fatal(served):
    sock, _ = served
    c = _Chat(sock._path)
    try:
        c.s.sendall(b"this is not json\n")
        buf = b""
        while b"\n" not in buf:
            buf += c.s.recv(4096)
        assert json.loads(buf.split(b"\n", 1)[0])["ok"] is False
        # and the server is still alive
        assert c.send(op="stats")["ok"] is True
    finally:
        c.close()


def test_a_failing_answer_never_kills_the_daemon(sockdir):
    """The daemon outlives a bad question. A face gets an error object; the
    capture loop keeps running, because losing perception to a query is a
    trade this product must never make."""
    class Exploding:
        def stats(self):
            return {"bound": 0}

        def ask(self, q):
            raise RuntimeError("model wedged")

    sock = AskSocket(Exploding(), path=sockdir / "ask.sock")
    sock.start()
    try:
        c = _Chat(sock._path)
        r = c.send(op="ask", question="anything")
        assert r["ok"] is False and "model wedged" in r["error"]
        assert c.send(op="stats")["ok"] is True      # still serving
        c.close()
    finally:
        sock.stop()


def test_two_faces_never_answer_at_once(sockdir):
    """The one-model law. Two questions in flight would put two models on
    the Metal GPU, whose documented failure is a SILENT OOM — no exception,
    just degraded output nobody attributes to the right cause."""
    overlap = {"max": 0, "now": 0}
    guard = threading.Lock()

    class Slow:
        def stats(self):
            return {"bound": 0}

        def ask(self, q):
            with guard:
                overlap["now"] += 1
                overlap["max"] = max(overlap["max"], overlap["now"])
            time.sleep(0.15)
            with guard:
                overlap["now"] -= 1
            return type("A", (), {"text": "ok", "abstained": False,
                                  "citations": ()})()

    sock = AskSocket(Slow(), path=sockdir / "ask.sock")
    sock.start()
    try:
        def askers():
            c = _Chat(sock._path)
            c.send(op="ask", question="q")
            c.close()

        threads = [threading.Thread(target=askers) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        assert overlap["max"] == 1, (
            "%d answers ran concurrently — the one-model law is not held by "
            "the socket" % overlap["max"])
    finally:
        sock.stop()


def test_the_socket_file_is_gone_after_stop(sockdir):
    brain, _, _ = _brain()
    sock = AskSocket(brain, path=sockdir / "ask.sock")
    p = sock.start()
    from pathlib import Path
    assert Path(p).exists()
    sock.stop()
    assert not Path(p).exists(), "a stale socket makes the next bind() fail"


# -- FOUND BY USING THE PRODUCT (2026-07-28) ------------------------------
def test_backfill_binds_what_was_already_on_disk():
    """THE DAEMON STARTED WITH AN EMPTY MIND ON A FULL STORE.

    `tools/ask.py --stats` against the founder's real store reported
    0 bound / 0 episodes / 0 nodes with 604 real claims sitting in it,
    because absorb() only ever bound what the eye handed over LIVE. Every
    question about anything captured before the current process start
    answered "not in memory" — true, and a useless product. A memory that
    forgets everything it recorded before breakfast is not a memory.
    """
    brain, hub, clock = _brain()
    existing = [make_claim("already here %d" % i, t_ms=1000 + i,
                           frame="f%d" % i) for i in range(5)]
    report = brain.backfill(existing)
    assert report["bound"] == 5
    assert brain.stats()["bound"] == 5


def test_backfilling_a_claim_already_on_disk_does_not_duplicate_it():
    """Backfill routes through Hub.post_claims, which appends — so the
    property that matters is IDEMPOTENCE, not abstention. T2 (content-id
    primary key, INSERT OR IGNORE) is what makes re-binding a store safe to
    do on every single boot. Without it, restarting the daemon twice would
    double the founder's memory.

    (This test first asserted 'backfill never appends'. It does append, via
    the hub, and idempotently — the assertion was corrected to the property
    that is actually true rather than the one I assumed.)"""
    brain, hub, clock = _brain()
    claim = make_claim("on disk", t_ms=1, frame="f1")
    hub.w.claim_log.append(claim)
    before = hub.w.claim_log.count()

    brain.backfill([claim])
    brain.backfill([claim])            # and again, as a restart would

    assert hub.w.claim_log.count() == before, "backfill duplicated a claim"


def test_backfilled_memory_is_askable():
    """The whole point: a question about what was captured BEFORE this
    process started must reach an answer."""
    brain, hub, clock = _brain()
    brain.backfill([make_claim("the kiln fired at 1200 degrees",
                               t_ms=1000, frame="f1")])
    answer = brain.ask("kiln")
    assert answer is not None


def test_backfill_of_an_empty_store_is_a_no_op():
    brain, hub, clock = _brain()
    assert brain.backfill([])["bound"] == 0


# ---- the counter that could not report a death ----------------------------
#
# FOUND ON THE LIVE DAEMON 2026-08-10. Capture stopped on 5 August. For the
# five days after, `./trace status` printed:
#
#     bound claims : 612482
#     episodes     : 160
#     nodes        : 764
#
# All true, all cumulative, and therefore all incapable of ever going down.
# The store went on growing the whole time on `system:witness` heartbeats —
# roughly one every seventy seconds, 1,327 bytes each — so even the file's
# mtime said "alive". The founder had no number in front of him that could
# have said otherwise.

def _witness_and_sense_store(tmpdir):
    from tracemem.text.claim_log import ClaimLog
    log = ClaimLog(str(Path(tmpdir) / "s.sqlite3"))
    log.append(make_claim(text="a real sighting", t_ms=1_000_000,
                          bidder="peripheral:change"))
    # The blind daemon's own heartbeats, written LATER than the last sighting.
    for i in range(5):
        log.append(make_claim(text="i am alive", t_ms=2_000_000 + i * 70_000,
                              bidder="system:witness"))
    return log


class _StoreOnly:
    """Just enough brain for the reading under test — it reads the store."""

    def __init__(self, store):
        self._store = store


def test_last_capture_ignores_the_daemons_own_heartbeats(sockdir):
    """THE ORACLE. A sense died; the daemon kept writing about itself. The
    'last seen' reading must date the SENSE, not the self-report — otherwise
    a blind eye reports a capture seventy seconds ago, forever."""
    log = _witness_and_sense_store(sockdir)
    sock = AskSocket(_StoreOnly(log), path=sockdir / "unused.sock")

    assert sock._last_capture_ms() == 1_000_000, (
        "the witness heartbeats were counted as capture — this is exactly "
        "the reading that made five blind days look healthy")


def test_last_capture_is_none_when_nothing_was_ever_seen(sockdir):
    """A store holding only heartbeats has never seen anything, and must say
    so rather than dating itself from its own noise."""
    from tracemem.text.claim_log import ClaimLog
    log = ClaimLog(str(Path(sockdir) / "empty.sqlite3"))
    log.append(make_claim(text="i am alive", t_ms=5_000,
                          bidder="system:witness"))
    sock = AskSocket(_StoreOnly(log), path=sockdir / "unused2.sock")

    assert sock._last_capture_ms() is None


def test_last_capture_survives_a_store_it_cannot_read(sockdir):
    """A reading is not a law. If the store cannot be queried, status must
    still print — a status command that crashes is a status command nobody
    runs when it matters."""
    class _NoDb:
        _store = object()

    sock = AskSocket(_NoDb(), path=sockdir / "unused3.sock")
    assert sock._last_capture_ms() is None


def test_last_capture_is_always_reported_even_when_it_is_none(sockdir):
    """The key must exist whether or not a sense ever fired. If it appears
    only on success, a face cannot tell 'this daemon is too old to know' from
    'nothing was ever seen' — and it will print one of them wrongly, with
    confidence, which is the exact failure this reading exists to end."""
    from tracemem.text.claim_log import ClaimLog
    log = ClaimLog(str(Path(sockdir) / "witness_only.sqlite3"))
    log.append(make_claim(text="i am alive", t_ms=5_000,
                          bidder="system:witness"))

    class _Loop:
        _store = log

        def stats(self):
            return {"bound": 1, "pending": 0, "episodes": 0, "nodes": 0}

    sock = AskSocket(_Loop(), path=sockdir / "unused4.sock")
    out = sock._handle(json.dumps({"op": "stats"}).encode())

    assert "last_capture_ms" in out, (
        "absence and None were collapsed into the same wire state")
    assert out["last_capture_ms"] is None


def test_last_seen_reads_the_store_on_a_REAL_brain_not_a_stand_in(sockdir):
    """THE BUG THE STAND-IN HID.

    Every other test here hands AskSocket a `_StoreOnly` fake that exposes
    `_store`, so the old `getattr(brain, "_store")` lookup always worked in
    the suite. The real `Brain` exposes `_append` and `_hub` and NEITHER of
    those names — so on the founder's daemon the lookup returned None and
    `last seen` printed "never — this store holds no perceptions" about a
    store holding 471,503 of them.

    That is the single reading added on 2026-08-10 so that a dead eye could
    be seen at all. A status that cannot tell a full store from an empty one
    is the exact failure it was built to end, so this test asserts against
    the real object.
    """
    from tracemem.app.brain import Brain

    log = _witness_and_sense_store(sockdir)

    class _NoHub:
        def post_claims(self, claims):
            return {"episodes": 0}

    brain = Brain(_NoHub(), log.append, lambda: 2_000_000)
    sock = AskSocket(brain, path=sockdir / "unused2.sock", claim_log=log)
    assert sock._last_capture_ms() == 1_000_000


def test_a_dying_daemon_does_not_unlink_a_live_one_s_socket(sockdir):
    """THE HANDOVER RACE, witnessed 2026-08-20.

    Restarting the daemon — here, taking it back from launchd — runs two
    shutdowns and one bind in the same second. The new daemon created the
    socket; the OLD daemon's `stop()` then unlinked the PATH; and the
    survivor was left listening on a socket nothing could find. The log said
    "ask socket listening" and `./trace status` said "no daemon is
    listening", and both were true about different files.
    """
    path = sockdir / "handover.sock"
    old = AskSocket(_StoreOnly(None), path=path)
    old.start()

    new = AskSocket(_StoreOnly(None), path=path)
    new.start()                      # rebinds the path; this file is now NEW's
    assert path.exists()

    old.stop()                       # the outgoing daemon tidies up
    assert path.exists(), "the dying daemon removed the live daemon's socket"

    new.stop()
    assert not path.exists()


def test_a_refusal_reason_reaches_the_face(sockdir):
    """Why the guard refused travels out with the answer.

    Not for the founder to read — "the block does not support the question"
    is the checker talking to its author. It travels so the refusal rate can
    be reported as WHICH refusals happened rather than only how many, and so
    the next investigation starts from a sentence instead of a re-run with an
    instrumented checker.
    """
    class Refusing:
        last_refusal = ""

        def stats(self):
            return {"bound": 0}

        def ask(self, q):
            from tracemem.llm.answer import Answer
            self.last_refusal = "[abc123] Hana is your sister."
            return Answer(text="not in memory", citations=(), abstained=True)

    sock = AskSocket(Refusing(), path=sockdir / "ask.sock")
    sock.start()
    try:
        c = _Chat(sock._path)
        r = c.send(op="ask", question="who is Hana?")
        assert r["ok"] is True
        assert r["refused_because"] == "[abc123] Hana is your sister."
        c.close()
    finally:
        sock.stop()


def test_a_reason_never_outlives_its_question(sockdir):
    """The dangerous version of this feature is a stale reason.

    A leftover string attached to the NEXT question is a false explanation
    for an answer that was never refused — worse than no explanation, because
    it would be believed and chased. So the field is cleared before every
    question, not after.
    """
    class Once:
        last_refusal = ""

        def __init__(self):
            self.n = 0

        def stats(self):
            return {"bound": 0}

        def ask(self, q):
            from tracemem.llm.answer import Answer
            self.n += 1
            if self.n == 1:
                self.last_refusal = "[abc123] made up a sister"
                return Answer(text="not in memory", citations=(),
                              abstained=True)
            # Second question answers cleanly and sets nothing. If the socket
            # cleared only on refusal, the first reason would still be here.
            return Answer(text="You used Brave. [xyz789]",
                          citations=("xyz789",), abstained=False)

    sock = AskSocket(Once(), path=sockdir / "ask.sock")
    sock.start()
    try:
        c = _Chat(sock._path)
        assert c.send(op="ask", question="who is Hana?")["refused_because"]
        second = c.send(op="ask", question="what did I do yesterday?")
        assert second["abstained"] is False
        assert second["refused_because"] == ""
        c.close()
    finally:
        sock.stop()


def test_a_brain_without_the_field_still_answers(sockdir):
    """An older brain object has no `last_refusal`. Reading it must not turn
    a working answer into an error."""
    class Old:
        __slots__ = ()

        def stats(self):
            return {"bound": 0}

        def ask(self, q):
            from tracemem.llm.answer import Answer
            return Answer(text="fine [a1]", citations=("a1",), abstained=False)

    sock = AskSocket(Old(), path=sockdir / "ask.sock")
    sock.start()
    try:
        c = _Chat(sock._path)
        r = c.send(op="ask", question="anything")
        assert r["ok"] is True and r["refused_because"] == ""
        c.close()
    finally:
        sock.stop()


def test_an_answer_comes_back_with_a_handle_to_correct_it(served):
    """Without an id travelling out beside the answer, a face can show a
    "that's wrong" button and have nothing to name when it is pressed. That
    is why the law sat recorded and unbuilt."""
    sock, brain = served
    c = _Chat(sock._path)
    try:
        r = c.send(op="ask", question="when was the kiln firing rescheduled to?")
        assert r["ok"] and not r["abstained"]
        assert r["line"], "no handle came back with the answer"
        # And the handle actually resolves — a grade recorded against it
        # lands as the founder's own words.
        g = c.send(op="grade", line=r["line"], verdict="wrong",
                   correction="It moved to Friday, not Thursday.")
        assert g["ok"] is True
        assert "It moved to Friday" in g["recorded"]
    finally:
        c.close()


def test_an_abstention_offers_no_handle(served):
    """"not in memory" is a statement about the store, not about a life."""
    sock, _ = served
    c = _Chat(sock._path)
    try:
        r = c.send(op="ask", question="what did zzyzx close at?")
        assert r["abstained"] is True
        assert not r["line"]
    finally:
        c.close()


def test_a_grade_cannot_name_something_never_shown(served):
    """The guard that matters. A grade is written in the founder's voice at
    the highest grade in the store; letting it name a statement he was never
    shown puts words in his mouth where they outrank everything."""
    sock, _ = served
    c = _Chat(sock._path)
    try:
        g = c.send(op="grade", line="deadbeef1234", verdict="wrong",
                   correction="whatever")
        assert g["ok"] is False
        assert "actually put in front of you" in g["error"]
    finally:
        c.close()


def test_a_correction_and_what_it_corrects_both_survive(served):
    """THE NO-FOUNDER-EXCEPTION ORACLE, through the UI's path.

    The law (campaign/tree, The Local LLM / No founder exception):
    "correction + re-ask → answer contains both the screen's claim and the
    correction, neither deleted."

    A memory that DELETED what it saw when told it was wrong would be a
    memory that edits its own past on request — which is the failure the
    whole append-only claim log exists to prevent. And one that ignored the
    correction would be useless. Both testimonies, both dated, forever.

    Asserted on what the answer was BUILT FROM rather than on its wording:
    the reasoner here is an extractive fake, so checking its phrasing would
    test the fake instead of the law.
    """
    sock, brain = served
    reasoner = brain._hub.w.reasoner
    c = _Chat(sock._path)
    try:
        first = c.send(op="ask",
                       question="when was the kiln firing rescheduled to?")
        assert first["line"], "no handle to correct"
        assert "thursday" in first["text"].lower()

        c.send(op="grade", line=first["line"], verdict="wrong",
               correction="The kiln firing actually moved to Friday.")
        brain.flush(force=True)

        reasoner.prompts.clear()
        again = c.send(op="ask",
                       question="when was the kiln firing rescheduled to?")
        assert again["ok"]
        shown = "\n".join(reasoner.prompts).lower()
    finally:
        c.close()

    # NEITHER DELETED. The screen's original testimony and the founder's
    # correction were both put in front of the model.
    assert "thursday" in shown, "the screen's own claim vanished after a grade"
    assert "friday" in shown, "the correction never reached the answer"


def test_a_correction_and_what_it_corrects_both_survive(served):
    """THE NO-FOUNDER-EXCEPTION ORACLE, through the UI's own path.

    The recorded law: "correction + re-ask → answer contains both the
    screen's claim and the correction, neither deleted."

    This test FAILED when first written, and the failure was the point. The
    correction was in the store, bound and durable at the highest grade there
    is — and structurally unreachable: it carries no app, no place and no
    frame, so it births no node, and retrieval walks nodes. The most
    authoritative statement anywhere in the store was the only one no
    question could find, while the window told the founder "TRACE will
    re-derive from this tonight."

    A memory that DELETED what it saw when corrected would be a memory that
    edits its past on request. One that ignored the correction would be
    useless. Both testimonies, both dated, forever.

    Asserted on what the answer was BUILT FROM, not on its wording: the
    reasoner here is an extractive fake, so checking phrasing would test the
    fake instead of the law.
    """
    sock, brain = served
    reasoner = brain._hub.w.reasoner
    c = _Chat(sock._path)
    try:
        first = c.send(op="ask",
                       question="when was the kiln firing rescheduled to?")
        assert first["line"], "no handle to correct the answer by"
        assert "thursday" in first["text"].lower()

        g = c.send(op="grade", line=first["line"], verdict="wrong",
                   correction="The kiln firing actually moved to Friday.")
        assert g["ok"] is True
        brain.flush(force=True)

        reasoner.prompts.clear()
        again = c.send(op="ask",
                       question="when was the kiln firing rescheduled to?")
        assert again["ok"]
        shown = "\n".join(reasoner.prompts).lower()
    finally:
        c.close()

    assert "friday" in shown, "the correction never reached the answer"
    assert "thursday" in shown, "the screen's own claim vanished after a grade"


def test_an_unrelated_correction_does_not_follow_you_around(served):
    """Corrections are matched to the question, not stapled to every context.

    Padding every answer with unrelated founder opinions would spend the
    budget the actual evidence needs, and would invite the model to connect
    things the founder never connected.
    """
    sock, brain = served
    reasoner = brain._hub.w.reasoner
    c = _Chat(sock._path)
    try:
        first = c.send(op="ask",
                       question="when was the kiln firing rescheduled to?")
        c.send(op="grade", line=first["line"], verdict="wrong",
               correction="Bicycles are stored in the shed.")
        brain.flush(force=True)
        reasoner.prompts.clear()
        c.send(op="ask", question="what did zzyzx close at?")
        shown = "\n".join(reasoner.prompts).lower()
    finally:
        c.close()
    assert "bicycles" not in shown


def test_what_you_asked_is_written_down(served):
    """`Ask/History`: every ask lands as a claim about what you wondered —
    and the model's answer never lands in the claim's TEXT."""
    sock, brain = served
    c = _Chat(sock._path)
    try:
        c.send(op="ask", question="when was the kiln firing rescheduled to?")
        brain.flush(force=True)

        def look():
            return [(cl.text, cl.structured)
                    for cl in brain._hub.w.claim_log.all_claims()
                    if (cl.structured or {}).get("asked")]

        rows = brain._on_owner(look)
    finally:
        c.close()

    assert len(rows) == 1, "the question was not recorded"
    text, structured = rows[0]
    assert text == 'You asked: "when was the kiln firing rescheduled to?"'
    assert structured["answered"]
    assert structured["answered"] not in text


def test_a_past_ask_is_findable(served):
    """THE ORACLE. Storing questions was the easy half — a stored ask was
    unreachable by any question, because it carries no app, no place and no
    frame, so it births no node and retrieval walks nodes."""
    sock, _ = served
    c = _Chat(sock._path)
    try:
        c.send(op="ask", question="when was the kiln firing rescheduled to?")
        c.send(op="ask", question="what did zzyzx close at?")

        allh = c.send(op="history")
        assert allh["ok"]
        assert "kiln firing" in allh["text"]
        assert "zzyzx" in allh["text"]

        # And findable BY WORD — someone looking for what they asked is
        # remembering their own words, not the machine's.
        one = c.send(op="history", query="kiln")
        assert "kiln firing" in one["text"]
        assert "zzyzx" not in one["text"]
    finally:
        c.close()


def test_history_says_when_it_had_nothing(served):
    """That the memory failed a question is itself worth remembering — it is
    how anyone later learns which questions it kept failing."""
    sock, _ = served
    c = _Chat(sock._path)
    try:
        c.send(op="ask", question="what did zzyzx close at?")
        assert "it had nothing" in c.send(op="history", query="zzyzx")["text"]
    finally:
        c.close()


def test_an_empty_history_says_so(served):
    sock, _ = served
    c = _Chat(sock._path)
    try:
        assert "not asked" in c.send(op="history")["text"]
    finally:
        c.close()


def test_past_questions_never_leak_into_an_answer(served):
    """A correction is authority; a past question is not.

    Pulling "You asked: who is Juno?" into a fresh question about Juno
    spends budget the real evidence needs and invites the model to treat the
    asking as a fact about her. History is served on request, never smuggled
    into retrieval.
    """
    sock, brain = served
    reasoner = brain._hub.w.reasoner
    c = _Chat(sock._path)
    try:
        c.send(op="ask", question="when was the kiln firing rescheduled to?")
        brain.flush(force=True)
        reasoner.prompts.clear()
        c.send(op="ask", question="when was the kiln firing rescheduled to?")
        shown = "\n".join(reasoner.prompts)
    finally:
        c.close()
    assert "You asked" not in shown


def test_every_new_op_works_through_a_real_brain(served):
    """THE CLASS OF BUG THIS EXISTS TO CATCH.

    `last_refusal` was set on the Hub and read off the Brain, which has no
    delegation — so the feature never worked in production while its own
    tests passed, because those tests used a stub brain carrying the
    attribute directly.

    Six ops landed on 2026-08-25. Each is exercised here against the REAL
    Brain over a REAL hub and store, so a value that does not cross that seam
    fails here rather than in front of the founder.
    """
    sock, _ = served
    c = _Chat(sock._path)
    try:
        first = c.send(op="ask",
                       question="when was the kiln firing rescheduled to?")
        assert first["ok"]

        # moment — follow the citation the answer just gave.
        cite = str((first["citations"] or ["deadbeef"])[0])[:8]
        m = c.send(op="moment", cite=cite)
        assert m["ok"] and isinstance(m["text"], str) and m["text"]

        # history — the ask above must be in it.
        h = c.send(op="history")
        assert h["ok"] and "kiln firing" in h["text"]

        # morning — may be quiet, but must not error.
        mo = c.send(op="morning")
        assert mo["ok"] and ("quiet" in mo)

        # questions — may be quiet, but must not error.
        q = c.send(op="questions")
        assert q["ok"] and ("quiet" in q)

        # answer — refuses an id that was never put to anyone, rather than
        # inventing a question to attach the words to.
        a = c.send(op="answer", question="nope", said="something")
        assert a["ok"] is False
        assert "question" in a["error"].lower()

        # grades — the answer's citations carry how much to trust them.
        assert isinstance(first.get("citation_grades"), dict)
        if first["citations"]:
            assert first["citation_grades"], "no grade crossed the seam"
    finally:
        c.close()


def test_the_window_path_answers_with_the_same_shape(served):
    """CAUGHT BY A SHADOW RUN AGAINST THE REAL STORE.

    "What did I do yesterday?" is the commonest question there is, and it
    routes to the window path — which returned a reply it built by hand.
    So on that one path, and only on that one, there was no gradeable handle,
    no citation grades and no entry in the ask history. Three features
    silently doing nothing on the path a person actually uses, while working
    perfectly on the paths the tests exercised.

    An early return that reconstructs a reply by hand is how a code path
    drifts out of a contract without any diff ever looking wrong.
    """
    sock, brain = served
    c = _Chat(sock._path)
    try:
        r = c.send(op="ask", question="what did I do yesterday?")
        assert r["ok"]
        # Every field the ordinary path promises must be present here too.
        for field in ("text", "abstained", "citations", "unsupported",
                      "refused_because", "line", "citation_grades"):
            assert field in r, "the window path dropped %r" % field

        # And the ask must have been recorded, whichever path served it.
        brain.flush(force=True)
        h = c.send(op="history")
        assert "yesterday" in h["text"], \
            "an ask served by the window path never reached the history"
    finally:
        c.close()


def test_a_timed_out_request_names_where_the_owner_is_stuck():
    """A BrainNotServing that says only "wedged" sent a whole morning of
    diagnosis (2026-10-07) through native samples and a crashing signal dump.
    The waiting thread can read the owner's Python frame safely; the error
    now carries it."""
    import threading
    from tracemem.app.brain import BrainNotServing

    brain, _hub, _clock = _brain()
    release = threading.Event()
    started = threading.Event()

    def hold_the_owner_in_a_named_place():
        brain.claim_owner()
        started.set()
        release.wait(5)

    t = threading.Thread(target=hold_the_owner_in_a_named_place)
    t.start()
    started.wait(5)
    try:
        with pytest.raises(BrainNotServing) as err:
            brain._on_owner(lambda: None, timeout_s=0.2)
        assert "hold_the_owner_in_a_named_place" in str(err.value)
    finally:
        release.set()
        t.join(5)


def test_where_reports_every_thread_without_touching_the_owner():
    from tracemem.app.ask_socket import AskSocket
    import json
    sock = AskSocket.__new__(AskSocket)
    sock._waking = lambda: None
    sock._window = None
    got = sock._handle(json.dumps({"op": "where", "depth": 3}).encode())
    assert got["ok"] and any("MainThread" in k for k in got["threads"])
    assert all(len(v) <= 3 for v in got["threads"].values())
