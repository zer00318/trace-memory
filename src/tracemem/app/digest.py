"""OWNS: the digest — what the brain did, and what it now thinks (P3).

THE FOUNDER ASKED FOR THIS BY NAME, when asked how the memory tier could ever
be measured: *"show what the brain did and what it thinks cumulatively, like a
summary. Since I'm the only user, I can grade it."*

It is three things at once, which is why it outranks everything else that is
not the night itself:

  1. **The only way to observe the night.** `run_night()` had zero production
     callers until 2026-07-29; nobody has ever read what this engine believes.
  2. **The memory-tier metric.** Gap-to-ceiling is measured on a SINGLE FRAME
     (`eval_runs/context_gap.json` says so in its own header), so it
     structurally cannot score accumulation across days — which is the whole
     thesis. A graded digest can.
  3. **The product's face**, until the local web app exists (blocked on F10:
     `network.server=false` vs a page that must be served).

FOUR SECTIONS, and the order is the argument: what I SAW (perception, with its
honest holes) → what I BELIEVE (derivation) → what CHANGED MY MIND (the moat:
beliefs that died) → what I'm UNSURE of (the questions). A reader who stops
after section one still gets a true report.

EVERY LINE CITES. A digest line with no receipt is exactly the confabulation
this product exists to refuse, so `DigestLine.citations` is never empty for a
belief and the renderer shows them.

WHAT THIS MODULE DOES NOT DO: it does not decide anything, it does not call a
model, and it never writes. It reads the graph and the log and renders. The
one thing that writes is `grade()` in app/founder.py, and that writes a CLAIM
— not a tuning knob (F-GRADE).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from tracemem.app.coverage import compute as compute_coverage
from tracemem.nodes.accrual import label_of

MS_DAY = 86_400_000

# A witness may START before the window (an overnight gap is stamped the
# previous evening), so coverage reads back this far to find intervals that
# reach INTO the day. Same reasoning and same value as binder.GAP_LOOKBACK_MS.
GAP_LOOKBACK_MS = 36 * 60 * 60 * 1000

SECTIONS = ("saw", "believe", "changed", "unsure")

# A STATEMENT CAN BE GRADEABLE WITHOUT BEING PART OF THE NIGHTLY DIGEST.
#
# `Ask/'That's wrong'` is a recorded law and had no path to reach it: the
# grade machinery resolved a line id against the last DIGEST only, so the
# founder could grade what the night reported and NOT the answer he had just
# read. The surface people actually use was the one surface with no way to
# say "no, that's wrong".
#
# An answer is therefore a gradeable line with its own section — but the
# digest renderer still walks SECTIONS, never this, because an answer is not
# something the night did and a digest that printed an empty "ANSWERED"
# heading every morning would be lying about its own shape.
ANSWER_SECTION = "answered"
GRADEABLE_SECTIONS = SECTIONS + (ANSWER_SECTION,)

# Beliefs below this confidence are reported as UNSURE rather than as things
# the system believes. UNCALIBRATED — it is the same bar the birth floor uses,
# so "not sure enough to say" means one thing across the product.
UNSURE_BELOW = 0.6


@dataclass(frozen=True)
class DigestLine:
    """One gradable statement.

    `id` is content-derived and STABLE: the founder grades a line, and the
    grade must still name the same statement tomorrow even though the digest
    is regenerated. Deriving it from (section, text) rather than from a row
    number is what makes a grade durable.
    """
    section: str
    text: str
    citations: Tuple[str, ...] = ()
    node_id: Optional[str] = None
    id: str = ""

    def __post_init__(self) -> None:
        if self.section not in GRADEABLE_SECTIONS:
            raise ValueError("unknown digest section %r" % (self.section,))
        if not self.id:
            h = hashlib.sha256(("%s|%s" % (self.section, self.text)).encode())
            object.__setattr__(self, "id", h.hexdigest()[:12])


@dataclass
class Digest:
    t0_ms: int
    t1_ms: int
    lines: List[DigestLine] = field(default_factory=list)
    coverage_pct: Optional[float] = None
    gaps: Tuple = ()
    claims_seen: int = 0
    unreadable: int = 0

    def section(self, name: str) -> List[DigestLine]:
        return [l for l in self.lines if l.section == name]

    def by_id(self, line_id: str) -> Optional[DigestLine]:
        for l in self.lines:
            if l.id == line_id:
                return l
        return None


def _in_window(t_ms: int, t0: int, t1: int) -> bool:
    return t0 <= t_ms <= t1


def _newest_attr_t(node) -> int:
    return max((a.t_ms for a in node.attributes), default=0)


def _citations(node) -> Tuple[str, ...]:
    out: List[str] = []
    for a in node.attributes:
        for ep in a.source_episode_ids:
            if ep not in out:
                out.append(ep)
    return tuple(out[:4])


def _best_confidence(node) -> float:
    return max((a.confidence.value for a in node.attributes), default=0.0)


def build_digest(graph_store, claim_log, t0_ms: int, t1_ms: int) -> Digest:
    """Read the window and render it. Never writes, never calls a model."""
    d = Digest(t0_ms=t0_ms, t1_ms=t1_ms)

    # -- what I SAW ---------------------------------------------------------
    # Perception first, and with its holes attached: coverage comes from
    # WITNESSES alone (app/coverage.py), never from claim density, because
    # claim density calls a quiet afternoon a blind spot and a broken loop
    # excellent coverage.
    witnesses = claim_log.read_range(t0_ms - GAP_LOOKBACK_MS, t1_ms)
    cov = compute_coverage(witnesses, t0_ms, t1_ms)
    d.coverage_pct = cov.percent
    d.gaps = cov.gaps
    d.unreadable = getattr(claim_log, "unreadable", lambda: 0)()

    claims = claim_log.read_range(t0_ms, t1_ms)
    d.claims_seen = len(claims)
    # WHAT I SAW, AS MOMENTS — not as a bar chart of claim counts.
    #
    # This used to read "Brave Browser — 29,241 observation(s)", which is
    # true, useless, and indistinguishable between a day of work and a day of
    # cartoons. A day is not a count; it is a handful of stretches of time on
    # particular things, and the AX sense has been reading the url and window
    # title all along. Same builder the ask path uses, so the digest and the
    # answer can never describe the same day differently.
    from tracemem.supplier import timeline as _tl

    moments = _tl.build([c for c in claims
                         if not (c.structured or {}).get("witness")])
    for m in sorted(moments, key=lambda m: m.duration_ms, reverse=True)[:8]:
        mins = m.duration_ms / 60000.0
        d.lines.append(DigestLine(
            section="saw",
            text="%s — %s (%.0f min)" % (m.app, m.label(), mins),
            citations=tuple(_tl.cite_id(c) for c in m.claim_ids[:2])))
    if not moments and claims:
        d.lines.append(DigestLine(
            section="saw",
            text="%d observation(s), none long enough to be a moment"
                 % len(claims)))
    if cov.percent_text is not None:
        # NEVER re-format this number here. `%.0f` on the capped 99.9 printed
        # "100%" next to "Blind for 3 interval(s)" in the founder's own
        # digest — see Coverage.percent_text for why the rendering lives
        # beside the rule instead of at the surface.
        note = "I was watching %s of this period." % cov.percent_text
        if cov.gaps:
            note += " Blind for %d interval(s)." % len(cov.gaps)
        d.lines.append(DigestLine(section="saw", text=note))

    # -- what I BELIEVE -----------------------------------------------------
    # Derived nodes only (depth >= 1). The entity tier is not a belief, it is
    # an observation with a name.
    for node in list(graph_store.nodes.values()):
        if getattr(node, "derivation_depth", 0) < 1:
            continue
        if not _in_window(_newest_attr_t(node), t0_ms, t1_ms):
            continue
        text = label_of(node)
        if not text:
            continue
        cites = _citations(node)
        if not cites:
            continue                    # a belief with no receipt is not shown
        line = DigestLine(
            section=("unsure" if _best_confidence(node) < UNSURE_BELOW
                     else "believe"),
            text=text, citations=cites, node_id=node.id)
        d.lines.append(line)

    # -- what CHANGED MY MIND ----------------------------------------------
    # The moat: beliefs that DIED. Retiring tombstones them rather than
    # vaporizing them precisely so this section can exist.
    for node, retired_t in graph_store.retired():
        if not _in_window(retired_t, t0_ms, t1_ms):
            continue
        text = label_of(node)
        if text:
            d.lines.append(DigestLine(
                section="changed",
                text="I no longer think: %s" % text,
                citations=_citations(node), node_id=node.id))

    return d


def render(d: Digest, when: str = "") -> str:
    """The founder's morning read. Plain text on purpose — it must be
    gradable from a terminal today, and legible in a web page later."""
    HEAD = {"saw": "WHAT I SAW", "believe": "WHAT I NOW BELIEVE",
            "changed": "WHAT CHANGED MY MIND", "unsure": "WHAT I'M UNSURE OF"}
    out = ["TRACE digest%s" % (" — %s" % when if when else ""),
           "=" * 60]
    for name in SECTIONS:
        lines = d.section(name)
        out.append("")
        out.append(HEAD[name])
        if not lines:
            # Said, not omitted. An empty section is a fact about the night.
            out.append("  (nothing)")
            continue
        for l in lines:
            cite = ("  [%s]" % ",".join(c[:8] for c in l.citations[:2])
                    if l.citations else "")
            out.append("  %s  %s%s" % (l.id, l.text, cite))
    out.append("")
    out.append("-" * 60)
    out.append("%d observation(s) in window." % d.claims_seen)
    if d.unreadable:
        out.append("%d claim(s) I cannot read (written under another key)."
                   % d.unreadable)
    out.append("Grade a line:  ./trace grade <id> right|wrong|meh "
               "[\"the correct thing, in one sentence\"]")
    return "\n".join(out)
