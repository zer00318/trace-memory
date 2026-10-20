"""An answer that arrives as it is written.

A local 12B model takes 10-30 seconds. Waiting in silence for half a minute
is the difference between a memory that feels alive and one that feels
broken, and no amount of speed work closes it — first-token latency is what a
person experiences, not total time.

`complete` is untouched. Everything that VERIFIES an answer — the never-lie
guard, the grounding check — wants the whole thing at once and gains nothing
from chunks.
"""
from __future__ import annotations

import socket

import pytest

from tracemem.llm.reasoner_ollama import (OllamaReasoner, ReasonerUnavailable,
                                          _default_stream_transport)


class Lease:
    def __init__(self):
        self.held = 0
        self.max_held = 0
        self.acquires = 0

    def acquire(self, model, role="?", priority=0):
        lease = self

        class _Ctx:
            def __enter__(self_):
                lease.acquires += 1
                lease.held += 1
                lease.max_held = max(lease.max_held, lease.held)
                return None

            def __exit__(self_, *exc):
                lease.held -= 1
                return False
        return _Ctx()


def _reasoner(chunks=("Hello", " ", "world"), lease=None, boom=None):
    def transport(url, payload, timeout_s):
        assert payload["stream"] is True, "it asked for a whole answer"
        for c in chunks:
            if boom is not None and c == boom:
                raise ConnectionResetError("the server went away")
            yield c
    return OllamaReasoner("gemma3:12b-it-qat", lease=lease, stream_transport=transport)


def test_chunks_arrive_in_order():
    assert list(_reasoner().stream("q")) == ["Hello", " ", "world"]


def test_the_lease_is_held_for_the_whole_stream():
    """Releasing between chunks would let the night take the GPU mid-sentence,
    and the answer would arrive in two halves written by a model that was
    swapped out and back in between them."""
    lease = Lease()
    r = _reasoner(lease=lease)
    seen = []
    for chunk in r.stream("q"):
        seen.append(chunk)
        assert lease.held == 1, "the lease was dropped mid-answer"
    assert lease.acquires == 1
    assert lease.held == 0, "the lease was not released at the end"


def test_a_dead_server_mid_stream_is_reported_not_retried():
    """`complete` retries a refused connection because nobody has seen
    anything yet. Once a token is on screen, retrying replays the answer from
    the start — the words already read would be repeated or contradicted,
    which is worse than an answer that stops and says so."""
    r = _reasoner(chunks=("Hel", "lo", " world"), boom=" world")
    got = []
    with pytest.raises(ReasonerUnavailable):
        for chunk in r.stream("q"):
            got.append(chunk)
    assert got == ["Hel", "lo"], "it threw away what had already arrived"


def test_a_timeout_is_never_swallowed():
    """The swap-deadlock class. `complete` re-raises these rather than
    retrying, and streaming must not be the softer door."""
    def transport(url, payload, timeout_s):
        raise socket.timeout("gone")
        yield  # pragma: no cover

    r = OllamaReasoner("gemma3:12b-it-qat", stream_transport=transport)
    with pytest.raises(socket.timeout):
        list(r.stream("q"))


def test_complete_is_untouched_and_still_asks_for_the_whole_answer():
    seen = {}

    def transport(url, payload, timeout_s):
        seen.update(payload)
        return "whole"

    r = OllamaReasoner("gemma3:12b-it-qat", transport=transport)
    assert r.complete("q") == "whole"
    assert seen["stream"] is False


# -- the wire format ------------------------------------------------------

class _Resp:
    def __init__(self, lines):
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._lines)


def test_the_transport_reads_one_json_object_per_line(monkeypatch):
    import urllib.request

    lines = [b'{"response":"a"}\n', b'{"response":"b"}\n',
             b'{"response":"","done":true}\n']
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: _Resp(lines))
    assert list(_default_stream_transport("http://127.0.0.1:11434/api/generate", {}, 1.0)) == ["a", "b"]


def test_a_truncated_line_costs_one_token_not_the_answer(monkeypatch):
    """A local server under memory pressure can emit a broken line. Raising
    would throw away every token already delivered — for a streaming answer
    that means the person watching it appear sees it vanish."""
    import urllib.request

    lines = [b'{"response":"a"}\n', b'{"resp\n', b'{"response":"c"}\n',
             b'{"done":true}\n']
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: _Resp(lines))
    assert list(_default_stream_transport("http://127.0.0.1:11434/api/generate", {}, 1.0)) == ["a", "c"]


def test_done_ends_the_stream(monkeypatch):
    import urllib.request

    lines = [b'{"response":"a"}\n', b'{"done":true}\n',
             b'{"response":"never"}\n']
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: _Resp(lines))
    assert list(_default_stream_transport("http://127.0.0.1:11434/api/generate", {}, 1.0)) == ["a"]
