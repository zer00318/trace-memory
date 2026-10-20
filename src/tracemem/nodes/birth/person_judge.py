"""Is this capitalised word actually a PERSON? Asked once, of a model, at night.

WHY A REGEX CANNOT DO THIS. `entity_extractor._PERSON_CTX_RE` promotes any
capitalised word following "with/to/from/told/asked/met/thanks/hey/dear/cc".
Over prose that is a decent heuristic. Over OCR fragments of arbitrary screen
text it is not, and the founder's own graph measures the damage: 110 surfaces
were born as people and roughly 30 of them are people. The other 80 are
(categories as measured; the example words are illustrative stand-ins)

    cities      Springfield · Riverton · Lakeside · Norway · Peru
    apps        Claude · Instagram · Notes · Calendar · Google
    UI chrome   Trash · Watch · Edit · File · Folder · Next · Sign · Draft
    German      Kontakt · Bildquelle · Startseite · Impressum
    fragments   Aug · Jul · Mar · Also · Correction · Overview

No stoplist fixes this. The operating manual names that failure directly —
a rule per failure scales with engineering hours and never with data — and
the list above is not a list of mistakes, it is a list of CATEGORIES the
regex cannot represent. "Springfield" and "Marta" are the same shape.

Distinguishing them needs world knowledge, which is exactly what the local
model has and the regex does not. This is `specs/ONTRACK_ANALYSIS` §4's
recommendation ("wire the reasoner-as-judge into more than the one causal
call") applied where it pays most.

WHY IT DEFAULTS TO NO. The failure hierarchy is `4 never-lies > 5 mine >
2 exact > 1 saw > 3 noticed`. A memory that holds eighty people who do not
exist has already broken the first promise, and every belief derived from
them inherits it. A missing person costs coverage — the bottom of the list.
Anything the judge cannot affirm stays in the waiting room, in raw, and
returns the moment better evidence arrives.

WHY AT NIGHT. It is a model call, and model calls belong in the night window
under the one-GPU law. The verdict is recorded so the question is asked once
per surface, not once per sighting.
"""
from __future__ import annotations

from typing import Callable, List, Optional, Sequence

from tracemem.domain import Confidence
from tracemem.domain.grading import retracted

VERDICT_KEY = "person_verdict:%s"

PROMPT = (
    "You are reading fragments of text captured from a computer screen.\n"
    "The word in question is: \"%s\"\n\n"
    "Fragments where it appeared:\n%s\n\n"
    "Is that word being used as the NAME OF A PERSON — someone the user "
    "knows, messages, or is reading about as an individual?\n"
    "Answer no if it is a place, city, country, company, app, website, "
    "product, brand, UI label, menu item, month, or an ordinary word that "
    "merely happens to be capitalised.\n"
    "If you are not sure, answer no.\n"
    "Answer with one word: yes or no."
)

# What one verdict costs the night's token ledger. Small — a short prompt and
# a one-word answer — but NOT free, and the ledger must not be told otherwise.
# `test_night_spends_only_through_the_gate` caught this pass spending outside
# the gate, which is exactly the check that test exists to make: a budget with
# a way around it is not a budget.
JUDGE_TOKENS = 300

MAX_SAMPLES = 4
MAX_SAMPLE_CHARS = 160


def _render(samples: Sequence[str]) -> str:
    out = []
    for s in list(samples)[:MAX_SAMPLES]:
        text = " ".join(str(s).split())[:MAX_SAMPLE_CHARS]
        if text:
            out.append("  - %s" % text)
    return "\n".join(out) or "  (no surrounding text was captured)"


def is_person(surface: str, samples: Sequence[str], reasoner) -> bool:
    """One question, one word back. Any answer that is not 'yes' is a no."""
    raw = reasoner.complete(PROMPT % (surface, _render(samples)))
    word = str(raw).strip().lower()
    return word.startswith("yes")


def adjudicate(graph_store, pending, reasoner,
               samples_for: Callable[[str], List[str]],
               limit: int = 40, budget=None,
               stop: Optional[Callable[[], bool]] = None) -> dict:
    """Judge un-judged person surfaces. Returns what it decided.

    `pending` is an iterable of (key, surface) awaiting a verdict; `key` is
    the mention key used by the corroboration waiting room, so a verdict
    recorded here is found by the same key on the next upkeep.

    `stop` is polled before every model call. This runs on the daemon's one
    owner thread inside a night slice, and a slice is abandonable only where
    something asks whether it should stop (witnessed 2026-10-07: without a
    poll point here, every question timed out while the screen was locked).
    `limit` bounds ATTEMPTS, failed ones included — a failing model leaves
    the question open, but must not walk the whole waiting room paying a
    timeout per surface.
    """
    decided = {"asked": 0, "people": [], "not_people": [],
               "out_of_budget": False, "stopped": False}
    attempts = 0
    for key, surface in pending:
        if attempts >= limit:
            break
        meta_key = VERDICT_KEY % key
        if graph_store.get_meta(meta_key) is not None:
            continue
        if stop is not None and stop():
            decided["stopped"] = True
            break
        if budget is not None and not budget.may_spend(JUDGE_TOKENS):
            decided["out_of_budget"] = True
            break
        attempts += 1
        try:
            verdict = is_person(surface, samples_for(key), reasoner)
        except Exception:
            # An unreachable model must not silently become a NO that is then
            # cached forever. Leave it unjudged; the next night asks again.
            continue
        decided["asked"] += 1
        if budget is not None:
            budget.spend(JUDGE_TOKENS)
        graph_store.set_meta(meta_key, "yes" if verdict else "no")
        (decided["people"] if verdict else decided["not_people"]).append(surface)
    return decided


def verdict_for(graph_store, key: str) -> Optional[bool]:
    raw = graph_store.get_meta(VERDICT_KEY % key)
    if raw is None:
        return None
    return raw == "yes"


def unjudged_node_surfaces(graph_store):
    """[(key, surface)] for names ALREADY on the graph with no verdict.

    The waiting room only holds mentions that have not become nodes yet, so
    surfaces born before the gate existed would never be offered for
    judgement and would sit in the graph forever. Eight of them did.
    """
    from tracemem.domain import NodeKind

    out, seen = [], set()
    for node in graph_store.nodes.values():
        if node.kind is not NodeKind.ENTITY or node.derivation_depth != 0:
            continue
        for a in node.attributes:
            if a.key != "seen_as":
                continue
            surface = str(a.value).strip()
            if not surface or surface.lower() == "a person":
                continue
            key = "person:%s" % surface.lower()
            if key in seen or verdict_for(graph_store, key) is not None:
                continue
            seen.add(key)
            out.append((key, surface))
    return out


def enforce(graph_store) -> dict:
    """Strip judged-no names from the graph; un-birth what is left nameless.

    Runs after adjudication so a verdict takes effect on the SAME night it is
    reached, rather than only gating future births. Without this the graph
    keeps every false person it ever made and only stops adding new ones.
    """
    from tracemem.domain import NodeKind

    removed, stripped = [], []
    for node in list(graph_store.nodes.values()):
        if node.kind is not NodeKind.ENTITY or node.derivation_depth != 0:
            continue
        names = [a for a in node.attributes if a.key == "seen_as"]
        real = [a for a in names if str(a.value).lower() != "a person"]
        if not real:
            continue
        rejected = [a for a in real
                    if verdict_for(graph_store, "person:%s"
                                   % str(a.value).lower()) is False]
        if not rejected:
            continue
        if len(rejected) == len(real):
            graph_store.unbirth(node.id)
            removed.extend(str(a.value) for a in rejected)
            continue
        # A survivor keeps its real name and loses the false alias: the
        # resolver merges mentions, so one node held both "Hana" and
        # "Trash". Deleting it loses a person; keeping it whole leaves
        # "Trash" answerable as a name.
        #
        # DEMOTED, NOT DELETED — 2026-08-26. This was a hard delete, and it
        # was the ONLY path in the whole system that erased evidence without
        # leaving a trail. That matters more here than almost anywhere else:
        # the thing being erased is a judgement the machine made about a
        # PERSON'S NAME, and if it is wrong the founder has no way to see
        # that it was ever made. "Why did it stop calling her that?" had no
        # answer, and neither did "what else has it quietly decided?".
        #
        # Grade.FLOOR is the honest resting place. `label_of` picks the
        # highest-graded value, so a floored alias can never stand for the
        # node again — but it is still there to be read, counted and argued
        # with, which is the whole difference between a memory that revised
        # itself and one that rewrote itself.
        for a in rejected:
            a.grade = retracted()          # THE table, never a literal
            a.confidence = Confidence(0.0)
        graph_store.upsert_node(node)
        stripped.extend(str(a.value) for a in rejected)
    return {"unbirthed": removed, "aliases_stripped": stripped}
