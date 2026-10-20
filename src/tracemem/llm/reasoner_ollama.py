"""OWNS: the real local reasoner adapter (spec 08 §1-2 + env gotchas).

Retry discipline learned the hard way on this machine (witnessed 2026-07-12,
legacy commit 67cfb77): Ollama.app respawn windows refuse connections for
minutes — refused/reset gets exponential backoff and retry. TIMEOUTS ARE THE
SWAP-DEADLOCK CLASS: a timed-out call means the model is wedged, retrying
cannot cure it and burns the night — timeouts propagate immediately.

Transport is injectable so the retry logic is unit-testable without a server;
the lease turn-guard keeps the one-model law at the adapter seam.
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request

from tracemem.llm.egress import require_loopback
from typing import Callable, Optional

from tracemem.llm import registry

RETRY_MAX = 4
BACKOFF_S = (1.0, 3.0, 9.0, 27.0)


class ReasonerUnavailable(Exception):
    pass


class ReasonerCancelled(ReasonerUnavailable):
    """A person started waiting mid-generation; the call was abandoned."""


class Interruptible:
    """A reasoner whose `complete` gives way to a waiting person.

    THE NIGHT HELD THE ANSWER THREAD FOR UP TO 107s (2026-10-15). A causal
    or mining judgment is one 30-100s generation on the daemon's single owner
    thread; the night polls for a waiting question only BETWEEN such calls,
    so a question asked during one waited for all of it. This streams the
    same request and checks `interrupted()` between chunks; on a waiting
    person it closes the stream, which closes the HTTP connection, which
    makes ollama stop generating, and raises ReasonerCancelled. The judgment
    is retried by a later slice; nothing half-generated is ever used.
    """

    def __init__(self, reasoner, interrupted) -> None:
        self._reasoner = reasoner
        self._interrupted = interrupted

    def complete(self, prompt: str) -> str:
        if self._interrupted():
            raise ReasonerCancelled("a question is waiting")
        chunks = self._reasoner.stream(prompt)
        parts = []
        try:
            for chunk in chunks:
                if self._interrupted():
                    raise ReasonerCancelled("a question is waiting")
                parts.append(chunk)
        finally:
            close = getattr(chunks, "close", None)
            if close is not None:
                close()
        return "".join(parts)

    def __getattr__(self, name):
        return getattr(self._reasoner, name)



def _default_stream_transport(url: str, payload: dict, timeout_s: float):
    """Yield chunks as ollama emits them. One JSON object per line.

    A MALFORMED LINE IS SKIPPED, NOT FATAL. A local server under memory
    pressure can emit a truncated line; losing one token is a blemish, and
    raising would throw away every token already delivered — which for a
    streaming answer means the person watching it appear sees it vanish.
    """
    req = urllib.request.Request(
        require_loopback(url), data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            chunk = obj.get("response")
            if chunk:
                yield chunk
            if obj.get("done"):
                return


def _default_transport(url: str, payload: dict, timeout_s: float) -> str:
    req = urllib.request.Request(
        require_loopback(url), data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode("utf-8"))["response"]


class OllamaReasoner:
    def __init__(self, model: str, lease=None,
                 url: str = "http://127.0.0.1:11434/api/generate",
                 timeout_s: float = 120.0,
                 transport: Optional[Callable] = None,
                 stream_transport: Optional[Callable] = None,
                 sleep: Callable = time.sleep,
                 on_refused: Optional[Callable[[], None]] = None) -> None:
        # THE REGISTRY GATE (map: LLM / Model registry). Refusing here, in
        # the constructor, rather than at call time: a wedged model tag
        # should fail when the adapter is built, not three seconds into a
        # night run. An unregistered model is a loud error and never a
        # default — an answer from an unrecorded model cannot be
        # reproduced, re-scored, or defended.
        self.entry = registry.get(model)
        self.model = model
        self.lease = lease
        self.url = url
        self.timeout_s = timeout_s
        self._transport = transport or _default_transport
        self._stream_transport = stream_transport or _default_stream_transport
        self._sleep = sleep
        # Told when the server refuses a connection, so a dead server is
        # restarted during this call's backoff instead of being retried four
        # times and given up on (see llm/ollama_keeper).
        self._on_refused = on_refused

    def complete(self, prompt: str) -> str:
        if self.lease is not None:
            with self.lease.acquire(self.model, role="ANSWER", priority=2):
                return self._call(prompt)
        return self._call(prompt)

    def stream(self, prompt: str):
        """The same answer, arriving as it is written.

        WHY THIS EXISTS AND WHY IT IS SEPARATE FROM `complete`. A local 12B
        model takes 10-30 seconds to answer. Waiting in silence for half a
        minute is the difference between a memory that feels alive and one
        that feels broken, and no amount of speed work closes that gap —
        first-token latency is what a person experiences, not total time.
        `complete` stays exactly as it was, because everything that VERIFIES
        an answer (the guard, the grounding check) wants the whole thing at
        once and would gain nothing from chunks.
        
        THE LEASE IS HELD FOR THE WHOLE STREAM. Releasing it between chunks
        would let the night take the GPU mid-sentence, and the answer would
        arrive in two halves written by a model that had been swapped out and
        back in between them.

        NO RETRIES, deliberately. `complete` retries a refused connection
        because nobody has seen anything yet. Once a token has been shown to
        a person, retrying would replay the answer from the start — the words
        already on screen would be repeated or contradicted, which is worse
        than an answer that stops and says it stopped.
        """
        if self.lease is not None:
            with self.lease.acquire(self.model, role="ANSWER", priority=2):
                for chunk in self._stream(prompt):
                    yield chunk
            return
        for chunk in self._stream(prompt):
            yield chunk

    def _stream(self, prompt: str):
        payload = {"model": self.model, "prompt": prompt, "stream": True,
                   "options": registry.sampling_for(self.model)}
        try:
            for chunk in self._stream_transport(self.url, payload,
                                                self.timeout_s):
                yield chunk
        except (socket.timeout, TimeoutError):
            raise
        except (urllib.error.URLError, ConnectionRefusedError,
                ConnectionResetError) as e:
            raise ReasonerUnavailable("ollama unreachable while streaming: %r"
                                      % (e,))

    def _call(self, prompt: str) -> str:
        # THE SAMPLING LAW (map: LLM / Sampling law): near-greedy params ride
        # on EVERY request, sourced from the registry rather than written
        # here. Temperature buys variety at the cost of fidelity, which is
        # the wrong trade for a mouth whose job is to say what the evidence
        # says — and it makes a wrong answer unreproducible, so a lie caught
        # on Tuesday cannot be re-caught on Wednesday to check the fix.
        payload = {"model": self.model, "prompt": prompt, "stream": False,
                   "options": registry.sampling_for(self.model)}
        last = None
        for attempt in range(RETRY_MAX):
            try:
                return self._transport(self.url, payload, self.timeout_s)
            except (socket.timeout, TimeoutError):
                raise                                   # swap-deadlock class: never retry
            except urllib.error.URLError as e:
                if isinstance(getattr(e, "reason", None), (socket.timeout, TimeoutError)):
                    raise                               # timeout dressed as URLError
                last = e                                # refused/reset: respawn window
            except (ConnectionRefusedError, ConnectionResetError) as e:
                last = e
            if self._on_refused is not None and attempt == 0:
                try:
                    self._on_refused()
                except Exception:                           # noqa: BLE001
                    pass
            self._sleep(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])
        raise ReasonerUnavailable("ollama unreachable after %d attempts: %r"
                                  % (RETRY_MAX, last))
