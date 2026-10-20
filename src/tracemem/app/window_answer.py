"""ANSWERING ABOUT A PERIOD WITHOUT WAKING THE BRAIN.

A question that names a time needs exactly two things: the claims in that
range, and a mouth. It does not need episodes, nodes, edges, activation, the
vector index, or the night — and every one of those is what the daemon spends
its boot binding.

That distinction is worth a module because of what it costs when it is
missed. On the founder's store the ask socket refused every question with
"still waking up, N of M bound" until backfill finished, and backfill over
470,322 claims is the longest thing this program does. So the most common
question a person has for a memory — "what was I doing yesterday" — was
gated behind the slowest thing the memory does, for no reason: the answer
lives in a time-indexed table (`ix_claim_time`, 29,250 claims read in 2.1s).

ITS OWN CONNECTION, AND WHY THAT IS NOT A SECOND WRITER. The daemon's law is
ONE writer: the supervision thread does every append, because the hash chain
computes `link_n` from the tail it can see and two racing writers produce two
claims claiming the same predecessor. This reader NEVER appends — it has no
code path that does — and SQLite in WAL mode is built for exactly this shape:
readers that do not block the writer and are not blocked by it. The law is
about writes; it was never about reads.
"""
from __future__ import annotations

from typing import Callable, Optional

from tracemem.llm.answer import Answer, parse_answer
from tracemem.llm.prompt import render_window_prompt
from tracemem.supplier.activity_summary import is_ranking_question
from tracemem.supplier.activity_summary import summarize as summarize_activity
from tracemem.supplier.cue import parse_cue
from tracemem.supplier.window import supply_window


class WindowAnswerer:
    """Answers time-scoped questions straight off the claim log."""

    def __init__(self, claim_log_factory, reasoner, clock,
                 budget_tokens: int = 6000,
                 spotlight=None, log: Optional[Callable[[str], None]] = None,
                 verify_answers: bool = True):
        # A FACTORY, NOT A CONNECTION. The ask socket serves each client on
        # its own thread and `sqlite3` connections are thread-bound
        # (check_same_thread), so one shared reader would raise on the second
        # caller. Opening a connection costs about a millisecond against a
        # range read measured at 2.1 seconds for a day, so per-question is
        # the right granularity and it is thread-safe by construction rather
        # than by discipline.
        self._open = claim_log_factory
        self._reasoner = reasoner
        self._clock = clock
        self._budget = budget_tokens
        self._spotlight = spotlight
        self._log = log or (lambda m: None)
        self.served = 0
        # THE GUARD IS ON BY DEFAULT, like everywhere else. A defence you
        # have to remember to enable is off when it matters — and this path
        # had none at all until 2026-08-26.
        self._verify = verify_answers
        self.last_refusal = ""
        self.last_citation_grades = {}

    def window_of(self, question: str):
        """The period this question is about, or None if it names none."""
        return parse_cue(question, clock=self._clock).window

    def answer(self, question: str) -> Optional[Answer]:
        """An Answer, or None if this is not a question about a period.

        None rather than an abstention on purpose: "this is not my question"
        and "I have nothing for your question" are different, and only the
        second one is an answer the founder should ever see.
        """
        rng = self.window_of(question)
        if rng is None:
            return None
        import time as _t
        t_start = _t.monotonic()
        side: dict = {}
        context = supply_window(self._open(), rng, self._budget,
                                moments_out=side)
        t_supplied = _t.monotonic()
        self._log("period %r: %d moment(s) supplied in %.1fs [%s]"
                  % (rng.phrase, len(side.get("moments") or ()),
                     t_supplied - t_start, side.get("timing", "")))
        if not context.blocks:
            return Answer(text="I was not watching %s." % rng.phrase,
                          citations=(), abstained=True)
        # WHAT THE FOUNDER SAID HIMSELF. A correction is the highest-graded
        # statement in the store and this path could not see one, so "what
        # did I do yesterday" answered from the screen's testimony alone even
        # after being told it was wrong.
        context = self._with_corrections(context, question)

        # A RANKING HAS A COUNTED ANSWER. "Which apps did I use most today"
        # is a sum of durations; asking a model to write it invited a
        # sentence the guard then had to refuse (2026-10-09).
        if side.get("moments") and is_ranking_question(question):
            self.served += 1
            return self._counted(side, rng)

        prompt = render_window_prompt(context, question)
        t_model = _t.monotonic()
        raw = self._complete(prompt)
        self._log("period answer: model wrote it in %.1fs"
                  % (_t.monotonic() - t_model))
        self.served += 1
        answer = parse_answer(raw, context)
        self.last_citation_grades = {
            sid: int(b.grade) for b in context.blocks
            for sid in (b.source_ids or ())}
        # THE NEVER-LIE GUARD, WHICH HAD NEVER RUN ON THIS PATH.
        #
        # MEASURED 2026-08-26 against a snapshot of the real store. This
        # module contained no reference to the guard at all — no bluff_check,
        # no verify, nothing. So the single most-asked question in the
        # product, "what did I do yesterday?", was answered by a local model
        # over packed evidence with NOTHING checking that what it said was in
        # that evidence.
        #
        # Every other path has been guarded since July. This one is the
        # default, it is the one a person uses first, and it had the fewest
        # protections of any of them — which is the worst possible place for
        # the gap to be, and the hardest to notice precisely because it
        # answers so well most of the time.
        t_guard = _t.monotonic()
        answer = self._guard(answer, question, context, side, rng)
        self._log("period answer: checked in %.1fs (total %.1fs)"
                  % (_t.monotonic() - t_guard, _t.monotonic() - t_start))

        if answer.invented:
            # Not fatal — the rest of the answer is cited — but it is the one
            # thing a reader cannot check for themselves, so it is said.
            self._log("answer cited %d source(s) that do not exist: %s"
                      % (len(answer.invented), ", ".join(answer.invented)))
        return answer

    def _with_corrections(self, context, question: str):
        """Prepend the founder's own words when they bear on the question."""
        from tracemem.supplier.corrections import find_corrections
        from tracemem.supplier.corrections import render as render_corrections
        from tracemem.supplier.packer import Context, CtxBlock

        try:
            said = find_corrections(self._open(), question)
            text = render_corrections(said)
        except Exception:                                    # noqa: BLE001
            return context
        if not text:
            return context
        block = CtxBlock(kind="claim", text=text,
                         grade=max(c.grade for c in said),
                         source_ids=tuple(c.id for c in said),
                         t_ms=max(c.coord.t_ms for c in said))
        return Context((block,) + tuple(context.blocks), context.tokens)

    def _counted(self, side, rng):
        from tracemem.llm.answer import Answer
        from tracemem.supplier import timeline
        text, cites = summarize_activity(
            side["moments"], timeline.cite_id, caveat=side.get("caveat"),
            phrase=rng.phrase)
        return Answer(text=text, citations=cites, abstained=False)

    def _guard(self, answer, question: str, context, side=None, rng=None):
        """Verify, or refuse to prose — the same contract hub.ask keeps."""
        from tracemem.llm.answer import Answer
        from tracemem.llm.bluff_check import guard as bluff_guard

        if not self._verify or answer.abstained:
            return answer

        if not answer.citations:
            # AN UNCITED ANSWER IS NOT AN ANSWER. Nothing about it can be
            # checked, so it is never served; the evidence is shown instead,
            # which cannot fabricate because it IS the context.
            self.last_refusal = ("the answer cited nothing, so nothing about "
                                 "it could be checked")
            return self._evidence_only(context, side, rng)

        blocks_by_id = {sid: b.text for b in context.blocks
                        for sid in (b.source_ids or ())}
        try:
            checked = bluff_guard(answer, question, blocks_by_id,
                                  self._reasoner, mode="synthesis",
                                  note=self._note_refusal)
        except Exception:                                    # noqa: BLE001
            # A GUARD THAT STUMBLES MUST NOT SERVE THE UNVERIFIED ANSWER.
            self.last_refusal = "the check could not be completed"
            return self._evidence_only(context, side, rng)
        if checked.abstained:
            # The guard refused the PROSE, not the evidence. For a summary,
            # "not in memory" would be a lie of a different kind.
            return self._evidence_only(context, side, rng)
        return checked

    def _note_refusal(self, why: str) -> None:
        self.last_refusal = str(why)[:400]

    def _evidence_only(self, context, side=None, rng=None):
        from tracemem.llm.answer import Answer

        # THE REASON GOES TO THE LOG. It was kept in `last_refusal` and read
        # by nobody, so every "here is the evidence" answer the founder saw
        # (2026-10-09: two in a row) was impossible to diagnose afterwards.
        self._log("period answer refused, evidence shown instead: %s"
                  % (self.last_refusal or "unknown"))
        if side and side.get("moments") and rng is not None:
            # Counted first: what was in front, for how long. It is built
            # only from read durations and titles, so it cannot say anything
            # the evidence does not. Then a SHORT sample of what was read,
            # because the count alone drops the content a question may be
            # about (a helper's "Code symbols: MemoryCell", a page's words).
            counted = self._counted(side, rng)
            read = []
            for block in context.blocks:
                for line in str(block.text).splitlines():
                    line = line.strip()
                    if line.startswith("seen:") and line not in read:
                        read.append(line)
            if read:
                body = "\n".join("  " + l for l in read[:8])
                if len(read) > 8:
                    body += "\n  … and %d more line(s)." % (len(read) - 8)
                counted = Answer(
                    text=counted.text + "\n\nRead on screen:\n" + body,
                    citations=counted.citations, abstained=False)
            return counted

        lines = []
        for block in context.blocks:
            for line in str(block.text).splitlines():
                if line.strip():
                    lines.append(line)
        shown = lines[:18]
        body = "\n".join(shown)
        if len(lines) > len(shown):
            body += "\n… and %d more line(s)." % (len(lines) - len(shown))
        cites = tuple(dict.fromkeys(
            sid for b in context.blocks for sid in (b.source_ids or ())))
        return Answer(
            text=("I could not summarise this without risking a claim the "
                  "evidence does not support, so here is the evidence:\n\n"
                  + body),
            citations=cites, abstained=False)

    def _complete(self, prompt: str) -> str:
        """One model call, under the spotlight when there is one.

        The one-model-on-Metal law binds this exactly as it binds the graph
        path: a question is allowed to interrupt the night, and never allowed
        to run beside it.
        """
        if self._spotlight is None:
            return self._reasoner.complete(prompt)
        from tracemem.app.scheduler import QUERY_PRIORITY
        self._spotlight.signal_query()
        with self._spotlight.acquire("local-ollama", role="ANSWER",
                                     priority=QUERY_PRIORITY):
            return self._reasoner.complete(prompt)
