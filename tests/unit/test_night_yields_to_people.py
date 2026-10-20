"""A person waiting beats a generation in progress (2026-10-15).

Night slices held the owner thread for up to 107s: one causal or mining
generation runs 30-100s, and the night only polled for a waiting question
between generations. And the flag it polled for was raised on the owner
thread itself, after the night had already handed the thread back.
"""
from __future__ import annotations

import threading

import pytest

from tracemem.llm.reasoner_ollama import Interruptible, ReasonerCancelled


class Streamer:
    def __init__(self):
        self.closed = False

    def stream(self, prompt):
        try:
            for chunk in ("a", "b", "c", "d"):
                yield chunk
        finally:
            self.closed = True


def test_a_waiting_person_cancels_the_generation_and_closes_the_stream():
    inner = Streamer()
    seen = []

    def interrupted():
        seen.append(1)
        return len(seen) > 2

    with pytest.raises(ReasonerCancelled):
        Interruptible(inner, interrupted).complete("p")
    assert inner.closed, "the HTTP stream must close so ollama stops generating"


def test_without_anyone_waiting_the_answer_is_whole():
    inner = Streamer()
    assert Interruptible(inner, lambda: False).complete("p") == "abcd"
    assert inner.closed


def test_a_cancelled_night_is_a_resumable_slice_not_a_failure(monkeypatch):
    import tracemem.nodes.marination.nightly as nightly

    def body(report, *a, **k):
        raise ReasonerCancelled("a question is waiting")

    monkeypatch.setattr(nightly, "_run_night", body)

    class Lease:
        def interrupted(self):
            return True

    report = nightly.run_night(None, None, None, Lease(), None,
                               reasoner=Streamer())
    assert report.status == "RESUMABLE"


def test_asking_raises_the_waiting_flag_before_queueing_behind_the_night():
    from tracemem.app.brain import Brain

    order = []
    spot = type("S", (), {"signal_query": lambda self: order.append("signal")})()
    hub = type("H", (), {})()
    hub.w = type("W", (), {"spotlight": spot})()
    brain = Brain(hub, lambda c: None, lambda: 0)
    brain._owner = threading.get_ident() + 1          # someone else owns it
    brain._on_owner = lambda fn, timeout_s=120.0: order.append("queued")
    brain.ask("what was I doing?")
    assert order == ["signal", "queued"]
    order.clear()
    brain.stats = Brain.stats.__get__(brain)
    try:
        brain.stats()
    except Exception:                                   # noqa: BLE001
        pass
    assert "signal" not in order, "a machine poll must not starve the night"
