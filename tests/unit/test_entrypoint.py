"""ORACLE for the entrypoint (WO-091).

Before this existed, `Supervisor` had zero production callers: a fully
tested lifecycle nothing could start. The suite was green about a program
that could not run.

These tests drive the REAL entrypoint against a REAL persistent store — no
fakes for the store, the lock, or the witnesses — because the whole point of
the work order is that the thing starts for real. The one thing they do not
do is capture, since no organ implements the supervisor's contract yet; that
absence is asserted rather than papered over.
"""
import sqlite3

import pytest

from tracemem.__main__ import _organ_health, last_state, main, run
from tracemem.app.witness import WitnessKind
from tracemem.text.claim_log import ClaimLog
from tests.helpers import make_claim

MINUTE = 60_000
HOUR = 3_600_000


def paths(tmp_path):
    return (str(tmp_path / "store.sqlite3"), str(tmp_path / "trace.lock"))


def kinds(store_path):
    return [(c.structured or {}).get("witness")
            for c in ClaimLog(store_path).all_claims()]


# -- it starts, and it leaves the store behind ----------------------------
def test_a_bounded_run_boots_and_stops_cleanly(tmp_path):
    store, lock = paths(tmp_path)
    report = run(store_path=store, seconds=0.3, lock_path=lock,
                 tick_seconds=0.05)

    assert report["claims_before"] == 0
    assert report["claims_after"] == 2
    assert kinds(store) == [WitnessKind.START.value,
                            WitnessKind.CLEAN_STOP.value]


def test_the_store_is_created_and_never_truncated(tmp_path):
    """tools/first_beat.py deletes its store on every invocation — correct
    for a probe, and the reason no run in this project's history had ever
    produced a persistent store. A daemon must not do that."""
    store, lock = paths(tmp_path)
    run(store_path=store, seconds=0.2, lock_path=lock, tick_seconds=0.05)
    run(store_path=store, seconds=0.2, lock_path=lock, tick_seconds=0.05)

    assert ClaimLog(store).count() == 4
    assert kinds(store).count(WitnessKind.START.value) == 2


def test_the_store_directory_is_created_if_absent(tmp_path):
    store = str(tmp_path / "nested" / "deeper" / "store.sqlite3")
    run(store_path=store, seconds=0.1,
        lock_path=str(tmp_path / "l"), tick_seconds=0.05)
    assert ClaimLog(store).count() == 2


def test_the_daemons_own_witnesses_verify_on_the_hash_chain(tmp_path):
    """The chain seals real daemon output, not fixtures."""
    store, lock = paths(tmp_path)
    run(store_path=store, seconds=0.2, lock_path=lock, tick_seconds=0.05)
    report = ClaimLog(store).verify_chain()
    assert report["ok"] is True and report["checked"] == 2


# -- boot honesty ----------------------------------------------------------
def test_a_short_gap_after_a_clean_stop_is_not_slandered_as_a_kill(tmp_path):
    """Restarting seconds later must NOT write a gap. If it did, every
    ordinary restart would put a hole in the record that never happened —
    the daemon would slander itself on each shutdown."""
    store, lock = paths(tmp_path)
    run(store_path=store, seconds=0.2, lock_path=lock, tick_seconds=0.05)
    run(store_path=store, seconds=0.2, lock_path=lock, tick_seconds=0.05)

    assert WitnessKind.GAP.value not in kinds(store)
    assert WitnessKind.KILL_DETECTED.value not in kinds(store)


def test_a_long_unexplained_silence_is_witnessed_at_boot(tmp_path):
    """The other direction, and the one that matters for honesty: claims
    from hours ago with no clean stop must produce a kill-detected witness
    BEFORE anything else happens."""
    store, lock = paths(tmp_path)
    import time
    now_ms = int(time.time() * 1000)
    seed = ClaimLog(store)
    seed.append(make_claim(text="yesterday's work", t_ms=now_ms - 6 * HOUR))

    run(store_path=store, seconds=0.2, lock_path=lock, tick_seconds=0.05)

    got = kinds(store)
    assert WitnessKind.KILL_DETECTED.value in got
    assert got.index(WitnessKind.KILL_DETECTED.value) < \
        got.index(WitnessKind.START.value), "the hole is written down first"


def test_last_state_reads_the_claims_not_a_sidecar(tmp_path):
    store, _lock = paths(tmp_path)
    log = ClaimLog(store)
    assert last_state(log) == (None, None)

    log.append(make_claim(text="a", t_ms=1000))
    t_ms, kind = last_state(log)
    assert t_ms == 1000 and kind is None

    from tracemem.app.witness import witness
    log.append(witness(WitnessKind.CLEAN_STOP, t_ms=2000))
    t_ms, kind = last_state(log)
    assert t_ms == 2000 and kind is WitnessKind.CLEAN_STOP


def test_boot_state_never_decrypts_the_lifetime_store(tmp_path, monkeypatch):
    """Startup cost is constant even after a lifetime of observations."""
    store, _lock = paths(tmp_path)
    log = ClaimLog(store)
    for i in range(2000):
        log.append(make_claim(text="ordinary %d" % i, t_ms=1000 + i))
    from tracemem.app.witness import witness
    log.append(witness(WitnessKind.CLEAN_STOP, t_ms=5000))

    monkeypatch.setattr(
        log, "all_claims",
        lambda: (_ for _ in ()).throw(
            AssertionError("boot decrypted the lifetime store")))

    assert last_state(log) == (5000, WitnessKind.CLEAN_STOP)


# -- one daemon only -------------------------------------------------------
def test_a_second_daemon_refuses_to_start(tmp_path):
    """Two daemons would both capture, both witness, and disagree about the
    same seconds."""
    from tracemem.app.daemon import SingleInstanceLock
    store, lock = paths(tmp_path)

    held = SingleInstanceLock(lock)
    assert held.acquire() is True
    try:
        with pytest.raises(SystemExit) as exc:
            run(store_path=store, seconds=0.2, lock_path=lock)
        assert "another tracemem daemon" in str(exc.value)
    finally:
        held.release()


def test_the_lock_is_released_so_the_next_run_can_start(tmp_path):
    store, lock = paths(tmp_path)
    run(store_path=store, seconds=0.1, lock_path=lock, tick_seconds=0.05)
    run(store_path=store, seconds=0.1, lock_path=lock, tick_seconds=0.05)


# -- what it honestly does NOT do -----------------------------------------
def test_it_runs_eyeless_and_says_so(tmp_path):
    """No class in src/ implements the organ contract (name/start/stop/
    alive) — `def alive` appears only in tests. Running eyeless is a
    first-class witnessed state, not a pretence that capture is happening."""
    store, lock = paths(tmp_path)
    report = run(store_path=store, seconds=0.1, lock_path=lock,
                 tick_seconds=0.05)
    assert report["eyeless"] is True
    assert report["organs"] == {}


def test_a_registered_organ_is_supervised(tmp_path):
    """The seam is real even though nothing production-side fills it yet —
    so wiring the eye is a matter of writing one adapter, not of changing
    this loop."""
    class FakeOrgan:
        name = "eye"

        def __init__(self):
            self.started = 0
            self._alive = False

        def start(self):
            self.started += 1
            self._alive = True

        def stop(self):
            self._alive = False

        def alive(self):
            return self._alive

    organ = FakeOrgan()
    store, lock = paths(tmp_path)
    report = run(store_path=store, seconds=0.2, lock_path=lock,
                 organs=[organ], tick_seconds=0.05)

    assert organ.started >= 1
    assert report["eyeless"] is False
    assert report["organs"] == {"eye": "dead"}   # stopped by the clean stop


def test_the_cli_runs(tmp_path):
    store, lock = paths(tmp_path)
    assert main(["--store", store, "--lock", lock, "--plaintext",
                 "--seconds", "0.1", "--tick", "0.05"]) == 0


# -- the capture hand-off (WO-093) ----------------------------------------
def test_claims_submitted_from_another_thread_land_in_the_store(tmp_path):
    """The daemon drains the queue on every beat, so an organ capturing on
    SCStream's thread gets its claims durable without ever touching a
    thread-bound sqlite connection."""
    import threading

    class ThreadedOrgan:
        name = "eye"

        def __init__(self, submit):
            self._submit = submit
            self._thread = None
            self._stop = threading.Event()

        def start(self):
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

        def _loop(self):
            i = 0
            while not self._stop.is_set() and i < 5:
                self._submit(make_claim(text="seen %d" % i, t_ms=1000 + i,
                                        frame="f%d" % i))
                i += 1
                self._stop.wait(0.02)

        def stop(self):
            self._stop.set()

        def alive(self):
            return self._thread is not None and self._thread.is_alive()

    store, lock = paths(tmp_path)
    report = run(store_path=store, seconds=0.5, lock_path=lock,
                 organs=[ThreadedOrgan], tick_seconds=0.05)

    texts = [c.text for c in ClaimLog(store).all_claims()]
    captured = [t for t in texts if t.startswith("seen ")]
    assert len(captured) == 5, texts
    assert report["claims_queue"]["dropped"] == 0
    assert ClaimLog(store).verify_chain()["ok"] is True


def test_the_queue_is_drained_before_the_clean_stop(tmp_path):
    """A claim appended AFTER the goodbye would read as having happened
    during a period the system said it was not watching."""
    class LateOrgan:
        name = "eye"

        def __init__(self, submit):
            self.submit = submit

        def start(self):
            self.submit(make_claim(text="last thing seen", t_ms=999))

        def stop(self):
            pass

        def alive(self):
            return True

    store, lock = paths(tmp_path)
    run(store_path=store, seconds=0.1, lock_path=lock, organs=[LateOrgan],
        tick_seconds=0.05)

    got = [(c.structured or {}).get("witness") or c.text
           for c in ClaimLog(store).all_claims()]
    assert got.index("last thing seen") < got.index(WitnessKind.CLEAN_STOP.value)


def test_an_organ_is_never_handed_the_store(tmp_path):
    """It gets `submit` and nothing else. An organ that could reach the
    store could append from its own thread, which is the whole defect."""
    seen = {}

    class Nosy:
        name = "eye"

        def __init__(self, submit):
            seen["arg"] = submit

        def start(self): pass
        def stop(self): pass
        def alive(self): return True

    store, lock = paths(tmp_path)
    run(store_path=store, seconds=0.1, lock_path=lock, organs=[Nosy],
        tick_seconds=0.05)

    assert callable(seen["arg"])
    assert not hasattr(seen["arg"], "append")


# -- the eye is opt-in ----------------------------------------------------
def test_the_eye_is_closed_unless_asked_for(tmp_path):
    """A daemon that began recording someone's screen the first time they
    typed its name — before they had decided to be recorded — would be
    exactly the product this one refuses to be."""
    store, lock = paths(tmp_path)
    # --plaintext because this test drives the REAL CLI, and the real CLI is
    # the one seam that reaches the founder's login keychain (see main()).
    # A suite that opens the actual Keychain as a side effect of asserting
    # something about consent would be undermining the property it tests.
    # The claims are read back below with NullCrypto, so the flag is also
    # what makes the assertion possible at all.
    assert main(["--store", store, "--lock", lock, "--plaintext",
                 "--seconds", "0.1", "--tick", "0.05"]) == 0
    # Only the daemon's own witnesses; nothing was captured.
    assert kinds(store) == [WitnessKind.START.value,
                            WitnessKind.CLEAN_STOP.value]


def test_the_eye_flag_registers_the_real_organ(tmp_path, monkeypatch):
    """--eye wires EyeOrgan through the submit factory. Monkeypatched so the
    test never touches a display or anyone's screen."""
    import tracemem.app.eye_organ as eye_mod

    built = {}

    class FakeEye:
        name = "eye"

        def __init__(self, submit):
            built["submit"] = submit

        def start(self): built["started"] = True
        def stop(self): pass
        def alive(self): return True

    monkeypatch.setattr(eye_mod, "EyeOrgan",
                        lambda submit, **kw: FakeEye(submit))

    store, lock = paths(tmp_path)
    assert main(["--store", store, "--lock", lock, "--eye", "--plaintext",
                 "--seconds", "0.1", "--tick", "0.05"]) == 0
    assert built.get("started") is True
    assert callable(built["submit"])


def test_the_ear_flag_registers_the_real_organ(tmp_path, monkeypatch):
    """--ear reaches the supervised ambient organ without requiring --eye."""
    import tracemem.app.ear_organ as ear_mod

    built = {}

    class FakeEar:
        name = "ear"

        def __init__(self, submit):
            built["submit"] = submit

        def start(self): built["started"] = True
        def stop(self): pass
        def alive(self): return True

    monkeypatch.setattr(ear_mod, "EarOrgan",
                        lambda submit, **kw: FakeEar(submit))

    store, lock = paths(tmp_path)
    assert main(["--store", store, "--lock", lock, "--ear", "--plaintext",
                 "--seconds", "0.1", "--tick", "0.05"]) == 0
    assert built.get("started") is True
    assert callable(built["submit"])


def test_the_owner_loop_actually_schedules_the_macro_pass(tmp_path):
    """A built macro sense with no loop caller was the original defect."""
    order = []

    class Brain:
        def absorb(self, _claim): pass
        def claim_owner(self): pass
        def backfill(self, _claims, progress=None):
            return {"bound": 0, "episodes": 0}
        def serve(self): order.append("answer")
        def flush(self, force=False): pass
        def stats(self): return {}

    class Spot:
        capture_active = True
        def set_capture(self, active): self.capture_active = active

    class Macro:
        def __init__(self): self.calls = 0
        def run(self):
            self.calls += 1
            assert spot.capture_active is False
            order.append("macro")
            return {"ran": 0, "claims": 0}
        def stats(self): return {"calls": self.calls}

    spot = Spot()
    macro = Macro()
    store, lock = paths(tmp_path)
    report = run(store_path=store, seconds=.12, lock_path=lock,
                 tick_seconds=.02, brain=Brain(), spotlight=spot,
                 macro_pass=macro)

    assert macro.calls > 0
    assert report["macro"]["calls"] == macro.calls
    assert order.index("answer") < order.index("macro"), (
        "background vision made a waiting founder wait")


def test_fast_restart_resumes_the_cumulative_bound_counter(tmp_path):
    """Delta boot must not make a lifetime memory look nearly empty."""
    class Graph:
        def set_meta(self, *_args): pass
        def get_meta(self, *_args): return "41"
        def binding_seen(self, ids): return set(ids)

    class Hub:
        def __init__(self):
            self.w = type("W", (), {"graph_store": Graph()})()
        def _advance_bind_watermark(self): return 41

    class Brain:
        def __init__(self):
            self.bound = 0
            self._hub = Hub()
        def absorb(self, _claim): pass
        def claim_owner(self): pass
        def backfill(self, _claims, progress=None):
            return {"bound": 0, "episodes": 0}
        def serve(self): pass
        def flush(self, force=False): return {"bound": 0}
        def stats(self):
            return {"bound": self.bound, "pending": 0,
                    "episodes": 0, "nodes": 0}

    store, lock = paths(tmp_path)
    report = run(store_path=store, seconds=.02, lock_path=lock,
                 tick_seconds=.01, brain=Brain())

    assert report["brain"]["bound"] == 41


def test_required_index_warms_behind_the_live_waking_gate(tmp_path):
    """FOUND IN PRODUCTION: the first complete vector build took minutes.

    It must happen before content is served, but after the socket opens.  A
    status request must remain immediate and name that work without touching
    the owner-thread brain; a content request must be refused rather than
    answered from a partial index.
    """
    import json
    import os
    import socket
    import threading
    import time

    entered = threading.Event()
    release = threading.Event()

    class Brain:
        bound = 0

        def absorb(self, _claim): pass
        def claim_owner(self): pass
        def backfill(self, _claims, progress=None):
            return {"bound": 0, "episodes": 0}
        def prepare_index(self):
            entered.set()
            assert release.wait(5), "test did not release index preparation"
        def ask(self, _question):
            raise AssertionError("content reached a partial search index")
        def serve(self): pass
        def flush(self, force=False): return {"bound": 0}
        def stats(self):
            return {"bound": self.bound, "pending": 0,
                    "episodes": 0, "nodes": 0}

    store, lock = paths(tmp_path)
    ask_path = "/tmp/trace-waking-%d-%x.sock" % (os.getpid(), id(entered))
    done = {}

    def _run():
        done["report"] = run(
            store_path=store, seconds=.05, lock_path=lock,
            tick_seconds=.01, brain=Brain(), ask_socket_path=ask_path)

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    assert entered.wait(3), "entrypoint never began mandatory index warmup"

    def _request(payload):
        started = time.monotonic()
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(1)
        client.connect(ask_path)
        client.sendall(json.dumps(payload).encode() + b"\n")
        response = json.loads(client.makefile("rb").readline())
        client.close()
        assert time.monotonic() - started < .5
        return response

    stats = _request({"op": "stats"})
    assert stats["ok"] is True and stats["waking"] is True
    assert stats["phase"] == "search index"
    refused = _request({"op": "ask", "question": "what did I do?"})
    assert refused["ok"] is False and refused["waking"] is True

    release.set()
    worker.join(3)
    assert not worker.is_alive()
    assert "report" in done


def test_status_exposes_detailed_health_for_every_physical_sense():
    class Organ:
        def __init__(self, name):
            self.name = name
        def health(self):
            return {"sense": self.name, "errors": 0}

    class State:
        def __init__(self, organ): self.organ = organ

    class Supervisor:
        _organs = {name: State(Organ(name)) for name in ("eye", "ear")}
        def health(self): return {"eye": "alive", "ear": "alive"}

    got = _organ_health(Supervisor())
    assert got["eye_detail"]["sense"] == "eye"
    assert got["ear_detail"]["sense"] == "ear"
