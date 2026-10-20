"""ORACLE for the capture→store hand-off (WO-093).

The defect: SCStream calls back on its own thread, sqlite3 connections are
thread-bound, and a live capture run died with `ProgrammingError: SQLite
objects created in a thread can only be used in that same thread` — 3
errors, 0 claims durable.

These tests use a REAL ClaimLog driven from a REAL second thread, because
the bug only exists across threads and a mock sink would prove nothing about
it. `test_the_real_defect_is_actually_reproducible` fires the original
failure on purpose first, so the fix is measured against a bug that has been
seen rather than one that was described.
"""
import sqlite3
import threading

import pytest

from tracemem.app.claim_queue import ClaimQueue, guarded
from tracemem.app.witness import WitnessKind
from tracemem.text.claim_log import ClaimLog
from tests.helpers import make_claim


def log_at(tmp_path):
    return ClaimLog(str(tmp_path / "store.sqlite3"))


# -- the bug itself --------------------------------------------------------
def test_the_real_defect_is_actually_reproducible(tmp_path):
    """Appending straight from another thread still fails. If this ever
    stops failing, the queue has become unnecessary and should be deleted
    rather than kept as folklore."""
    log = log_at(tmp_path)
    boom = {}

    def other_thread():
        try:
            log.append(make_claim(text="from the capture thread", t_ms=1))
        except BaseException as exc:            # noqa: BLE001
            boom["err"] = exc

    t = threading.Thread(target=other_thread)
    t.start(); t.join()

    assert isinstance(boom.get("err"), sqlite3.ProgrammingError)
    assert "thread" in str(boom["err"]).lower()


def test_the_queue_carries_a_claim_across_the_thread_boundary(tmp_path):
    """THE FIX. Submit from a second thread, drain from this one, and the
    claim is durable."""
    log = log_at(tmp_path)
    q = ClaimQueue(sink=log.append)

    def capture_thread():
        q.submit(make_claim(text="seen on screen", t_ms=1000))

    t = threading.Thread(target=capture_thread)
    t.start(); t.join()

    assert q.pending() == 1
    assert log.count() == 0                      # nothing written yet
    assert q.drain() == 1
    assert log.count() == 1
    assert log.all_claims()[0].text == "seen on screen"


def test_many_capture_threads_one_writer(tmp_path):
    """The property that makes the hash chain and the durability policy
    true: however many threads submit, exactly one appends."""
    log = log_at(tmp_path)
    q = ClaimQueue(sink=log.append)

    def worker(n):
        for i in range(20):
            q.submit(make_claim(text="t%d-%d" % (n, i), t_ms=n * 100 + i,
                                frame="f%d-%d" % (n, i)))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert q.drain() == 80
    assert log.count() == 80
    assert log.verify_chain()["ok"] is True      # one writer, intact chain


def test_a_second_drainer_is_refused(tmp_path):
    """A second drain thread would reintroduce the race the queue removes."""
    log = log_at(tmp_path)
    q = ClaimQueue(sink=log.append)
    q.drain()                                    # this thread claims ownership

    err = {}

    def other():
        try:
            q.drain()
        except BaseException as exc:             # noqa: BLE001
            err["e"] = exc

    t = threading.Thread(target=other)
    t.start(); t.join()

    assert isinstance(err.get("e"), RuntimeError)
    assert "single-writer" in str(err["e"])


# -- submit never blocks the capture callback -----------------------------
def test_submit_never_blocks_when_full():
    """A blocked capture callback drops FRAMES, which is a worse loss than
    the claim being saved."""
    q = ClaimQueue(sink=lambda c: None, maxsize=3)
    for i in range(3):
        assert q.submit(make_claim(text="x%d" % i, t_ms=i, frame="f%d" % i)) is True
    assert q.submit(make_claim(text="overflow", t_ms=99, frame="f99")) is False
    assert q.dropped == 1


def test_overflow_is_witnessed_not_silent(tmp_path):
    """A bounded queue that quietly drops is the worst of both worlds: the
    eye believes it saw something, and the memory has neither the claim nor
    a hole."""
    log = log_at(tmp_path)
    q = ClaimQueue(sink=log.append, maxsize=2, now_ms=lambda: 4242)
    for i in range(5):
        q.submit(make_claim(text="x%d" % i, t_ms=i, frame="f%d" % i))

    q.drain()
    kinds = [(c.structured or {}).get("witness") for c in log.all_claims()]
    assert WitnessKind.OVERWHELM.value in kinds

    shed = [c for c in log.all_claims()
            if (c.structured or {}).get("witness") == WitnessKind.OVERWHELM.value][0]
    assert shed.structured["dropped"] == 3
    assert shed.coord.t_ms == 4242


def test_a_clean_run_writes_no_overwhelm_witness(tmp_path):
    log = log_at(tmp_path)
    q = ClaimQueue(sink=log.append)
    q.submit(make_claim(text="fine", t_ms=1))
    q.drain()
    kinds = [(c.structured or {}).get("witness") for c in log.all_claims()]
    assert WitnessKind.OVERWHELM.value not in kinds


def test_the_shed_count_resets_so_it_is_not_re_reported(tmp_path):
    """Each overwhelm witness reports the claims shed since the last one.
    Re-reporting a cumulative total would make one bad second look like a
    bad afternoon in the gap list."""
    log = log_at(tmp_path)
    q = ClaimQueue(sink=log.append, maxsize=1)
    for i in range(3):
        q.submit(make_claim(text="a%d" % i, t_ms=i, frame="fa%d" % i))
    q.drain()
    q.submit(make_claim(text="b", t_ms=50, frame="fb"))
    q.drain()

    sheds = [c.structured["dropped"] for c in log.all_claims()
             if (c.structured or {}).get("witness") == WitnessKind.OVERWHELM.value]
    assert sheds == [2]


def test_drain_can_be_limited(tmp_path):
    log = log_at(tmp_path)
    q = ClaimQueue(sink=log.append)
    for i in range(10):
        q.submit(make_claim(text="x%d" % i, t_ms=i, frame="f%d" % i))
    assert q.drain(limit=4) == 4
    assert q.pending() == 6


def test_stats_report_the_whole_story(tmp_path):
    log = log_at(tmp_path)
    q = ClaimQueue(sink=log.append, maxsize=2)
    for i in range(4):
        q.submit(make_claim(text="x%d" % i, t_ms=i, frame="f%d" % i))
    q.drain()
    got = q.stats()
    assert got["submitted"] == 2 and got["dropped"] == 2
    assert got["drained"] == 2 and got["pending"] == 0


# -- the silent-exception guard -------------------------------------------
def test_guarded_records_what_the_objc_delegate_would_swallow():
    """A capture thread that starts raising looks exactly like a quiet
    afternoon: no error, no claims, no hole."""
    seen = []

    def explodes(_frame):
        raise ValueError("OCR blew up")

    wrapped = guarded(explodes, on_error=seen.append)
    assert wrapped("frame") is None              # does not propagate
    assert isinstance(seen[0], ValueError)


def test_guarded_passes_results_through_when_nothing_is_wrong():
    wrapped = guarded(lambda x: x * 2, on_error=lambda e: None)
    assert wrapped(21) == 42


def test_guarded_catches_even_baseexception():
    """The delegate swallows everything, so the guard must see everything —
    a KeyboardInterrupt inside a capture callback would otherwise vanish."""
    seen = []
    wrapped = guarded(lambda: (_ for _ in ()).throw(KeyboardInterrupt()),
                      on_error=seen.append)
    wrapped()
    assert isinstance(seen[0], KeyboardInterrupt)
