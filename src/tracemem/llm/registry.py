"""OWNS: the model registry, the sampling law, and the context budget ledger
(map: LLM / Model registry, Sampling law, Context budget ledger).

Three atoms in one file because they are one fact about a model wearing
three names: which models may speak, how they are sampled, and how much
room their window has. Splitting them would mean a model could be added in
one file and left without a budget in another, which is exactly the drift
each of them exists to prevent.

NO DRIVE-BY MODEL SWAPS. The adapter refuses any model not listed here. It
is easy to read that as bureaucracy; it is not. A model tag is the single
biggest determinant of what the mouth says, and an answer produced by an
unrecorded model is an answer nobody can reproduce, re-score, or defend.
Every never-lie score on the cockpit is a score OF a specific model at a
specific quantization — the registry is what makes that sentence mean
something.

THE ENTRIES ARE REAL. Tags and digests below were read from this Mac with
`ollama list` on 2026-07-22, not invented. What is NOT real yet is the
`blessed_by` field on every one of them: no model here has passed the
lie-catch harness, because that is night-gated (one-GPU law) and has not
run. They are therefore registered as CANDIDATES, and `mouth()` refuses to
return an unblessed model. The registry is allowed to know about a model it
does not yet trust; the product is not allowed to answer with one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


class UnregisteredModel(Exception):
    """Raised loudly rather than defaulting. A default model is a drive-by
    swap with extra steps."""


class UnblessedModel(Exception):
    """Registered but never measured. Knowing a model exists is not the same
    as trusting it to speak."""


@dataclass(frozen=True)
class ModelEntry:
    tag: str                 # exactly what the adapter sends to ollama
    digest: str              # the id ollama reports; pins the weights
    role: str                # "mouth" | "stand-in" | "embedder"
    context_window: int      # tokens
    blessed_by: str = ""     # the measurement that earned it the mouth role
    notes: str = ""

    @property
    def blessed(self) -> bool:
        return bool(self.blessed_by)


# Read from this Mac 2026-07-22 (`ollama list`). Digests are the truncated
# ids ollama prints; they pin the weights, so a re-pull that changes them is
# a different model and must be re-blessed.
REGISTRY: Dict[str, ModelEntry] = {
    "gemma3:12b-it-qat": ModelEntry(
        tag="gemma3:12b-it-qat", digest="5d4fa005e7bb", role="mouth",
        context_window=128_000,
        notes="QAT int4. The candidate mouth: the largest model that leaves "
              "GPU headroom for capture under the one-model law."),
    "gemma3:27b-it-qat": ModelEntry(
        tag="gemma3:27b-it-qat", digest="29eb0b9aeda3", role="stand-in",
        context_window=128_000,
        notes="18 GB. Ceiling reference for the quantization bench, NOT a "
              "live mouth — it cannot share this machine with capture."),
    "bge-m3:latest": ModelEntry(
        tag="bge-m3:latest", digest="790764642607", role="embedder",
        context_window=8_192,
        notes="Registered for completeness. NOT USED: ollama's embedding "
              "endpoint is broken on this machine, so embeddings come from "
              "sentence-transformers on CPU."),
    "nomic-embed-text:latest": ModelEntry(
        tag="nomic-embed-text:latest", digest="0a109f422b47", role="embedder",
        context_window=8_192,
        notes="Same as bge-m3: present on the machine, unused for the same "
              "reason. Listed so its presence is explained rather than "
              "mysterious."),
}


# -- the sampling law ------------------------------------------------------
#
# Near-greedy, and the same for every model. A memory system's mouth is not
# being asked to be interesting; it is being asked to say what the evidence
# says. Temperature is the knob that buys variety at the cost of fidelity,
# which is precisely the wrong trade here — and worse, it makes a wrong
# answer unreproducible, so a lie caught on Tuesday cannot be re-caught on
# Wednesday to check the fix.
#
# Not zero: exact-0 greedy decoding on some runtimes degenerates into
# repetition loops on long contexts. 0.1 keeps that away while staying
# effectively deterministic.
TEMPERATURE = 0.1
TOP_P = 0.9

SAMPLING: Dict[str, float] = {"temperature": TEMPERATURE, "top_p": TOP_P}


# -- the context budget ledger --------------------------------------------
#
# ONE place that knows token counts. The packer and the prompt both import
# from here; the atom's oracle is a grep asserting no token-count literals
# live anywhere else. The failure it prevents is the quiet one: a packer
# that believes it has more room than the prompt left it silently truncates
# evidence, and a truncated pack produces a confident answer missing the
# very claim that would have contradicted it.
#
# The split is expressed as FRACTIONS of the model's window, not absolute
# counts, so a model with a different window gets a proportional budget
# rather than one copied from a model it has nothing in common with.
SYSTEM_FRACTION = 0.05        # the prompt's fixed framing
CONVERSATION_FRACTION = 0.15  # dialogue history
EVIDENCE_FRACTION = 0.60      # the claim pack — the biggest share, by design
# The remaining 0.20 is headroom for the model's own answer. Named rather
# than implied, because a budget that spends 100% of a window leaves no room
# to reply and fails at generation time, far from where it was decided.
ANSWER_FRACTION = 0.20

# Dialogue iterations the evidence budget is divided across. A follow-up
# question must not be able to spend the whole window on turn one.
MAX_DIALOGUE_ITERATIONS = 4


def _fractions() -> Tuple[float, ...]:
    return (SYSTEM_FRACTION, CONVERSATION_FRACTION, EVIDENCE_FRACTION,
            ANSWER_FRACTION)


def get(tag: str) -> ModelEntry:
    """The registry gate. Raises rather than defaulting."""
    entry = REGISTRY.get(tag)
    if entry is None:
        raise UnregisteredModel(
            "model %r is not in the registry — no drive-by model swaps. "
            "Add it to llm/registry.py with its tag, digest, role and the "
            "measurement that blessed it. Registered: %s"
            % (tag, sorted(REGISTRY)))
    return entry


def mouth(tag: str) -> ModelEntry:
    """The stricter gate for the model that actually answers a human."""
    entry = get(tag)
    if entry.role != "mouth":
        raise UnregisteredModel(
            "model %r is registered with role %r, not 'mouth' — it may not "
            "answer questions" % (tag, entry.role))
    if not entry.blessed:
        raise UnblessedModel(
            "model %r has no blessing measurement. Registering a model says "
            "it exists; only the lie-catch harness says it may speak. That "
            "run is night-gated (one-GPU law) and has not happened."
            % (tag,))
    return entry


def budget_for(tag: str, iterations: int = 1) -> Dict[str, int]:
    """Token allowances for one dialogue iteration.

    `iterations` divides the EVIDENCE share only: the system framing does not
    shrink because a conversation got longer, and the answer still needs room
    on the last turn as much as the first.
    """
    if iterations < 1:
        raise ValueError("iterations must be >= 1, got %r" % (iterations,))
    if iterations > MAX_DIALOGUE_ITERATIONS:
        raise ValueError(
            "iterations %d exceeds MAX_DIALOGUE_ITERATIONS=%d — a dialogue "
            "that deep needs a deliberate decision, not a silent squeeze"
            % (iterations, MAX_DIALOGUE_ITERATIONS))
    window = get(tag).context_window
    return {
        "window": window,
        "system": int(window * SYSTEM_FRACTION),
        "conversation": int(window * CONVERSATION_FRACTION),
        "evidence": int(window * EVIDENCE_FRACTION / iterations),
        "answer": int(window * ANSWER_FRACTION),
    }


def sampling_for(tag: str) -> Dict[str, float]:
    """Sampling params for a registered model. Going through the registry
    (rather than importing SAMPLING directly) means an unregistered tag
    cannot acquire sampling params by accident."""
    get(tag)
    return dict(SAMPLING)


def registered_tags() -> Tuple[str, ...]:
    return tuple(sorted(REGISTRY))


def blessed_mouths() -> Tuple[str, ...]:
    return tuple(sorted(t for t, e in REGISTRY.items()
                        if e.role == "mouth" and e.blessed))
