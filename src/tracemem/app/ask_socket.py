"""OWNS: the local API a face talks to. A UNIX DOMAIN SOCKET, never a port.

WHY NOT HTTP ON LOCALHOST, which is what the tree's brief for 10_faces >
Ask > The window loosely calls "the hub's local API": because
`packaging/Trace.entitlements` declares

    com.apple.security.network.server   false
    com.apple.security.network.client   false

and `packaging/entitlements.md` justifies the first with, verbatim, "The app
listens on no port. The hub in the old stack bound :8765; the Watcher does
not inherit that. Local UI talks to the daemon over a UNIX domain socket in
the app's container, which needs no entitlement and is not reachable from
another machine."

That is not a preference. The entitlements file is the argument this product
makes at App Review and to its user — that a memory of your whole life
cannot leave your Mac because the app holds no capability to send it. A
localhost port would be a listening socket any other process on the machine
could reach, and the board already records a live instance of this class
(the ear opening a network connection in a product declaring
network.client=false). A filesystem socket with 0600 permissions is
reachable by this user and nothing else, and it needs no entitlement to
contradict.

PROTOCOL: one JSON object per line, request and response.

    -> {"op": "ask", "question": "when was the kiln firing moved to?"}
    <- {"ok": true, "text": "...", "citations": [...], "abstained": false}

    -> {"op": "stats"}
    <- {"ok": true, "bound": 604, "episodes": 12, "nodes": 37, "pending": 0}

Line-delimited JSON rather than a framed binary protocol because the face is
the only client, both ends are ours, and a protocol a human can speak with
`nc` is a protocol a human can debug at 2am.

ONE QUESTION AT A TIME, and it is a hardware law rather than a simplifying
assumption. Answering loads a model onto the Metal GPU, and this machine's
first hard constraint is that two models resident at once degrade into a
silent OOM. The handler is serialized; a second question waits. Concurrency
here would not be a performance win, it would be the failure mode the
Spotlight exists to prevent.
"""
from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path
from typing import Callable, Optional

# In the app's container in production; under the repo in development.
DEFAULT_SOCKET = Path.home() / ".trace" / "ask.sock"

# A question can take seconds (model swap + generation). A face that has
# gone away must not pin the handler thread forever.
CLIENT_TIMEOUT_S = 300.0
MAX_LINE_BYTES = 64 * 1024

# sockaddr_un.sun_path is 104 bytes on Darwin, 108 on Linux, and bind()
# fails with a bare "AF_UNIX path too long" that names no limit and no
# path. The daemon must not die at startup on an error a person cannot act
# on, so the limit is checked here where the remedy can be stated.
SUN_PATH_MAX = 103


class SocketPathTooLong(ValueError):
    pass


class AskSocket:
    # Every op this build serves. Kept beside the handlers so a new op that
    # forgets to join the list is the exception, not the rule — and a face
    # comparing against it can tell a person exactly what a restart buys.
    OPS = ("ask", "stats", "night", "digest", "grade", "history", "moment",
           "morning", "questions", "answer", "map", "belief", "where",
           "volume")

    """Serves `ask` and `stats` over a UNIX domain socket.

    Runs its accept loop on a daemon thread. Every request is handled under
    one lock, so the one-model law holds no matter how many faces connect.
    """

    def __init__(self, brain, path=DEFAULT_SOCKET,
                 log: Optional[Callable[[str], None]] = None,
                 night=None, waking=None, eyelid=None, organs=None,
                 window_answerer=None, claim_log=None,
                 claim_log_factory=None) -> None:
        self._brain = brain
        self._window = window_answerer
        # THE STORE, HANDED OVER RATHER THAN GUESSED AT. These two readings
        # used to reach for `brain._store` / `brain.store`, and Brain has
        # neither — it holds `_append` and `_hub`. So both getattrs returned
        # None and `last seen` printed "never — this store holds no
        # perceptions" about a store holding 471,503 of them. That line is
        # the ONE number the 2026-08-10 fix added so a dead eye could be
        # seen; it was reading nothing. A status that lies about being blind
        # is the exact failure it was built to end.
        self._claim_log = claim_log
        # A FACTORY, because these readings run on the CLIENT's thread.
        # sqlite3 connections are bound to the thread that opened them, so a
        # shared handle raises ProgrammingError here — and `_last_capture_ms`
        # catches Exception and returns None, which `./trace status` prints
        # as "never — this store holds no perceptions". A store with 471,503
        # claims reported itself empty, through a bare `except`, in the one
        # reading built to prove the eye is alive. Fixed by opening a
        # short-lived read connection per request; it never appends, so the
        # one-writer law is untouched.
        self._open_log = claim_log_factory

        # Optional: a daemon run with --no-night has none, and the `night` op
        # then refuses honestly rather than pretending it thought.
        self._night = night
        # The eyelid, so `./trace status` can say whether the eye is open and
        # WHY it last closed. An automatic thing nobody can observe is an
        # automatic thing nobody can debug.
        self._eyelid = eyelid
        # Organ health, so a blind eye is one question away rather than
        # buried in a witness nobody thought to read.
        self._organs = organs
        # WAKING (2026-07-29, N3). Returns None once the store is bound, or a
        # dict of progress while it is still binding.
        #
        # The socket used to be opened AFTER backfill, and the reason written
        # down was exactly right: "so a user is never briefly told their
        # memory is empty when it is not." On 17,000 claims that took about
        # two minutes, during which `./trace start` — which waits 120s for
        # the socket — reported FAILED on a daemon that was working perfectly.
        #
        # Answering early would trade a visible failure for an invisible one:
        # a question asked mid-backfill would get "(not in memory)" about
        # something that IS in memory. That is a FALSE ABSENCE, and absence
        # answers are the promise this product ranks highest.
        #
        # So the socket opens immediately and REFUSES, by name, with its
        # progress. "I am still waking up, 8,000 of 17,000" is a true
        # sentence. "Not in memory" would not have been.
        self._waking = waking or (lambda: None)
        self._path = Path(path)
        self._log = log or (lambda m: None)
        self._sock: Optional[socket.socket] = None
        self._inode: Optional[int] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._answering = threading.Lock()
        self.served = 0

    def _store_db(self):
        """The claim store's connection, explicit first.

        The fallback is kept because it is what every existing caller and
        test passes, but it must never be the ONLY route: `Brain` exposes
        neither `_store` nor `store`, so on the real daemon both getattrs
        returned None and `last seen` printed "never — this store holds no
        perceptions" about a store holding 471,503 of them. That reading is
        the one number added on 2026-08-10 so a dead eye could be seen, and
        it was reading nothing.
        """
        if self._open_log is not None:
            return getattr(self._open_log(), "_db", None)
        log = self._claim_log
        if log is None:
            log = (getattr(self._brain, "_store", None)
                   or getattr(self._brain, "store", None))
        return getattr(log, "_db", None)

    def _seen_last_hour(self) -> int:
        """Perceptions in the last hour — witnesses excluded.

        `bidder` is a plaintext column, so this reads correctly on the
        encrypted store. Witnesses are excluded because a count that rises
        while the daemon writes about its own blindness is the number that
        made a dark eye look healthy for hours.
        """
        try:
            since = int(self._brain._now_ms() - 3600 * 1000)
            db = self._store_db()
            if db is None:
                return -1
            return db.execute(
                "select count(*) from claim where t_ms > ? and "
                "bidder <> 'system:witness'", (since,)).fetchone()[0]
        except Exception:                                # noqa: BLE001
            return -1

    def _last_capture_ms(self):
        """When a SENSE last wrote — witnesses excluded. None if never.

        THE NUMBER THAT WAS MISSING (2026-08-10). Every figure `status`
        printed was cumulative: bound claims, episodes, nodes. Cumulative
        counters cannot fall, so they cannot report a death. Capture stopped
        on 5 August and for five days the daemon answered "612,482 claims" —
        true, healthy-looking, and irrelevant to the only question the founder
        was actually asking, which is "is it still watching?".

        Witnesses are excluded for the same reason `_seen_last_hour` excludes
        them: the daemon writes a witness roughly every 70 seconds whether or
        not it can see, so counting them would make a blind eye report a
        capture two minutes ago, forever. The self-report of a broken sense
        must never be admissible as evidence that the sense works.
        """
        try:
            db = self._store_db()
            if db is None:
                return None
            row = db.execute(
                "select max(t_ms) from claim where "
                "bidder <> 'system:witness'").fetchone()
            return row[0] if row and row[0] is not None else None
        except Exception:                                # noqa: BLE001
            return None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> str:
        encoded = str(self._path).encode()
        if len(encoded) > SUN_PATH_MAX:
            raise SocketPathTooLong(
                "socket path is %d bytes; the OS limit is %d. Put the socket "
                "somewhere shorter (e.g. ~/.trace/ask.sock) — this is a hard "
                "kernel limit on sockaddr_un, not something a longer buffer "
                "fixes.\n  %s" % (len(encoded), SUN_PATH_MAX, self._path))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # A socket file left by a killed daemon would make bind() fail with
        # EADDRINUSE forever. Removing a STALE one is correct; removing a
        # LIVE one would steal another daemon's socket, so the single-
        # instance lock — not this — is what prevents two daemons.
        if self._path.exists():
            self._path.unlink()

        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(str(self._path))
        # REMEMBER WHICH FILE IS OURS. `stop()` unlinks the socket path, and
        # on a restart the outgoing daemon's shutdown races the incoming
        # one's bind: the new daemon creates the socket, the old daemon's
        # `stop()` unlinks the PATH, and the survivor is left listening on a
        # socket nobody can find. Witnessed 2026-08-20 handing the daemon
        # between launchd and a terminal — the log said "ask socket
        # listening", `./trace status` said "no daemon is listening", and
        # both were telling the truth about different files.
        try:
            self._inode = os.stat(str(self._path)).st_ino
        except OSError:
            self._inode = None
        # 0600 BEFORE listen: between bind and chmod the socket exists with
        # the process umask, and on a shared machine that is a window in
        # which another user could connect to a person's memory.
        os.chmod(str(self._path), 0o600)
        self._sock.listen(4)
        self._sock.settimeout(0.5)          # so stop() is not blocked on accept

        self._thread = threading.Thread(target=self._serve, name="ask-socket",
                                        daemon=True)
        self._thread.start()
        self._log("ask socket listening on %s" % self._path)
        return str(self._path)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        # Only remove the socket if it is still OURS — see the note in
        # start(). Unlinking a path we no longer own takes down a healthy
        # daemon that just replaced us.
        try:
            if self._inode is None or os.stat(str(self._path)).st_ino == self._inode:
                self._path.unlink()
        except (FileNotFoundError, OSError):
            pass

    # -- the loop ----------------------------------------------------------

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break                        # socket closed under us: stopping
            threading.Thread(target=self._client, args=(conn,),
                             daemon=True).start()

    def _client(self, conn: socket.socket) -> None:
        conn.settimeout(CLIENT_TIMEOUT_S)
        try:
            buf = b""
            while not self._stop.is_set():
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
                if len(buf) > MAX_LINE_BYTES:
                    self._send(conn, {"ok": False, "error": "request too large"})
                    return
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        self._send(conn, self._handle(line))
        except (socket.timeout, ConnectionResetError, BrokenPipeError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    @staticmethod
    def _send(conn: socket.socket, obj: dict) -> None:
        conn.sendall((json.dumps(obj) + "\n").encode())

    def _handle(self, line: bytes) -> dict:
        try:
            req = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            return {"ok": False, "error": "bad json: %s" % e}
        if not isinstance(req, dict):
            return {"ok": False, "error": "request must be a JSON object"}

        op = req.get("op")

        # THE WAKING GATE, checked on the SOCKET thread. It has to be here:
        # backfill occupies the thread that owns the store, so a request
        # handed to that thread would simply block for two minutes and time
        # out — indistinguishable, from the outside, from a dead daemon.
        waking = self._waking()
        # A QUESTION ABOUT A PERIOD IS NOT GATED ON BINDING. It reads the
        # time-indexed claim table and touches no node, so making it wait for
        # backfill made the commonest question a memory gets ("what was I
        # doing yesterday") the slowest thing the daemon could do. Tried
        # BEFORE the waking gate and before the brain, deliberately.
        if op == "ask" and self._window is not None:
            question = req.get("question")
            if isinstance(question, str) and question.strip():
                try:
                    answer = self._window.answer(question)
                except Exception as e:
                    self._log("window answer failed: %s: %s"
                              % (type(e).__name__, e))
                    answer = None
                if answer is not None:
                    self.served += 1
                    # THE WINDOW PATH IS STILL AN ANSWER, and for a month it
                    # was the only one that behaved like something else.
                    #
                    # MEASURED against a snapshot of the real store: "what
                    # did I do yesterday?" — the commonest question there is,
                    # and the one that routes here — came back with no
                    # gradeable handle, no citation grades, and no entry in
                    # the ask history, because this branch returns before all
                    # of that. Three features silently did nothing on the
                    # path a person actually uses, while working perfectly on
                    # the paths the tests exercised.
                    #
                    # An early return that reconstructs a reply by hand is
                    # how a code path drifts out of a contract without any
                    # diff ever looking wrong.
                    return self._answer_reply(question, answer,
                                              path="window",
                                              source=self._window)
        if waking is not None and op != "stats":
            return dict({"ok": False, "waking": True,
                         "error": "still waking up — binding what is already "
                                  "on disk. Nothing is lost; ask again in a "
                                  "moment."}, **waking)

        if op == "where":
            # WHERE EVERY THREAD IS, in Python. Read-only, answered on this
            # socket thread, and safe on a live daemon — unlike the SIGUSR2
            # faulthandler dump, which segfaulted it (2026-10-07). Built when
            # a period answer spent 46 of 57s waiting and a native sample
            # could say only that three threads were busy in "Python3".
            import sys as _sys
            import threading as _th
            import traceback as _tb
            names = {t.ident: t.name for t in _th.enumerate()}
            out = {}
            for ident, frame in _sys._current_frames().items():
                stack = _tb.extract_stack(frame)[-int(req.get("depth") or 12):]
                out["%s (%s)" % (names.get(ident, "?"), ident)] = [
                    "%s:%d %s" % (f.filename.rsplit("/", 2)[-1] if "/" in f.filename
                                  else f.filename, f.lineno, f.name)
                    for f in stack]
            return {"ok": True, "threads": out}

        if op == "stats":
            # STATS ANSWERS WHILE WAKING, and is the only op that does: it
            # reports counts, never content, so a partial count carries no
            # claim about what is or is not in memory. It is also what
            # `./trace status` needs in order to say "still waking" instead
            # of guessing.
            if waking is not None:
                return dict({"ok": True, "waking": True}, **waking)
            stats = dict(self._brain.stats())
            if self._night is not None:
                stats["night"] = self._night.stats()
            if self._eyelid is not None:
                stats["eyelid"] = self._eyelid.stats()
            # SEEN THIS HOUR, reported BY the daemon. The face used to count
            # this itself by opening the store — which works from a terminal
            # and is refused for an app bundle, because macOS puts ~/Documents
            # behind TCC. A face that must read the project directory is a
            # face that cannot ship.
            # WHAT THIS DAEMON CAN ACTUALLY DO.
            #
            # A running daemon is as old as the last restart, and the code on
            # disk moves. Six capabilities were added on 2026-08-25 alone —
            # following a citation, reading your own asks back, the morning
            # surface, the memory's questions — and every one of them
            # answered `unknown op` against the daemon that was up, with no
            # hint that a restart was all it needed.
            #
            # Reporting the OPS rather than a version number is deliberate:
            # a version tells a face that something differs, this tells it
            # exactly what is missing, and it cannot drift from the truth
            # because it is generated from the handler itself.
            stats["ops"] = sorted(self.OPS)
            stats["seen_last_hour"] = self._seen_last_hour()
            last_ms = self._last_capture_ms()
            # ALWAYS PRESENT, even as None. A key that appears only on success
            # makes "the daemon never told me" and "the sense never fired"
            # indistinguishable at the face, and the face would have to guess
            # which — the exact ambiguity that produces a confident wrong
            # status line. Absent now means "a daemon too old to know", which
            # is a different sentence and an honest one.
            stats["last_capture_ms"] = last_ms
            if last_ms is not None:
                import time as _t
                # THE INTEGER TRAVELS, THE FACE RENDERS IT (time law: no naive
                # datetime on the write path — store t_ms, render in the lived
                # tz at the faces). The age is arithmetic on two integers, not
                # a calendar operation, so it is safe to compute here and
                # spares every face from re-deriving the same subtraction.
                stats["last_capture_age_h"] = round(
                    (_t.time() - last_ms / 1000.0) / 3600.0, 2)
            if self._organs is not None:
                try:
                    stats["organs"] = self._organs()
                except Exception:                            # noqa: BLE001
                    pass
            return dict({"ok": True}, **stats)
        if op == "night":
            # RUN ONE SLICE ON DEMAND (P2, 2026-07-29). The night is
            # continuous, but nobody has ever WATCHED this engine work on
            # real data — it had no production caller at all until today.
            # Automating a process no one has observed is how you wake up to
            # a graph full of confident nonsense, so the same slice the
            # watchman runs on a beat can also be asked for, and its report
            # returned verbatim.
            if self._night is None:
                return {"ok": False,
                        "error": "this daemon runs with --no-night"}
            with self._answering:            # thinking holds the GPU too
                report = self._night.tick()
            if report is None:
                return {"ok": True, "ran": False,
                        "why_not": self._night.last_skip}
            return {"ok": True, "ran": True,
                    "status": getattr(report, "status", "?"),
                    "attempted": getattr(report, "attempted", 0),
                    "accepted": getattr(report, "accepted", 0),
                    "rejected": getattr(report, "rejected", 0),
                    "mined": len(getattr(report, "mined", ())),
                    "pruned": len(getattr(report, "pruned", ())),
                    "semantic_edges": getattr(report, "semantic_edges", 0),
                    "by_kind": dict(getattr(report, "by_kind", {}))}
        if op == "digest":
            # WHAT THE BRAIN DID AND WHAT IT NOW THINKS (P3). The founder's
            # own answer to "how would you know the memory tier works":
            # show him, and let him grade it.
            from tracemem.app.digest import MS_DAY, render
            hours = float(req.get("hours") or 24)
            t1 = int(req.get("t1_ms") or self._brain._now_ms())
            t0 = t1 - int(hours * MS_DAY / 24)
            with self._answering:
                d = self._brain.digest(t0, t1)
            return {"ok": True, "text": render(d),
                    "lines": [{"id": l.id, "section": l.section,
                               "text": l.text, "citations": list(l.citations)}
                              for l in d.lines],
                    "coverage_pct": d.coverage_pct,
                    "claims_seen": d.claims_seen}

        if op == "grade":
            line_id = req.get("line")
            verdict = req.get("verdict")
            if not isinstance(line_id, str) or not isinstance(verdict, str):
                return {"ok": False,
                        "error": "grade needs `line` and `verdict`"}
            try:
                claim = self._brain.grade(line_id, verdict,
                                          str(req.get("correction") or ""))
            except Exception as e:                          # noqa: BLE001
                return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            return {"ok": True, "recorded": claim.text, "claim_id": claim.id}

        if op == "questions":
            try:
                text = self._brain.questions()
            except Exception as e:                          # noqa: BLE001
                return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            return {"ok": True, "text": text, "quiet": text is None}

        if op == "answer":
            try:
                claim = self._brain.answer_question(
                    str(req.get("question") or ""), str(req.get("said") or ""))
            except Exception as e:                          # noqa: BLE001
                return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            # Echoed back: a claim written in someone's name that they never
            # read is the beginning of a memory they cannot trust.
            return {"ok": True, "recorded": claim.text}

        if op == "morning":
            import time as _t2
            try:
                since = int(req.get("since_ms")
                            or (_t2.time() - 24 * 3600) * 1000)
                text = self._brain.morning(since, int(req.get("limit") or 3))
            except Exception as e:                          # noqa: BLE001
                return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            # `text` is None on a quiet night, and that travels as None. A
            # face must be able to tell "nothing to say" from "here is an
            # empty page", or it will render the second and look broken.
            return {"ok": True, "text": text, "quiet": text is None}

        if op == "moment":
            # THE CITATION MADE FOLLOWABLE. Until now every answer came back
            # stamped [a3b4115f] with nothing behind the stamp — a citation
            # nobody can follow asks for trust instead of earning it.
            try:
                text = self._brain.moment(str(req.get("cite") or ""))
            except Exception as e:                          # noqa: BLE001
                return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            return {"ok": True, "text": text}

        if op == "history":
            # WHAT YOU ASKED, SERVED ON REQUEST. Not folded into retrieval:
            # a past question is history, not authority, and injecting it
            # into a fresh context invites a model to treat the asking as a
            # fact. See supplier/history.py.
            try:
                text = self._brain.history(str(req.get("query") or ""),
                                           int(req.get("limit") or 50))
            except Exception as e:                          # noqa: BLE001
                return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            return {"ok": True, "text": text}

        if op == "map":
            try:
                data = self._brain.live_map(
                    str(req.get("query") or ""),
                    bool(req.get("include_retired", True)),
                    int(req.get("limit") or 160))
            except Exception as e:                          # noqa: BLE001
                return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            return dict({"ok": True}, **data)

        if op == "volume":
            try:
                data = self._brain.volume(int(req.get("sample") or 4000))
            except Exception as e:                          # noqa: BLE001
                return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            return dict({"ok": True}, **data)

        if op == "belief":
            node_id = req.get("id")
            if not isinstance(node_id, str) or not node_id:
                return {"ok": False, "error": "belief needs a non-empty `id`"}
            try:
                data = self._brain.belief(node_id)
            except Exception as e:                          # noqa: BLE001
                return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            return dict({"ok": True}, **data)

        if op != "ask":
            return {"ok": False,
                    "error": "unknown op %r (want "
                             "ask|stats|night|digest|grade|history|"
                             "moment|morning|questions|answer|map|belief)" % op}

        question = req.get("question")
        if not isinstance(question, str) or not question.strip():
            return {"ok": False, "error": "ask needs a non-empty question"}

        # Serialized: see the one-model law in this module's docstring.
        with self._answering:
            # Cleared before the question, not after, so a reason left over
            # from an earlier question can never be attached to this one.
            try:
                self._brain.last_refusal = ""
            except Exception:                                # noqa: BLE001
                pass
            try:
                answer = self._brain.ask(question)
            except Exception as e:                          # noqa: BLE001
                # A face must never be left hanging on a raised exception,
                # and the daemon must never die because a question was hard.
                self._log("ask failed: %s: %s" % (type(e).__name__, e))
                return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            self.served += 1

        # WHY IT WOULD NOT SAY IT, CARRIED OUT TO THE FACE. Not shown to the
        # founder by default — "the block does not support the question" is
        # the checker talking to its author, not to a person. It travels so
        # that tools/answer_health.py can report WHICH refusals happened
        # instead of only how many, and so the next investigation starts from
        # a sentence rather than from a re-run with an instrumented checker.
        return self._answer_reply(question, answer)

    def _answer_reply(self, question, answer, path=None, source=None):
        """ONE shape for every answer, whichever path produced it.

        Built as a method rather than repeated, because the window path
        drifted out of this contract by being written separately — and the
        drift cost the ask history, the correction handle and the citation
        grades on the single most-asked question.
        """
        from tracemem.domain.grade import in_words

        # WHICHEVER OBJECT ACTUALLY PRODUCED THE ANSWER. The window path is
        # served by a different one, and reading the brain's fields for an
        # answer the brain did not write is how the citation grades came back
        # empty on the most-asked question in the product.
        src = source if source is not None else self._brain
        raw_grades = dict(getattr(src, "last_citation_grades", {}) or {})
        grades = {str(k)[:8]: in_words(v) for k, v in raw_grades.items()}
        why = str(getattr(src, "last_refusal", "") or "")
        if why:
            self._log("guard refused: %s || %s" % (question[:60], why))
        # THE HANDLE THAT MAKES "THAT'S WRONG" POSSIBLE. Without an id
        # travelling out with the answer, a face can show a correction button
        # and have nothing to name when the founder presses it — which is why
        # the law sat recorded and unbuilt. None for an abstention, on
        # purpose: see brain.remember_answer.
        try:
            line = self._brain.remember_answer(question, answer)
        except Exception:                                    # noqa: BLE001
            line = None
        # THE QUESTION ITSELF GOES INTO MEMORY. Guarded: a store write must
        # never cost the founder the answer he already has in hand.
        try:
            self._brain.record_ask(question, answer)
        except Exception as e:                               # noqa: BLE001
            self._log("could not record the ask: %s: %s" % (type(e).__name__, e))
        return {
            "ok": True,
            "text": answer.text,
            "abstained": bool(answer.abstained),
            "citations": list(answer.citations or ()),
            "unsupported": list(getattr(answer, "invented", ()) or ()),
            "refused_because": why,
            "line": line,
            # {citation: "read" | "glanced at" | "worked out" | ...}. An
            # answer resting on a VLM's glance used to look identical to one
            # resting on text the machine actually read.
            "citation_grades": grades,
            "path": path,
        }
