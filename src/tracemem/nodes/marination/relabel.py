"""OWNS: beliefs re-said in English, and beliefs that should never have been.

RE-MARINATION, WHICH IS WHY RAW IS KEPT FOREVER. F-RAW: "a node re-reading
its own old claims with grammars it did not have at the time". This is the
first pass that actually does it.

WHAT IT REPAIRS. Until 2026-08-26 every recurrence belief was rendered
`recurring: %s=%s observed in %d distinct moments`, printing the raw
attribute key and value into the thing a person is meant to read. Measured on
the founder's real graph the morning after the fix: 190 beliefs, 112 sayable
— so 78 of them still read like database rows, and would for ever, because
the new template only applies to beliefs derived after it existed.

TWO OUTCOMES, and the difference is the point.

  · A belief over a key that CAN be said gets re-said. `site=lichess.org`
    becomes "you keep going back to lichess.org". Same evidence, same
    citations, same node — only the sentence changes, because only the
    sentence was ever wrong.

  · A belief over a key that cannot be said is RETIRED. `page=/watch` is a
    URL path; there is no phrasing that makes it a fact about a life, and
    the current detector would not mint it at all. Retired, not deleted —
    it goes to the moat like any belief that died, so "what changed my mind"
    can still show it.

IT NEVER INVENTS. The old text carries its own key and value; both are read
back out of it and re-rendered by the SAME table the detector now uses. A
belief this cannot parse is left exactly as it is — leaving one ugly sentence
is a smaller harm than rewriting a person's memory from a guess.
"""
from __future__ import annotations

import re
from typing import Dict, List

# The old format, verbatim: "recurring: key=value observed in N distinct
# moments". Anchored, because a belief that merely CONTAINS something like
# this — a founder correction quoting one, say — must not be rewritten.
_OLD = re.compile(
    r"^recurring:\s*([a-z_]+)=(.+?)\s+observed in\s+(\d+)\s+distinct moments$",
    re.I)


def parse_old(text: str):
    """(key, value, count) from an old-format belief, or None."""
    m = _OLD.match(str(text or "").strip())
    if not m:
        return None
    return m.group(1).lower(), m.group(2).strip(), int(m.group(3))


def relabel_pass(graph_store, t_ms: int = 0) -> Dict[str, object]:
    """Re-say what can be said; retire what cannot. Returns what it did."""
    from tracemem.domain.grading import retracted
    from tracemem.domain.node import EMERGENT_KINDS
    from tracemem.nodes.combination.kinds.pattern import _SAYABLE

    resaid: List[str] = []
    retired: List[str] = []
    try:
        nodes = list(graph_store.nodes.values())
    except Exception:                                        # noqa: BLE001
        return {"resaid": [], "retired": [], "scanned": 0}

    scanned = 0
    for n in nodes:
        if getattr(n, "kind", None) not in EMERGENT_KINDS:
            continue
        scanned += 1
        touched = False
        unsayable_only = True
        for a in n.attributes:
            if a.grade == retracted():
                continue
            got = parse_old(a.value)
            if got is None:
                unsayable_only = False       # something else speaks for it
                continue
            key, value, count = got
            template = _SAYABLE.get(key)
            if template is None:
                continue                     # handled below, at node level
            a.value = "%s — in %d moments" % (template % value, count)
            resaid.append(a.value)
            touched = True
            unsayable_only = False
        if unsayable_only and n.attributes:
            # EVERY THING THIS BELIEF SAYS IS UNSAYABLE. Retired, not
            # deleted: it goes to the moat like any belief that died, so
            # "what changed my mind" can still show it.
            try:
                graph_store.retire_meta(n.id, t_ms)
                retired.append(n.id)
                continue
            except Exception:                                # noqa: BLE001
                pass
        if touched:
            graph_store.upsert_node(n)
    return {"resaid": resaid, "retired": retired, "scanned": scanned}
