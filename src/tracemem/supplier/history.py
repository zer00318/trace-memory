"""OWNS: what you have asked before, findable.

`Ask/History` says every ask is stored as a claim about what you wondered.
Storing them turned out to be the easy half. Measured 2026-08-25, a stored
ask was unreachable by any question:

    FINDABLE: False
    CONTEXT HEAD: MEMORY BLOCKS: (node|VERBATIM|…) [APP …] app=Calendar …

Same root cause as founder corrections, and that makes it a CLASS rather
than two bugs: a claim the founder said or asked has no app, no place and no
frame, so it births no node — and ordinary retrieval walks nodes. Everything
the human contributed is invisible to the machine's own search.

WHY NOT FULL-TEXT SEARCH, WHICH IS THE OBVIOUS ANSWER. The store is
encrypted at rest. An FTS index over claim text is a plaintext copy of
everything on disk, which trades the product's central promise for a
convenience — and that is a founder's decision to make, not an agent's. It
is why `Exact search hook` and `Index maintenance` both sit at 0%.

So this reads claims directly instead. It costs a scan, and a scan is
honest: the daemon already holds decrypted claims in memory to serve any
question at all, so nothing here is exposed that was not already exposed.

NOT INJECTED INTO EVERY ANSWER, DELIBERATELY. A correction is authority and
belongs in the context. A past question is HISTORY: pulling "You asked: who
is Juno?" into a fresh question about Juno spends budget the real
evidence needs and invites a model to treat the asking as a fact about her.
History is served on request — a surface of its own — not smuggled into
retrieval.
"""
from __future__ import annotations

from typing import List, Optional

from tracemem.app.founder import PERCEIVER
from tracemem.domain import ProvenanceClass

MAX_HISTORY = 50

_STOP = frozenset(
    "the a an and or but of to in on at for with from was were is are be been "
    "it its this that these those i me my you your he she they them what when "
    "where who how why did do does done had have has not no yes about show "
    "tell me all my everything".split())


def _tokens(s: str):
    return {t for t in "".join(
        ch.lower() if ch.isalnum() else " " for ch in str(s)).split()
        if len(t) > 2 and t not in _STOP}


def is_ask(claim) -> bool:
    prov = getattr(claim, "provenance", None)
    if prov is None or getattr(prov, "perceiver", "") != PERCEIVER:
        return False
    if getattr(prov, "klass", None) is not ProvenanceClass.OWNED:
        return False
    return bool((getattr(claim, "structured", None) or {}).get("asked"))


def find_asks(claim_log, query: str = "", limit: int = MAX_HISTORY) -> List:
    """Past questions, oldest first. An empty query returns the most recent.

    Filtering is word overlap against the QUESTION that was asked, not
    against the answer — someone looking for "what did I ask about the kiln"
    is remembering their own words, not the machine's.
    """
    if claim_log is None:
        return []
    want = _tokens(query)
    hits = []
    try:
        # Only founder claims can qualify, and `bidder` is an indexed
        # plaintext column: decrypt those, never the whole store (every
        # ask timed out at 120s on the founder's 635k claims, 2026-10-07).
        source = (claim_log.by_bidder(PERCEIVER)
                  if hasattr(claim_log, "by_bidder")
                  else claim_log.all_claims())
        for c in source:
            if not is_ask(c):
                continue
            if want and not (_tokens((c.structured or {}).get("asked", ""))
                             & want):
                continue
            hits.append(c)
    except Exception:                                        # noqa: BLE001
        return []
    hits.sort(key=lambda c: getattr(getattr(c, "coord", None), "t_ms", 0))
    return hits[-limit:]


def render(claims, clock_fmt=None) -> str:
    """The history, as a person would want to read it back."""
    if not claims:
        return "You have not asked TRACE anything yet."
    import time

    out = ["WHAT YOU HAVE ASKED", "=" * 40]
    for c in claims:
        s = c.structured or {}
        when = (clock_fmt(c.coord.t_ms) if clock_fmt
                else time.strftime("%Y-%m-%d %H:%M",
                                   time.localtime(c.coord.t_ms / 1000.0)))
        out.append("")
        out.append("%s  %s" % (when, s.get("asked", "")))
        # THE ANSWER IS SHOWN, AND IT IS STILL NOT EVIDENCE. Reading it back
        # here is history; it is never packed into a context as something
        # the machine saw. See app/founder.ask_claim.
        answered = str(s.get("answered") or "")
        if s.get("abstained"):
            out.append("    → it had nothing, and said so")
        elif answered:
            out.append("    → %s" % (answered[:160]
                                     + ("…" if len(answered) > 160 else "")))
    return "\n".join(out)
