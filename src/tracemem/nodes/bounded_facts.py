"""OWNS: which facts about a life naturally expire, and what closes them.

`The living cell / Facts that expire / Which facts are bounded`. The
machinery landed first (`accrual.true_at` answers true-for-then), and it is
useless without an answer to WHICH keys it should apply to. This is that
list, PROPOSED — the leaf's oracle asks for a founder review, so nothing here
is a ruling.

THE DISTINCTION THAT MATTERS. Some facts are bounded because the world
changes: an employer, an address, a phone. Others are bounded only in the
sense that any observation could be superseded — a person's name, a
building's location. Treating the second kind as bounded makes a memory
nervous about things that are not in doubt; treating the first kind as
unbounded makes it confidently wrong about a life that moved on.

WHAT OPENS AND WHAT CLOSES A BOUND, per kind, because "it expires" is not a
rule anyone can implement. A bound OPENS when the first observation of a
value lands. It CLOSES when a DIFFERENT value for the same key is observed —
never on a timer. A fact does not stop being true because nobody looked at
it, and expiring on silence would make a laptop left shut for a fortnight
forget where someone works.

THE ASYMMETRY IS DELIBERATE. Nothing here shortens a bound on absence alone.
The worst failure available to this module is a memory that quietly decides
a true thing has stopped being true, because nothing downstream can tell that
from the thing genuinely having changed.
"""
from __future__ import annotations

from typing import Dict, NamedTuple


class Bound(NamedTuple):
    key: str
    plain: str
    opens: str
    closes: str


# PROPOSED. Founder reviews once; see the module docstring.
BOUNDED: Dict[str, Bound] = {
    b.key: b for b in [
        Bound("employer", "where someone works",
              "a new employer is observed",
              "a different employer is observed — never a gap in evidence"),
        Bound("address", "where someone lives",
              "a new address is observed",
              "a different address is observed"),
        Bound("place", "where a routine happens",
              "a routine is observed at a place",
              "the same routine is observed at a different place"),
        Bound("device", "which machine is in use",
              "a claim arrives stamped with a device",
              "claims arrive stamped with another device"),
        Bound("app", "what someone uses for a job of work",
              "the app is seen doing that job",
              "a different app is seen doing the same job"),
        Bound("price", "what a thing costs",
              "a price is read",
              "a different price for the same thing is read"),
        Bound("role", "what someone does",
              "a role is stated",
              "a different role is stated"),
    ]
}

# NOT BOUNDED, and listed so the absence is a decision rather than an
# oversight. These change so rarely that treating them as expiring would make
# the memory hedge about things nobody is in doubt about — and every hedge it
# spends here is one a reader stops believing when it matters.
UNBOUNDED = {
    "seen_as": "what someone is called",
    "site": "a website's identity",
    "topic": "what a thing is about",
    "routine": "a habit, which weakens rather than expires",
}


def is_bounded(key: str) -> bool:
    return str(key or "").lower() in BOUNDED


def rule_for(key: str):
    return BOUNDED.get(str(key or "").lower())


def render() -> str:
    """The list as the founder reviews it."""
    out = ["FACTS THAT EXPIRE (proposed — needs your review)", "=" * 52, ""]
    for b in BOUNDED.values():
        out.append("  %-10s %s" % (b.key, b.plain))
        out.append("             opens:  %s" % b.opens)
        out.append("             closes: %s" % b.closes)
        out.append("")
    out.append("NOT TREATED AS EXPIRING, deliberately:")
    for k, why in sorted(UNBOUNDED.items()):
        out.append("  %-10s %s" % (k, why))
    out.append("")
    out.append("Nothing here expires on SILENCE. A fact does not stop being")
    out.append("true because nobody looked at it, and a laptop left shut for")
    out.append("a fortnight must not forget where you work.")
    return "\n".join(out)
