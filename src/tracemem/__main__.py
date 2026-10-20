"""The entrypoint (WO-091). `python -m tracemem` starts the daemon.

Before this file existed, `Supervisor` (app/daemon.py) had ZERO production
callers: the lifecycle — boot writes the gap witness, organs restart or get
benched, stop writes the clean goodbye — was fully built, fully tested, and
reachable only from tests. Nothing a user could start existed anywhere in
the repo.

WHAT THIS DOES AND DOES NOT CLAIM. It boots the supervision loop against a
PERSISTENT store and shuts down cleanly with the witnesses the gap detector
depends on. It does not yet capture: no class in `src/` implements the organ
contract (`name` / `start` / `stop` / `alive`) — `def alive` appears only in
tests/unit/test_daemon.py. So with no organs registered this daemon runs
EYELESS, and that is a first-class witnessed state rather than a pretence
(the same stance app/permissions.py takes when Screen Recording is denied).
Wiring the eye is its own work order; half-building it here would produce a
daemon that looks like it is watching and is not, which is the one failure
this product may never have.

WHY THE STORE PATH IS AN ARGUMENT WITH A REAL DEFAULT: tools/first_beat.py
writes to /tmp and DELETES its store on every invocation, which is correct
for a probe and is why no run in this project's history has ever produced a
persistent store. The daemon's default is `data/trace_store.sqlite3` and it
is never truncated. If a claim lands, it stays.

    python -m tracemem                     run until SIGINT/SIGTERM
    python -m tracemem --seconds 5         bounded run (the test uses this)
    python -m tracemem --store PATH        somewhere other than data/
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path
from typing import List, Optional

from tracemem.app.ask_socket import AskSocket
from tracemem.capture.steady_clock import SteadyClock
from tracemem.app.claim_queue import ClaimQueue
from tracemem.app.daemon import SingleInstanceLock, Supervisor
from tracemem.app.witness import WitnessKind, witness
from tracemem.app.wiring import _crypto_for
from tracemem.text.claim_log import ClaimLog

# Sentinel: `crypto=None` is a MEANINGFUL argument (it selects NullCrypto
# explicitly, which tests want), so it cannot double as "not supplied".
_UNSET = object()


def _log(msg: str) -> None:
    print("[tracemem] %s" % msg, file=sys.stderr, flush=True)

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_STORE = ROOT / "data" / "trace_store.sqlite3"
DEFAULT_GRAPH = ROOT / "data" / "trace_graph.sqlite3"
DEFAULT_LOCK = ROOT / "data" / "trace.lock"

# One supervision beat. Not tunable by accident: the beat is what turns a
# dead organ into a restarted one, so a long interval is a long blindness.
TICK_SECONDS = 1.0

def _binding_backlog(graph, claim_log):
    """Claims lacking a completed bind receipt, in local append order.

    Never use event time here.  A phone sync, file import, or founder answer
    may be appended today with last Tuesday's observation time.  The hash
    chain's append sequence is durable and total; the graph's per-claim
    receipt says which of those appends actually completed upkeep.
    """
    required = ("get_meta", "binding_seen")
    if graph is None or any(not hasattr(graph, name) for name in required):
        return list(claim_log.all_claims())
    try:
        after = int(graph.get_meta("bind_watermark_seq", 0) or 0)
    except (TypeError, ValueError):
        after = 0
    positions = claim_log.positions(after_seq=after)
    complete = graph.binding_seen([claim_id for claim_id, _seq in positions])
    missing = [claim_id for claim_id, _seq in positions
               if claim_id not in complete]
    return claim_log.read_ids(missing)


def _organ_health(supervisor, macro_pass=None) -> dict:
    """Public status projection for every supervised physical sense."""
    out = dict(supervisor.health())
    for name, st in getattr(supervisor, "_organs", {}).items():
        organ = getattr(st, "organ", None)
        if organ is None or not hasattr(organ, "health"):
            continue
        try:
            out["%s_detail" % name] = organ.health()
        except Exception:                                  # noqa: BLE001
            pass
    if macro_pass is not None:
        try:
            out["macro"] = macro_pass.stats()
        except Exception:                                  # noqa: BLE001
            pass
    return out


def last_state(store: ClaimLog):
    """(last_claim_t_ms, last_witness_kind) — what boot() needs to decide
    whether the silence since the last run is explainable.

    Read from the store rather than from a state file on purpose: a
    sidecar file can disagree with the claims, and when it does, the file
    wins and the hole gets mis-described. The claims are the truth.
    """
    last_t = store.latest_event_t_ms()
    if last_t is None:
        return None, None
    claim = store.latest_by_bidder("system:witness")
    witness_kind = ((claim.structured or {}).get("witness")
                    if claim is not None else None)
    try:
        kind = WitnessKind(witness_kind) if witness_kind else None
    except ValueError:
        kind = None                  # a kind this build does not know
    return last_t, kind


# -- SETTINGS THAT ACTUALLY DO SOMETHING ----------------------------------
#
# MEASURED 2026-08-25: `night_tokens`, `night_on_battery`, `eyelid_idle_
# minutes` and `model` were read by NOBODY. They were written to
# ~/.trace/settings.json, printed by `./trace settings` and by the app's
# Settings screen, and no part of the running product consulted any of them.
# Four controls that looked like controls.
#
# That is the same failure as the frozen blocklist and worse in one respect:
# a person could set the eye to close after an hour, read "60 minutes" back
# off the screen, and be watched on the old 15-minute rule for ever. The
# screen was describing a machine that did not exist.
#
# These read the file live, so a change lands on the next beat or the next
# night rather than the next restart. A read that stumbles falls back to the
# shipped default; it never falls back to zero, which would close the eye
# instantly and permanently.

# How many recent claims the boot check walks. The tail is what a crash or a
# bad shutdown damages; a walk over the whole store belongs to the night,
# which has a budget for it.
BOOT_CHAIN_TAIL = 500


def _live_settings():
    from tracemem.app import settings as _s
    global _SETTINGS_WATCHER
    try:
        _SETTINGS_WATCHER
    except NameError:
        _SETTINGS_WATCHER = _s.SettingsWatcher()
    return _SETTINGS_WATCHER.current()


def _idle_threshold_s() -> float:
    from tracemem.app.settings import DEFAULT_IDLE_MINUTES
    try:
        return float(_live_settings().eyelid_idle_minutes) * 60.0
    except Exception:                                        # noqa: BLE001
        return DEFAULT_IDLE_MINUTES * 60.0


def _night_on_battery() -> bool:
    try:
        return bool(_live_settings().night_on_battery)
    except Exception:                                        # noqa: BLE001
        return False                     # never on a cell, when unsure


def _night_token_budget() -> int:
    try:
        return int(_live_settings().night_tokens)
    except Exception:                                        # noqa: BLE001
        return 0


def _chosen_model(cli_model: str) -> str:
    """The founder's model if he named one, else whatever the CLI was told.

    Empty means untouched, deliberately: a settings file nobody has edited
    must not be able to repoint the mouth behind anyone's back.
    """
    try:
        chosen = str(_live_settings().model or "").strip()
    except Exception:                                        # noqa: BLE001
        chosen = ""
    return chosen or cli_model



def run(store_path: str, seconds: Optional[float] = None,
        lock_path: Optional[str] = None, organs: Optional[List] = None,
        tick_seconds: float = TICK_SECONDS, crypto=_UNSET, keychain=None,
        brain=None, ask_socket_path=None, store=None, window_answerer=None,
        claim_log_factory=None,
        night=None, spotlight=None, eyelid=True, clock=None,
        macro_pass=None, keeper=None,
        device_id: str = "mac-1") -> dict:
    """Boot, supervise, stop cleanly. Returns a small report.

    `seconds=None` runs until a signal arrives. Anything else is a bounded
    run, which is what makes this testable at all — a daemon whose only mode
    is "forever" is a daemon no test ever starts.
    """
    Path(store_path).parent.mkdir(parents=True, exist_ok=True)
    lock = SingleInstanceLock(lock_path or str(DEFAULT_LOCK))
    if not lock.acquire():
        raise SystemExit(
            "another tracemem daemon holds %s — refusing to start a second "
            "one. Two daemons would both capture, both witness, and disagree "
            "about the same seconds." % (lock_path or DEFAULT_LOCK))

    # THE EYE'S STORE GETS THE SAME CRYPTO DECISION AS EVERY OTHER STORE.
    # This line used to read `ClaimLog(store_path)` — the default, which is
    # NullCrypto — while wiring.build() was being taught to encrypt. The
    # daemon does not go through wiring.build(), so fixing the composition
    # root alone would have left encryption switched on everywhere EXCEPT
    # the one process that actually watches the founder's screen. That is
    # this campaign's named disease (a gate with zero production callers)
    # reappearing inside its own cure.
    # `store` may be supplied so the caller and this loop share ONE
    # connection. Two ClaimLogs on one file would be two writers, and the
    # hash chain's link_n is computed from the tail it can see — two writers
    # racing that read produce two claims claiming the same predecessor.
    if store is None:
        if crypto is _UNSET:
            crypto = _crypto_for(
                {} if keychain is None else {"keychain": keychain}, store_path)
        store = ClaimLog(store_path, crypto=crypto)
    enc = store.encryption()
    if enc["plaintext"]:
        _log("STORE IS PLAINTEXT: %s — every claim the eye writes is "
             "readable by anyone holding this file" % store_path)
    if enc["mixed"]:
        _log("STORE IS MIXED (created with %s, opening with %s) — older "
             "claims in this file CANNOT be read by this process"
             % (enc["created_with"], enc["cipher"]))
    claims_at_start = store.count()
    # ONE TIME MODEL FOR THE WHOLE PROCESS. The composition root passes this
    # same object to capture, binding, scheduling, questions and supervision.
    # Keeping the object (rather than only its bound now_ms method) matters:
    # drain_jumps() is how discontinuities become durable witnesses.
    clock = clock or SteadyClock()
    now_ms = clock.now_ms

    # Capture runs on SCStream's own thread and sqlite3 connections are
    # thread-bound, so organs never touch the store: they submit here and
    # THIS thread does every append (WO-093). One writer is what keeps the
    # hash chain and the durability policy true.
    # THE BRAIN, if one was supplied. Without it this daemon captures claims
    # into a store and stops there — which is precisely what it did until
    # 2026-07-23, leaving the binder, node creation and the supplier with no
    # production caller at all. With it, `absorb` still appends immediately
    # (the durability window ClaimQueue documents does not move) and binding
    # happens on a slower beat.
    sink = brain.absorb if brain is not None else store.append
    claims = ClaimQueue(sink=sink, now_ms=now_ms)

    # BIND WHAT IS ALREADY ON DISK — inside the lock, before serving.
    #
    # Two orderings matter and both were wrong when this was first written
    # up in main():
    #   · AFTER the single-instance lock. A second daemon would otherwise
    #     bind the whole store — touching a file another daemon owns and
    #     rebuilding the node index for a year of claims — and only THEN
    #     discover it is not allowed to run.
    #   · BEFORE the ask socket accepts anything, so a user is never briefly
    #     told their memory is empty when it is not.
    # THE SUPERVISOR IS BUILT EARLY — construction and registration have
    # no side effects until boot(), and both the eyelid and the ask socket
    # need a reference to it before the store is bound.
    # Witnesses are claims too.  Sending them straight to ClaimLog used to
    # bypass Binder permanently during a long-running process; they only
    # reached the graph by accident on a later restart.  Every producer uses
    # the same durable-then-bind seam.
    witness_sink = brain.absorb if brain is not None else store.append
    supervisor = Supervisor(sink=witness_sink, now_ms=now_ms,
                            device_id=device_id)
    for organ in (organs or []):
        # An entry may be a ready-made organ, or a factory taking the
        # queue's `submit`. The factory form is how a capture organ gets a
        # thread-safe way to hand claims over WITHOUT ever being given the
        # store — an organ that could reach the store could append from its
        # own thread, which is the defect WO-093 exists to make impossible.
        supervisor.register(organ(claims.submit) if callable(organ) else organ)

    # THE EYELID (F-EYELID, 2026-07-29). Built only when there are organs to
    # park AND a brain that would have something to do with the turn — an eye
    # closed for a night that cannot run is a blind spot bought for nothing.
    lid = None
    if eyelid and organs and brain is not None and night is not None:
        from tracemem.app.eyelid import Eyelid
        lid = eyelid if not isinstance(eyelid, bool) else Eyelid(
            supervisor, witness_sink, now_ms, log=_log,
            # LIVE, and previously ignored entirely. Someone could set 60
            # minutes, read 60 back off the settings screen, and have their
            # eye keep closing at 15 for ever — a screen describing a machine
            # that did not exist.
            idle_threshold_s=_idle_threshold_s)

    # THE SOCKET OPENS FIRST AND REFUSES WHILE IT WAKES (2026-07-29, N3).
    #
    # The two orderings above are still both right; a third was missing. On
    # 17,000 claims backfill takes about two minutes, and `./trace start`
    # waits 120 seconds for the socket to appear before declaring FAILED — so
    # the founder's own store had grown large enough that starting a
    # perfectly healthy daemon reported failure.
    #
    # The fix is NOT to answer sooner. A question answered against a
    # half-bound store returns "(not in memory)" about something that is in
    # memory, and a false absence is the single worst answer this product can
    # give. So the socket binds immediately and answers every op with "still
    # waking up, N of M bound" until the store is whole. The daemon is
    # reachable in milliseconds and honest for the whole two minutes.
    waking = {"bound": 0, "episodes": 0, "phase": "binding",
              "total": store.count() if hasattr(store, "count") else 0,
              "done": brain is None}

    def _waking():
        return None if waking["done"] else {
            "bound": waking["bound"], "episodes": waking["episodes"],
            "total": waking["total"], "phase": waking["phase"]}

    # THE STORE OWNER IS DECLARED BEFORE ANYTHING CAN ASK (2026-07-29).
    #
    # This used to live inside the supervision loop, which was fine while the
    # socket opened last. N3 moved the socket EARLIER — it now binds before
    # backfill so `./trace start` answers in a second — and that opened a
    # window: between the end of backfill and the loop's first iteration,
    # `Brain._owner` was still None, so `_on_owner` took its documented
    # single-threaded fallback and ran the job INLINE on the socket thread.
    #
    # sqlite connections are thread-bound, so a `stats` arriving in that
    # window died with "SQLite objects created in a thread can only be used in
    # that same thread", the handler thread crashed, and `./trace status`
    # printed "the daemon closed the connection". Intermittent, because it
    # depended on when the founder happened to look — which is the worst
    # possible schedule for a bug in the command he uses to check on it.
    #
    # Declared here, before the socket can accept anything, the window does
    # not exist. Same thread either way; only the moment changed.
    if brain is not None:
        brain.claim_owner()

    # THE EYE'S HEALTH, ASKABLE (2026-07-29). A blind eye is the one failure
    # this product may never have quietly, and it JUST HAPPENED: installing
    # the login item moved the daemon from a terminal (which held the Screen
    # Recording grant) to launchd, which spawns the interpreter directly. The
    # grant does not follow, SCShareableContent answers TCC -3801, and the
    # eye is dark. The store recorded it honestly — and nobody would have
    # looked, because everything else reported healthy.
    def _eye_health():
        # Every organ gets its own detailed counters. This used to special-
        # case only the eye, leaving a live ambient ear with zero claims
        # impossible to distinguish from denied microphone, silent audio,
        # failed ASR, or a stalled queue.
        out = _organ_health(supervisor, macro_pass)
        if keeper is not None:
            out["model_server"] = keeper.stats()
        return out

    ask_socket = None
    if brain is not None and ask_socket_path is not None:
        ask_socket = AskSocket(brain, path=ask_socket_path, log=_log,
                               night=night, waking=_waking, eyelid=lid,
                               organs=_eye_health,
                               window_answerer=window_answerer,
                               claim_log=store,
                               claim_log_factory=claim_log_factory)
        _log("ask socket: %s" % ask_socket.start())

    if brain is not None:
        # BOOT BINDS WHAT HAS NO COMPLETION RECEIPT, NOT WHAT HAS A NEWER
        # WALL-CLOCK TIMESTAMP.
        #
        # This used to be `store.all_claims()`. On the founder's store that is
        # 471,503 claims and roughly two hours of binding — and the eye CANNOT
        # WRITE while it runs, because this is the supervision thread. So the
        # daemon's startup cost grew with the size of the memory it was
        # protecting, and every day of successful capture made the next start
        # slower. A memory that becomes harder to start the more it remembers
        # has a bound on how long it can be used.
        #
        # The old timestamp watermark stranded delayed sync and founder-owned
        # claims whose event time fell behind the cursor.  Append sequence plus
        # a receipt for each completed claim closes that hole without decrypting
        # or replaying the lifetime store on every boot.
        graph = getattr(getattr(brain, "_hub", None), "w", None)
        graph = getattr(graph, "graph_store", None)
        # Production reachability evidence for the cockpit. This describes
        # the clock actually driving THIS loop, not merely a class that exists
        # in the source tree.
        if graph is not None and hasattr(graph, "set_meta"):
            graph.set_meta("clock_model", getattr(clock, "model_id", "unknown"))
        existing = _binding_backlog(graph, store)
        if existing:
            waking["total"] = len(existing)

            def _progress(bound, episodes):
                waking["bound"] = bound
                waking["episodes"] = episodes

            filled = brain.backfill(existing, progress=_progress)
            _log("bound %d claim(s) new since the last run -> %d episode(s)"
                 % (filled["bound"], filled["episodes"]))
        advance = getattr(getattr(brain, "_hub", None),
                          "_advance_bind_watermark", None)
        if advance is not None:
            cursor = advance()
            # `Brain.bound` is a cumulative status/settling mark, not a
            # per-boot work counter. Once boot became delta-only it restarted
            # at zero and then counted only the tiny backlog, making the UI
            # say a six-hundred-thousand-claim memory held three memories.
            # The contiguous durable watermark is exactly the number of
            # append positions known to have completed binding; resume from
            # it after repairing any backlog, then add live claims normally.
            if hasattr(brain, "bound"):
                brain.bound = max(int(getattr(brain, "bound", 0)), int(cursor))
            if not existing:
                _log("nothing awaiting bind (append cursor %d)" % cursor)
        # THE RETRIEVAL INDEX IS REQUIRED TO ANSWER HONESTLY. Its first build
        # used to happen inside the first night slice after `waking.done`, so
        # a daemon that had just claimed to be ready stopped answering for
        # minutes under work labelled with a ten-second budget. Build it while
        # the socket's waking gate is still closed: stats answer directly on
        # the socket thread, and content questions are refused as incomplete.
        prepare_index = getattr(brain, "prepare_index", None)
        if prepare_index is not None:
            waking["phase"] = "search index"
            prepare_index()
        skipped = store.unreadable()
        if skipped:
            _log("%d claim(s) in this store were written under a different "
                 "cipher and cannot be read by this process" % skipped)
        # AWAKE. Set last, after the unreadable-claim census, so nothing that
        # runs before this point can be answered against a partial store.
        waking["done"] = True

    stopping = {"now": False}

    def _handle(signum, _frame):
        # Set a flag rather than stopping here: a clean stop writes to the
        # store, and doing sqlite work inside a signal handler is how a
        # shutdown becomes a corruption.
        stopping["now"] = True

    # A wedged owner loop makes the whole product mute, including the stats
    # endpoint that would normally explain what it is doing.  SIGUSR2 is a
    # read-only emergency window into that state: it writes every Python
    # thread's stack to the daemon's existing stderr log and changes no
    # product data.  macOS does not permit attaching a debugger to a hardened
    # signed process, so without this hook a production-only livelock is
    # literally opaque.  The handler is deliberately outside SIGINT/SIGTERM;
    # it diagnoses, it never stops or restarts the daemon.
    try:
        import faulthandler
        faulthandler.register(signal.SIGUSR2, all_threads=True)
    except (AttributeError, OSError, RuntimeError):
        pass

    previous = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous[sig] = signal.signal(sig, _handle)
        except ValueError:
            pass                     # not the main thread (tests may do this)

    ticks = 0
    # Duration is elapsed time, not civil time. A wall correction must not
    # extend or truncate a bounded run; monotonic is deliberately separate
    # from the single civil timestamp source above.
    started = time.monotonic()
    try:
        last_t, last_kind = last_state(store)
        supervisor.boot(last_claim_t_ms=last_t, last_witness_kind=last_kind)
        if brain is not None:
            # THIS thread owns the store. Every question from the socket
            # thread is handed here to run — sqlite connections are bound to
            # their creating thread, so one thread touches the store for
            # both reads and writes, exactly as ClaimQueue already requires
            # for writes.
            pass   # the owner was declared before the socket opened; see below
        while not stopping["now"]:
            if seconds is not None and time.monotonic() - started >= seconds:
                break
            # THE EYELID RUNS FIRST, BEFORE supervisor.tick(). Ordering is
            # load-bearing: park() stops the organ, and a tick() in between
            # would see a not-alive organ and revive it before the night ever
            # got a slice. Opening is the mirror — unpark() only clears the
            # flag, and it is the tick BELOW that actually re-arms the eye
            # through the ordinary witnessed restart path.
            # A CLOCK STEP IS A FACT ABOUT THE DAY, AND IT GETS WRITTEN
            # DOWN. WitnessKind.CLOCK_JUMP has existed in the vocabulary
            # since July and nothing has ever produced one, because nothing
            # was watching the clock. A memory whose timeline moved and left
            # no record of it cannot tell "these two things happened an hour
            # apart" from "someone corrected the clock between them".
            # Call now_ms first: this is the observation that can detect a
            # wall/monotonic divergence. Then drain exactly once.
            now_ms()
            jumps = getattr(clock, "drain_jumps", None)
            if jumps is not None:
                for t_ms, delta_ms in jumps():
                    witness_sink(witness(
                        WitnessKind.CLOCK_JUMP, t_ms, device_id=device_id,
                        detail="wall clock stepped %+0.1fs" % (delta_ms / 1000.0),
                        structured_extra={"delta_ms": delta_ms}))

            if lid is not None:
                lid.tick()
            supervisor.tick()
            claims.drain()
            # THE GUARD LEARNS WHETHER THE EYE IS OPEN (P2, 2026-07-29).
            # `Spotlight.set_capture` had no production caller, so
            # `capture_active` was permanently False and the night's refusal
            # to share the GPU with capture — whose own error text says
            # "capture goes dark silently otherwise (O3)" — could never fire.
            # Signalled every beat from the supervisor's real health, so a
            # benched or dead eye correctly frees the GPU for thinking.
            # set_capture is idempotent, so this costs nothing per beat.
            if spotlight is not None:
                # The ambient ear is CPU-only by law. It remains open while
                # the night uses the model/GPU; only the reading eye owns the
                # scarce capture side of Spotlight.
                spotlight.set_capture(
                    supervisor.health().get("eye") == "alive")
            if brain is not None:
                # Runs any queued questions on THIS thread, then binds on the
                # beat. A question's model call happens here, so it briefly
                # delays the next drain — acceptable, and under the one-model
                # law unavoidable (answering holds the GPU).
                brain.serve()
            # UNDERSTAND THE SCENES THE EYE BANKED, but only after the eye is
            # actually closed AND after queued founder questions. MacroPass
            # independently enforces both capture and GPU exclusion, and
            # polls query preemption between frames. It runs on THIS owner
            # thread: its claims take the same append -> bind path as OCR and
            # never let the capture callback reach sqlite.
            # THE MODEL SERVER IS KEPT ALIVE before anything that needs it.
            # Ollama.app quitting used to be noticed only by the founder
            # asking a question (see llm/ollama_keeper).
            if keeper is not None:
                keeper.tick()
            if macro_pass is not None:
                macro_pass.run()
            # ...and only then, with everything perceptual already serviced,
            # the brain gets whatever is left. One abandonable slice; the
            # watchman refuses outright while the eye is open. Ordering is
            # the design: perception and answering are ahead of reflection in
            # the failure hierarchy, so they are ahead of it in the loop.
            if night is not None:
                night.tick()
            ticks += 1
            time.sleep(tick_seconds)
    finally:
        # HOW THE ORGANS WERE DOING WHILE RUNNING, read before stop() parks
        # them. Read afterwards, every stopped organ reports "dead" — so each
        # clean exit printed `organs: {'eye': 'dead'}` about an eye that had
        # delivered frames until the last second (witnessed 2026-10-07).
        try:
            organs_while_running = supervisor.health()
        except Exception:                                    # noqa: BLE001
            organs_while_running = None
        # Drain BEFORE the clean stop, so claims already captured land
        # ahead of the goodbye witness. A claim timestamped before a
        # clean-stop but appended after it would read, to the gap detector
        # and to any question about that minute, as having happened during
        # a period the system said it was not watching.
        try:
            claims.drain()
            if brain is not None:
                brain.flush(force=True)      # nothing captured stays unbound
        except Exception:
            pass
        # CLOSE ANY OPEN BLIND INTERVAL before the goodbye. Shutting down
        # while the eye is parked would leave an interval that was opened and
        # never closed — a hole running to the end of the store, which reads
        # to every later question as "blind ever since".
        if lid is not None:
            try:
                lid.force_open(now_ms())
            except Exception:
                pass
        if ask_socket is not None:
            ask_socket.stop()
        # ALWAYS the clean goodbye. If this is skipped, the next boot reads
        # the silence as a kill and writes a gap that never happened —
        # the daemon would slander itself on every ordinary shutdown.
        try:
            supervisor.stop()
            # stop() writes the clean-stop witness.  It is appended already;
            # bind it before releasing the one-writer lock so shutdown cannot
            # manufacture a permanent one-claim backlog.
            if brain is not None:
                brain.flush(force=True)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
            lock.release()

    return {
        "store": store_path,
        "encryption": store.encryption(),
        "claims_queue": claims.stats(),
        "claims_before": claims_at_start,
        "claims_after": store.count(),
        "ticks": ticks,
        "organs": supervisor.health(),        # after the clean stop
        "organs_while_running": organs_while_running,
        "eyeless": "eye" not in getattr(supervisor, "_organs", {}),
        "earless": "ear" not in getattr(supervisor, "_organs", {}),
        "brain": brain.stats() if brain is not None else None,
        "ask_socket": str(ask_socket_path) if ask_socket is not None else None,
        "night": night.stats() if night is not None else None,
        "macro": macro_pass.stats() if macro_pass is not None else None,
        "eyelid": lid.stats() if lid is not None else None,
    }


def _encrypt_store(args, keychain) -> int:
    """The CLI half of MASTER #5. All policy lives in text/migrate_crypto."""
    from tracemem.text.migrate_crypto import (MigrationFailed,
                                              MigrationRefused, migrate, plan)

    info = plan(args.store)
    print("store:  %s" % args.store)
    print("now:    %s" % info.get("created_with"))
    print("plan:   %s" % info.get("why"))
    if args.encrypt_plan:
        return 0 if info.get("ok") else 1
    if not info.get("ok"):
        return 1
    if keychain is None:
        print("\nrefusing: --plaintext was passed, so there is no key to "
              "encrypt with.", file=sys.stderr)
        return 2

    from tracemem.container.crypto import SodiumCrypto
    from tracemem.container.keyring import Keyring
    crypto = SodiumCrypto(Keyring(keychain).derive("claim-log"))

    def _daemon_running() -> bool:
        import subprocess
        r = subprocess.run(["pgrep", "-f", "tracemem --eye"],
                           capture_output=True)
        return r.returncode == 0

    try:
        report = migrate(args.store, crypto, daemon_running=_daemon_running)
    except MigrationRefused as e:
        print("\nREFUSED: %s\nNothing was changed." % e, file=sys.stderr)
        return 2
    except MigrationFailed as e:
        print("\nFAILED: %s" % e, file=sys.stderr)
        return 3

    print("\nENCRYPTED.")
    print("  claims      %d -> %d" % (report.claims_in, report.claims_out))
    print("  chain       ok=%s complete=%s"
          % (report.chain_ok, report.chain_complete))
    print("  round-trip  %s" % report.identity_ok)
    print("  cipher      %s" % report.cipher_after)
    print("  backup      %s" % report.backup)
    for note in report.notes:
        print("  note: %s" % note)
    return 0


def _encrypt_graph(args, keychain) -> int:
    if keychain is None:
        print("refusing: --plaintext supplies no graph key", file=sys.stderr)
        return 2
    from tracemem.container.keyring import Keyring
    from tracemem.nodes.migrate_graph_crypto import (
        GraphMigrationFailed, GraphMigrationRefused, migrate_graph,
    )

    def _daemon_running() -> bool:
        import subprocess
        return subprocess.run(
            ["pgrep", "-f", "tracemem --eye"], capture_output=True
        ).returncode == 0

    try:
        report = migrate_graph(
            args.graph, Keyring(keychain).derive("graph-store"),
            daemon_running=_daemon_running)
    except GraphMigrationRefused as exc:
        print("REFUSED: %s\nNothing was changed." % exc, file=sys.stderr)
        return 2
    except GraphMigrationFailed as exc:
        print("FAILED: %s\nThe original remains recoverable." % exc,
              file=sys.stderr)
        return 3
    print("SEMANTIC GRAPH ENCRYPTED.")
    print("  nodes        %d" % report.nodes)
    print("  tombstones   %d" % report.tombstones)
    print("  episodes     %d" % report.episodes)
    print("  pending      %d" % report.pending)
    print("  ledgers      %d" % report.ledger)
    print("  cipher       %s" % report.cipher)
    print("  backup       %s (encrypted and verified)" % report.backup)
    return 0


def reconcile_boundaries(store_path: str, graph_path: str, lock_path: str,
                         keychain=None) -> int:
    """Version quarantined late boundaries without touching immutable raw.

    This is a maintenance write and therefore crosses the same signed-helper
    key boundary and single-writer lock as ingestion and crypto migration.
    The previous standalone tool constructed ``MacKeychain`` directly,
    which cannot read the application-ACL item and encouraged running a
    privileged graph rewrite outside the production security boundary.
    """
    from tracemem.app.daemon import SingleInstanceLock
    from tracemem.app.wiring import _graph_key_for
    from tracemem.binder.reconcile import reconcile_pending
    from tracemem.nodes.sqlite_graph_store import SqliteGraphStore

    lock = SingleInstanceLock(lock_path)
    if not lock.acquire():
        print("refusing: TRACE is running; stop it before reconciling",
              file=sys.stderr)
        return 2
    raw = graph = None
    try:
        config = {} if keychain is None else {"keychain": keychain}
        raw = ClaimLog(store_path, crypto=_crypto_for(config, store_path))
        graph = SqliteGraphStore(
            graph_path, key=_graph_key_for(config, graph_path))
        report = reconcile_pending(graph, raw)
        import json
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    finally:
        if graph is not None:
            graph.close()
        if raw is not None:
            raw._db.close()
        lock.release()


def ingest_past(root: str, store_path: str, graph_path: str, lock_path: str,
                keychain=None, clock=None) -> int:
    """THE COLD-START PASS (P4). Read the user's OWN corpus into memory.

    A brain with no memory is useless on day one, and this product needs weeks
    of accumulation before it can derive anything. The founder's chosen answer
    is to give it a PAST: broad-index the files he already has, so the night
    has years of well-formed material to chew on instead of five days of
    browser OCR. `src/tracemem/owned/` was built and tested for exactly this
    and had no caller.

    OWNED claims travel the SAME path LIVED ones do — log → bind → birth — so
    the two hemispheres share segmentation, activation and the night. Nothing
    here is a second writer.

    WHY IT REFUSES BESIDE A DAEMON: the store has exactly one writer by
    construction (WO-093), and every durability property it declares is stated
    about a single connection. Two processes appending would race the hash
    chain's tail read and mint two claims naming the same predecessor.
    """
    from tracemem.app.daemon import SingleInstanceLock
    from tracemem.app.wiring import build
    from tracemem.owned.ingest import ingest_and_deepen
    from tracemem.supplier.embedder_st import SentenceTransformerEmbedder

    root_path = Path(root).expanduser()
    if not root_path.is_dir():
        print("not a directory: %s" % root_path)
        return 2

    lock = SingleInstanceLock(lock_path)
    if not lock.acquire():
        print("a daemon holds %s — stop it first (`./trace stop`).\n"
              "The store has ONE writer by construction; ingesting beside a "
              "running daemon would race the hash chain." % lock_path)
        return 1
    try:
        # The keychain is INJECTED, never constructed here. There is exactly
        # one construction site in this program — in main(), where a human
        # invoked it on purpose — and a regression guard
        # (test_only_the_cli_may_reach_the_real_keychain) enforces that,
        # because a second site once made the whole test suite read the
        # founder's real login keychain.
        crypto = (None if keychain is None
                  else _crypto_for({"keychain": keychain}, store_path))
        Path(store_path).parent.mkdir(parents=True, exist_ok=True)
        store = ClaimLog(store_path, crypto=crypto)
        before = store.count()

        # No reasoner: the broad pass and the deep pass are both deterministic
        # readers. A model here would be a cost with nothing to buy.
        class _NoReasoner:
            def complete(self, prompt):
                raise AssertionError(
                    "ingestion never reaches a model — if this fired, "
                    "something asked the corpus pass to think")

        hub = build({"claim_log": store, "graph_store_path": graph_path,
                     "clock": clock or SteadyClock(),
                     "keychain": keychain,
                     "embedder": SentenceTransformerEmbedder(),
                     "reasoner": _NoReasoner()})
        _log("indexing %s — this reads your files and writes TEXT, never "
             "copies them" % root_path)
        report = ingest_and_deepen(hub, str(root_path), device_id="mac-1")
    finally:
        lock.release()

    broad, deep = report["broad"], report["deep"]
    print("root:      %s" % root_path)
    print("files:     %d indexed, %d skipped"
          % (broad["files_indexed"], broad["files_skipped"]))
    print("resumed:   %d unchanged source(s) already complete"
          % broad.get("files_resumed", 0))
    print("history:   %d visit(s) from %d browser store(s)"
          % (broad.get("history_visits", 0),
             broad.get("history_sources", 0)))
    if broad.get("history_unreadable", 0):
        print("INCOMPLETE: %d browser store(s) could not be read; grant TRACE "
              "access and run the cold start again."
              % broad["history_unreadable"])
    if broad.get("permission_denied", 0):
        print("INCOMPLETE: macOS denied %d selected folder(s); grant TRACE "
              "Files & Folders access and run the cold start again."
              % broad["permission_denied"])
    print("claims:    %d -> %d" % (before, store.count()))
    print("deep:      %d claim(s) mined from hungry nodes" % deep["deep_claims"])
    print("episodes:  %d" % deep.get("episodes", broad.get("episodes", 0)))
    print("\nYour past is in memory. Start watching with:  ./trace start")
    return 0


def main(argv=None) -> int:
    # HAND THE INTERPRETER LOCK OVER FASTER (2026-10-09). A period answer
    # read 28,310 claims in 61.5s of wall time and 9.9s of its own CPU: every
    # sqlite step and every crypto call releases the GIL, and getting it back
    # from the busy eye/owner threads waited up to the default 5ms switch
    # interval each time. A shorter interval costs a little switching and
    # gives the question back its time.
    sys.setswitchinterval(0.0005)
    parser = argparse.ArgumentParser(prog="tracemem")
    parser.add_argument("--store", default=str(DEFAULT_STORE))
    parser.add_argument(
        "--graph", default=str(DEFAULT_GRAPH),
        help="where the DERIVED graph lives (--ask only). Separate file from "
             "the claim store on purpose: raw is immutable and append-only, "
             "the graph is the only mutable tier — one file per durability "
             "story, and a corrupt graph is rebuildable from raw while the "
             "reverse is not true.")
    parser.add_argument(
        "--encrypt-plan", action="store_true",
        help="say what encrypting this store WOULD do, and change nothing")
    parser.add_argument(
        "--encrypt", action="store_true",
        help="MIGRATION: re-write every claim under a Keychain-held key. "
             "Takes a verified backup first, builds the new store beside the "
             "old one, verifies count/chain/identity/cipher, and only then "
             "swaps. The plaintext original is kept, not removed.")
    parser.add_argument(
        "--encrypt-graph", action="store_true",
        help="MIGRATION: encrypt the semantic graph under its own Keychain-"
             "derived purpose key; verifies an encrypted backup and semantic "
             "round-trip before swapping")
    parser.add_argument(
        "--reconcile-boundaries", action="store_true",
        help="OFFLINE MAINTENANCE: version quarantined late episode "
             "boundaries atomically; immutable raw claims are only read")
    parser.add_argument("--expect", action="store_true",
                        help="what TRACE can and cannot do for you today")
    parser.add_argument("--settings", nargs="*", default=None,
                        metavar="ARG",
                        help="what you have chosen; "
                             "'block app:<id>' / 'unblock site:<host>' to change it")
    parser.add_argument("--export", metavar="PATH", default=None,
                        help="write every claim to PATH as plain JSON Lines — "
                             "your memory in a form that outlives this program")
    parser.add_argument("--lock", default=str(DEFAULT_LOCK))
    parser.add_argument("--seconds", type=float, default=None,
                        help="bounded run; omit to run until SIGINT/SIGTERM")
    parser.add_argument("--tick", type=float, default=TICK_SECONDS)
    parser.add_argument("--device", default="mac-1",
                        help="stable device identity written on observations")
    parser.add_argument(
        "--eye", action="store_true",
        help="open the eye: capture this Mac's screen for real. OFF BY "
             "DEFAULT — see the note in main().")
    parser.add_argument(
        "--ear", action="store_true",
        help="open the ambient ear: continuously capture short microphone "
             "chunks through the signed helper, transcribe locally, destroy "
             "the waveform, and retain TEXT only")
    parser.add_argument(
        "--phone", action="store_true",
        help="open the phone's door (F-PILLARS, F-LAN): listen on the local "
             "network for this user's paired iPhone and take its camera "
             "claims. LAN peers only; the phone proves it holds the phone key")
    parser.add_argument(
        "--pair-phone", action="store_true",
        help="draw the pairing code the iPhone app scans (signed helper only)")
    parser.add_argument(
        "--pair-phone-text", action="store_true",
        help="the pairing code as terminal art instead of a window")
    parser.add_argument("--falsify-queries", metavar="FILE", default=None,
                        help="run the falsification experiment on FILE")
    parser.add_argument("--falsify-author", action="store_true",
                        help="write the falsification question template")
    parser.add_argument("--falsify-max-claims", type=int, default=0,
                        help="rehearse on N sampled claims (never a verdict)")
    parser.add_argument("--revoke-phone", metavar="ID", default=None,
                        help="cut one paired phone off (ids: --list-phones)")
    parser.add_argument("--list-phones", action="store_true",
                        help="paired phone ids and whether each is revoked")
    parser.add_argument(
        "--ask", action="store_true",
        help="run the BRAIN: bind captured claims into episodes and nodes, "
             "and serve questions on a UNIX socket. Without this the daemon "
             "only captures — nothing is ever bound and nothing can be asked.")
    parser.add_argument(
        "--socket", default=str(Path.home() / ".trace" / "ask.sock"),
        help="where the ask socket lives (--ask only). Never a TCP port: "
             "the only listener is the phone's (--phone, F-LAN).")
    parser.add_argument(
        "--model", default="gemma3:12b-it-qat",
        help="the local model that answers (--ask only)")
    parser.add_argument(
        "--ingest", metavar="PATH", default=None,
        help="COLD START: broad-index your own files under PATH into memory, "
             "then deep-mine where the new nodes are hungry, and exit. A path "
             "is required and has no default — pointing a reader at a disk is "
             "the user's consent decision, never ours. Refuses to run while a "
             "daemon holds the store (one writer, always).")
    parser.add_argument(
        "--no-night", action="store_true",
        help="do not let the brain think (--ask only). The night is "
             "CONTINUOUS by default: one short, abandonable slice at a time, "
             "and never while the eye is open. This flag exists so a run can "
             "be measured with reflection switched off.")
    parser.add_argument(
        "--battery-night", action="store_true",
        help="let the brain think on battery too. Off by default: thinking "
             "holds the GPU, and the night is never urgent.")
    parser.add_argument(
        "--plaintext", action="store_true",
        help="write claims UNENCRYPTED. The store already records which "
             "cipher wrote it, so this can never be mistaken later — but it "
             "means anyone holding the file can read your screen.")
    args = parser.parse_args(argv)

    # Constructed once, before any producer. Every production consumer below
    # receives this exact object (or its now_ms method).
    clock = SteadyClock()

    # The signed helper passes only the sealed package root. One runtime is
    # shared by the eye's immediate turns and the night's durable replays, so
    # discovery, trust decisions, and the one-heavy-helper lock cannot drift.
    helper_runtime = None
    helper_root = os.environ.get("TRACE_HELPERS_PATH")
    if helper_root:
        try:
            from tracemem.extraction.helper_runtime import HelperRuntime
            helper_runtime = HelperRuntime.discover(helper_root)
        except Exception as exc:                            # noqa: BLE001
            _log("HELPERS DISABLED: %s" % exc)

    # One volatile bridge between the live eye and the macro model.  It is
    # constructed only when both ends exist; a capture-only process must not
    # retain pixels it can never understand.  MacroBuffer never writes them.
    macro_buffer = None
    if args.eye and args.ask:
        from tracemem.extraction.macro_buffer import MacroBuffer
        macro_buffer = MacroBuffer()

    # THE EYE IS OPT-IN, and that is a deliberate choice rather than an
    # unfinished one. Starting it captures the founder's actual screen, OCRs
    # it, and writes the text to disk. A daemon that began recording
    # someone's screen the first time they typed its name, before they had
    # decided to be recorded, would be exactly the product this one refuses
    # to be. The flag is how consent gets expressed once, explicitly, by the
    # person whose screen it is.
    organs = []
    if args.eye:
        from tracemem.app.eye_organ import EyeOrgan
        # THE RULES THE FOUNDER WROTE, handed to the thing that captures.
        # Loading them here — not inside the organ — keeps the organ testable
        # and keeps a corrupt settings file from being read on a capture
        # thread. `Settings.load` fails toward recording LESS and says so.
        from tracemem.app import settings as _settings
        # A CORRUPT SETTINGS FILE MUST NOT MEAN "EXCLUDE NOTHING".
        #
        # `load()` returns empty exclusion lists when it cannot parse the
        # file, and empty excludes nothing — so a typo in settings.json used
        # to silently start capturing whatever the founder had told TRACE to
        # ignore. That is the failure this module's own docstring forbids in
        # so many words. A fresh install with NO file is a different and
        # genuinely safe state, so only an unreadable one refuses.
        _first = _settings.load()
        if _first.unreadable:
            _log("REFUSING TO WATCH: %s" % _first.problem)
            _log("   Your exclusions cannot be read, so TRACE does not know "
                 "what it is meant to ignore.")
            _log("   Fix or delete %s, then start again."
                 % _settings.SETTINGS_PATH)
            return 2
        # LIVE, not frozen at boot — see app/settings.SettingsWatcher.
        _watch = _settings.SettingsWatcher()
        _rules = _watch.blocklist()
        if _rules.rules:
            _log("ignoring %d rule(s) you set: %s"
                 % (len(_rules.rules),
                    ", ".join(r.describe() for r in _rules.rules)))
        # Eval-only frames beside the store (gold/, git-ignored), unless
        # "gold_frames": false. See extraction/gold_bank.py.
        _gold_dir = None
        try:
            import json as _json
            _raw = _json.loads(_settings.SETTINGS_PATH.read_text()) \
                if _settings.SETTINGS_PATH.exists() else {}
        except Exception:                                   # noqa: BLE001
            _raw = {}
        if _raw.get("gold_frames") is not False:
            _gold_dir = Path(args.store).resolve().parent.parent / "gold" / "frames"
        organs.append(lambda submit: EyeOrgan(
            submit=submit, blocklist=_watch, clock=clock.now_ms,
            device_id=args.device, helper_runtime=helper_runtime,
            macro_buffer=macro_buffer, gold_dir=_gold_dir))
    if args.ear:
        from tracemem.app.ear_organ import EarOrgan
        organs.append(lambda submit: EarOrgan(
            submit=submit, clock=clock.now_ms, device_id=args.device))
    organs = organs or None

    # THIS is the seam where production meets the real Keychain — the CLI,
    # the one entry point a human actually invokes, and nowhere deeper.
    # Constructing it further down would drag every test that calls run()
    # into the founder's login keychain (it did, once; see wiring._crypto_for).
    keychain = None
    if not args.plaintext:
        inherited = os.environ.get("TRACE_MASTER_KEY_FD")
        if inherited is not None:
            from tracemem.container.inherited_keychain import InheritedKeychain
            keychain = InheritedKeychain(int(inherited))
        else:
            from tracemem.container.keychain_mac import MacKeychain
            keychain = MacKeychain()

    # Dispatched HERE, below the one keychain construction site, so the
    # cold-start pass gets the same crypto decision every other path gets and
    # the single-site invariant stays true.
    if args.expect:
        from tracemem.app.onboarding import render as _expect
        print(_expect())
        return 0

    if args.settings is not None:
        from tracemem.app.settings import (SETTINGS_PATH, UnknownRuleKind,
                                           UnknownSetting, add_rule, describe,
                                           load, remove_rule, set_value,
                                           settable)
        # A SETTINGS SCREEN YOU CAN ONLY READ IS A FILE PATH WITH EXTRA STEPS.
        # `./trace settings block app:com.bank.app` and `unblock` write
        # through the same code the app's buttons call, so the two surfaces
        # cannot drift into disagreeing about what is excluded.
        argv = list(args.settings)
        if argv:
            verb, rest = argv[0], argv[1:]
            if verb == "set":
                if len(rest) < 2:
                    print("usage: ./trace settings set <name> <value>")
                    for k, why in sorted(settable().items()):
                        print("   %-22s %s" % (k, why))
                    return 1
                try:
                    st = set_value(rest[0], " ".join(rest[1:]))
                except (UnknownSetting, ValueError) as e:
                    print("  ! %s" % e)
                    return 1
                print(describe(st))
                print("\n  changed. The eye and the night read this live — "
                      "no restart.")
                return 0
            if verb not in ("block", "unblock") or not rest:
                print("usage: ./trace settings [block|unblock] "
                      "app:<bundle-id> | site:<host> | title:<text>")
                print("       ./trace settings set <name> <value>")
                return 1
            spec = " ".join(rest)
            kind, _, value = spec.partition(":")
            if not value:
                print("  ! say WHICH kind: app:, site: or title:")
                return 1
            try:
                fn = add_rule if verb == "block" else remove_rule
                fn(kind.strip(), value.strip())
            except UnknownRuleKind as e:
                print("  ! %s" % e)
                return 1
            except ValueError as e:
                print("  ! %s" % e)
                return 1
            print("  %s %s:%s"
                  % ("now ignoring" if verb == "block" else "no longer ignoring",
                     kind.strip(), value.strip()))
            print("  the eye picks this up within a beat — no restart.")
            return 0
        st = load()
        print(describe(st))
        print("\nchange it:")
        print("  ./trace settings block app:com.example.bank")
        print("  ./trace settings block site:bank.com")
        print("  ./trace settings unblock site:bank.com")
        for k, why in sorted(settable().items()):
            print("  ./trace settings set %-22s # %s" % (k, why))
        print("\nor edit: %s" % SETTINGS_PATH)
        return 0

    if args.export:
        from tracemem.app.export import export_claims
        # NO local `from ... import ClaimLog` here, and this is not style.
        # ClaimLog is imported at module level; re-importing it INSIDE main()
        # makes the name local to the whole function under Python's scoping
        # rules, regardless of which branch runs — so `store = ClaimLog(...)`
        # in the --ask branch, 40 lines below, raised UnboundLocalError every
        # single time. Added 2026-07-30 (commit 0376163); the running daemon
        # was started 2026-07-29 and therefore never executed this file
        # again, so `--ask` was unstartable for three weeks with a fully
        # green suite. It also meant the ONE remedy the board prescribed for
        # the 2026-08-05 blindness — `./trace stop && ./trace start` — would
        # itself have died on launch.
        crypto = _crypto_for({} if keychain is None else {"keychain": keychain},
                             args.store)
        rep = export_claims(ClaimLog(args.store, crypto=crypto), args.export)
        print("exported %d claim(s) -> %s" % (rep["claims"], rep["path"]))
        if rep["unreadable"]:
            print("%s claim(s) could not be read and are NOT in the export"
                  % rep["unreadable"])
        print("\nTHIS FILE IS PLAINTEXT. Anyone holding it can read your "
              "memory —\nwhich is the point of an export, and worth knowing.")
        return 0

    if args.encrypt or args.encrypt_plan:
        return _encrypt_store(args, keychain)
    if args.encrypt_graph:
        return _encrypt_graph(args, keychain)
    if args.reconcile_boundaries:
        return reconcile_boundaries(
            args.store, args.graph, args.lock, keychain=keychain)

    if args.ingest is not None:
        return ingest_past(args.ingest, store_path=args.store,
                           graph_path=args.graph, lock_path=args.lock,
                           keychain=keychain, clock=clock)

    if args.falsify_author or args.falsify_queries:
        from tracemem.evalx.falsify_cli import run as _falsify
        argv = ["--store", args.store]
        if args.falsify_author:
            argv.append("--author")
        else:
            argv += ["--queries", args.falsify_queries]
            if args.falsify_max_claims:
                argv += ["--max-claims", str(args.falsify_max_claims)]
        return _falsify(argv, keychain=keychain, store_path=args.store)

    if args.pair_phone_text:
        args.pair_phone = True
    from tracemem.container.phone_link import PhoneRegistry
    _phones = PhoneRegistry(Path.home() / ".trace" / "phones.json")
    if args.revoke_phone:
        ok = _phones.revoke(args.revoke_phone, clock.now_ms())
        print(("revoked %s: it can no longer sync" if ok else
               "no active phone %s") % args.revoke_phone)
        return 0 if ok else 1
    if args.list_phones:
        for device, entry in sorted(_phones.all().items()):
            print("%s  %s" % (device, "REVOKED" if entry.get("revoked_ms")
                              else "active"))
        return 0
    if args.pair_phone or args.phone:
        if keychain is None:
            print("the phone needs the per-user key; --plaintext has none")
            return 2
        from tracemem.container.keyring import Keyring
        from tracemem.container.phone_link import phone_key
        _phone_key = phone_key(Keyring(keychain))
        if args.pair_phone:
            from tracemem.container.phone_pairing import pair
            print(pair(_phone_key, _phones, clock.now_ms(),
                       text_mode=args.pair_phone_text))
            return 0
        from tracemem.container.phone_link import PhoneLinkOrgan
        organs = list(organs or [])
        organs.append(lambda submit: PhoneLinkOrgan(
            submit=submit, key=_phone_key, clock=clock.now_ms,
            registry=_phones, log=_log))

    # THE BRAIN IS OPT-IN, for a different reason than the eye. It is not a
    # consent question — it touches nothing new — it is a WEIGHT question:
    # building it loads the sentence-transformers embedder into this process
    # and answering reaches a local model. A capture daemon that must not
    # miss a second of perception should not carry either unless the founder
    # asked for an askable memory in this run.
    brain = None
    sock_path = None
    if args.ask:
        from tracemem.app.brain import Brain
        from tracemem.app.wiring import build
        from tracemem.llm.reasoner_ollama import OllamaReasoner
        from tracemem.supplier.embedder_st import SentenceTransformerEmbedder

        # Resolve the mouth once for the whole boot.  The hub and the fast
        # period-answer path are separate adapters, but they must never
        # silently speak through different models.
        chosen_model = _chosen_model(args.model)
        from tracemem.llm.ollama_keeper import OllamaKeeper
        keeper = OllamaKeeper(log=_log)
        crypto = None if args.plaintext else _crypto_for(
            {"keychain": keychain}, args.store)
        # ONE ClaimLog, shared by the brain and the supervision loop. Two
        # connections to one sqlite file would be two writers, and every
        # durability property the store declares is stated about a single
        # one.
        Path(args.store).parent.mkdir(parents=True, exist_ok=True)
        store = ClaimLog(args.store, crypto=crypto)
        # THE STORE CHECKS ITSELF ON THE WAY UP.
        #
        # `verify_chain` has existed and been thorough since July, and the
        # DAEMON never called it — only the snapshotter, the crypto migration
        # and a torture tool did. So a running TRACE never once checked
        # whether its own hash chain still held: disk rot, a half-written
        # row, a tampered claim, all invisible.
        #
        # The never-lie promise rests on this log. An answer cited from a
        # claim that was altered after it was written is a lie the product
        # cannot detect and would defend.
        #
        # A TAIL, NOT THE WHOLE STORE. Boot is not the place for a walk over
        # 600k rows — that is the night's job. The tail is what a crash or a
        # bad shutdown would have damaged, and `complete: False` travels with
        # the result so nobody can mistake a tail pass for a clean bill of
        # health.
        #
        # IT REPORTS, IT DOES NOT REFUSE. A broken link does not mean the
        # rest is worthless, and a memory that will not start is worse than
        # one that starts and says what is wrong with it.
        _chain = store.verify_chain(tail=BOOT_CHAIN_TAIL)
        if not _chain.get("ok"):
            if _chain.get("reason") == "unchained":
                _log("note: the store has rows from before the hash chain "
                     "existed; they cannot be verified either way.")
            else:
                _log("!! THE STORE'S HASH CHAIN IS BROKEN at claim %s (%s)"
                     % (_chain.get("broken_at"), _chain.get("reason")))
                _log("   Something altered or lost a claim after it was "
                     "written. Your memories are still here; their proof of "
                     "integrity is not.")
        # THE GRAPH GETS A HOME ON DISK (P1). Without this the brain derives
        # beliefs all night and loses every one of them at shutdown; only the
        # entity tier survives, because backfill rebuilds that from raw.
        hub = build({
            "claim_log": store,
            "graph_store_path": args.graph,
            "clock": clock,
            "keychain": keychain,
            "embedder": SentenceTransformerEmbedder(),
            # THE MOUTH THE FOUNDER CHOSE, if he chose one. `settings.model`
            # was a setting nobody read: written to the file, printed on the
            # settings screen, and the reasoner was built from the CLI flag
            # regardless. Empty still means "whatever the CLI was told", so a
            # settings file that has never been touched cannot repoint the
            # model behind anyone's back.
            "reasoner": OllamaReasoner(chosen_model,
                                       on_refused=keeper.on_refused),
            "answer_model": chosen_model,
            # THE NIGHT'S ALLOWANCE, when the founder has named one.
            #
            # `night_tokens` had no consumer anywhere. It does now — but ONLY
            # when it is non-zero, and that asymmetry is deliberate. The
            # shipped default is 0, and TokenLedger.can_afford is
            # `spent + n <= cap`, so passing 0 straight through would make
            # every night unaffordable and stop the brain thinking, silently,
            # for everyone who never opened the settings screen. Zero here
            # means "untouched", not "forbid everything"; a number means what
            # it says.
            **({"night_token_cap": _night_token_budget()}
               if _night_token_budget() > 0 else {}),
        })
        # THE STAMP THAT NEVER GOES BACKWARDS. See capture/steady_clock.
        # This was `int(time.time() * 1000)` — the raw wall clock — so an NTP
        # correction backwards filed a person's afternoon before their
        # morning, silently, and every range query and episode boundary drawn
        # from those stamps was wrong in a way nothing could detect.
        now_ms = clock.now_ms
        brain = Brain(hub, store.append, now_ms,
                      helper_runtime=helper_runtime)

        # THE PERIOD PATH, which does not wait for the brain to wake.
        # "What was I doing yesterday" needs the time-indexed claim table and
        # a mouth; it needs no episode, node or edge. Gating it behind
        # backfill made the commonest question the slowest operation the
        # daemon performs. Its own connection per question — see
        # WindowAnswerer — never writes, so the one-writer law is untouched.
        from tracemem.app.window_answer import WindowAnswerer

        def _reader():
            """A short-lived READ connection. Never appends."""
            return ClaimLog(args.store, crypto=crypto)

        window_answerer = WindowAnswerer(
            claim_log_factory=_reader,
            reasoner=OllamaReasoner(chosen_model,
                                    on_refused=keeper.on_refused),
            clock=hub.w.clock, spotlight=hub.w.spotlight, log=_log)

        # THE BRAIN GETS PERMISSION TO THINK (P2, 2026-07-29). Until this
        # line, `run_night()` had zero production callers and had never
        # executed once outside a test — so the entire node tier of the
        # napkin, the product's actual differentiator, had no evidence for or
        # against it. The watchman owns WHEN (never while the eye is open,
        # not on battery, throttled) and FOR HOW LONG (one abandonable
        # slice); the night itself is unchanged.
        night = None
        if not args.no_night:
            from tracemem.app.night_watchman import NightWatchman
            from tracemem.capture.machine_sense import read_power
            night = NightWatchman(
                brain, hub.w.spotlight, now_ms, power_fn=read_power,
                # THE FLAG *OR* THE SETTING, checked per night rather than
                # frozen at boot. `night_on_battery` was written, displayed,
                # and read by nobody.
                require_ac=(lambda: not (args.battery_night
                                         or _night_on_battery())),
                log=_log, model_ready=lambda: keeper.up is not False)

        macro_pass = None
        if macro_buffer is not None:
            from tracemem.app.macro_pass import MacroPass
            from tracemem.extraction.vlm_macro import VlmMacroEye
            macro_pass = MacroPass(
                macro_buffer, VlmMacroEye(model=chosen_model), brain.absorb,
                hub.w.spotlight, clock, args.device, model=chosen_model,
                log=_log, ready=lambda: keeper.up is not False)

        sock_path = Path(args.socket).expanduser()
        report = run(store_path=args.store, seconds=args.seconds,
                     lock_path=args.lock, tick_seconds=args.tick,
                     organs=organs, brain=brain, store=store,
                     ask_socket_path=sock_path, window_answerer=window_answerer,
                     claim_log_factory=_reader,
                     night=night, spotlight=hub.w.spotlight, clock=clock,
                     macro_pass=macro_pass, keeper=keeper,
                     device_id=args.device)
    else:
        report = run(store_path=args.store, seconds=args.seconds,
                     lock_path=args.lock, tick_seconds=args.tick,
                     organs=organs, keychain=keychain, clock=clock,
                     device_id=args.device)

    print("store:   %s" % report["store"])
    crypto = report["encryption"]
    if crypto["plaintext"]:
        # Said every run, at the top, in the plainest words available. A
        # person whose screen is being written to disk in the clear should
        # never have to read source code to find that out.
        print("crypto:  NONE — claims are stored in PLAINTEXT and anyone "
              "with this file can read them")
    else:
        print("crypto:  %s" % crypto["cipher"])
    if crypto["mixed"]:
        print("WARNING: this store was created with %r and is being opened "
              "with %r — older claims are unreadable to this process"
              % (crypto["created_with"], crypto["cipher"]))
    print("claims:  %d -> %d" % (report["claims_before"], report["claims_after"]))
    print("ticks:   %d" % report["ticks"])
    running = report.get("organs_while_running")
    if running:
        print("organs:  %s while running (stopped cleanly at exit)" % running)
    else:
        print("organs:  %s" % (report["organs"] or "NONE — running EYELESS"))
    if report["eyeless"] and report["earless"]:
        print("\nNO SENSES OPEN: neither --eye nor --ear was given, so this run")
        print("witnessed its own start and stop and captured nothing. That is")
        print("recorded honestly rather than hidden.")
    elif report["eyeless"]:
        # The old text claimed "no organ implements the supervisor's contract
        # yet" — false since the eye and ear were built, and printed under an
        # ear-only run that HAD a live organ (witnessed 2026-10-07).
        print("EYELESS: the screen is not being watched (--eye not given).")
    if report["earless"]:
        print("EARLESS: ambient sound is not being transcribed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
