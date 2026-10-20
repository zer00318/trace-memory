"""ORACLE for Model registry + Sampling law + Context budget ledger (07_llm).

PROOFS:
  Model registry  "Adapter test: unregistered model name -> loud refusal."
  Sampling law    "Adapter test green; sampling params visible in every
                   logged request."
  Context ledger  "Grep test: no token-count literals outside the ledger;
                   packer test respects per-iteration allowance."

The grep test is the one that keeps the ledger meaningful after today. A
constants file nothing is forced to use decays into a suggestion, and the
failure it is meant to prevent is silent: a packer that believes it has more
room than the prompt left it truncates evidence, and a truncated pack
produces a confident answer missing the very claim that would have
contradicted it.
"""
import json
import re
from pathlib import Path

import pytest

from tracemem.llm import registry
from tracemem.llm.reasoner_ollama import OllamaReasoner
from tracemem.llm.registry import (
    ANSWER_FRACTION, CONVERSATION_FRACTION, EVIDENCE_FRACTION,
    MAX_DIALOGUE_ITERATIONS, REGISTRY, SAMPLING, SYSTEM_FRACTION, TEMPERATURE,
    TOP_P, UnblessedModel, UnregisteredModel, budget_for, get, mouth,
    sampling_for,
)

SRC = Path(__file__).resolve().parents[2] / "src" / "tracemem"


# -- the registry ----------------------------------------------------------
def test_every_entry_carries_what_makes_it_reproducible():
    """Tag, digest, role, window. A model missing its digest is a model
    whose weights can change under a re-pull without anyone noticing."""
    for tag, entry in REGISTRY.items():
        assert entry.tag == tag
        assert re.fullmatch(r"[0-9a-f]{12}", entry.digest), tag
        assert entry.role in ("mouth", "stand-in", "embedder"), tag
        assert entry.context_window > 0
        assert entry.notes.strip(), "%s has no explanation" % tag


def test_an_unregistered_model_is_refused_loudly():
    with pytest.raises(UnregisteredModel) as exc:
        get("llama3:70b-whatever")
    assert "drive-by" in str(exc.value)
    assert "registry.py" in str(exc.value)


def test_the_refusal_names_what_is_registered():
    """An error that says 'no' without saying 'these are the options' costs
    the reader a trip into the source."""
    with pytest.raises(UnregisteredModel) as exc:
        get("nope")
    assert "gemma3:12b-it-qat" in str(exc.value)


def test_no_model_is_blessed_yet_and_the_gate_says_so():
    """The honest state on 2026-07-22: the lie-catch harness is night-gated
    and has not run, so nothing has earned the right to answer a human.
    Registering a model says it exists; only a measurement says it may
    speak."""
    with pytest.raises(UnblessedModel) as exc:
        mouth("gemma3:12b-it-qat")
    assert "night-gated" in str(exc.value)
    assert registry.blessed_mouths() == ()


def test_a_non_mouth_may_not_answer_even_if_blessed():
    with pytest.raises(UnregisteredModel) as exc:
        mouth("bge-m3:latest")
    assert "not 'mouth'" in str(exc.value)


def test_the_unused_embedders_explain_themselves():
    """Both embedding models are present on the machine and unused. If the
    registry did not say why, the next reader would wire one up and be
    quietly defeated by the broken endpoint."""
    for tag in ("bge-m3:latest", "nomic-embed-text:latest"):
        assert "sentence-transformers" in REGISTRY[tag].notes \
            or "same as" in REGISTRY[tag].notes.lower()


# -- THE ORACLE: the adapter refuses ---------------------------------------
def test_the_adapter_refuses_an_unregistered_model():
    """The registry gate where it matters. In the CONSTRUCTOR, so a bad tag
    fails when the adapter is built rather than three seconds into a night
    run."""
    with pytest.raises(UnregisteredModel):
        OllamaReasoner("mistral:7b-not-ours")


def test_the_adapter_accepts_a_registered_model():
    adapter = OllamaReasoner("gemma3:12b-it-qat")
    assert adapter.entry.digest == "5d4fa005e7bb"


# -- THE ORACLE: sampling params on every request --------------------------
def test_every_request_carries_the_sampling_law():
    seen = {}

    def transport(url, payload, timeout_s):
        seen.update(payload)
        return "ok"

    OllamaReasoner("gemma3:12b-it-qat", transport=transport).complete("hi")
    assert seen["options"] == {"temperature": TEMPERATURE, "top_p": TOP_P}


def test_a_retried_request_still_carries_them():
    """Retry rebuilds nothing, but a future refactor might. The params must
    survive the respawn-window path, which is where a night run actually
    lives."""
    import socket
    calls = []

    def transport(url, payload, timeout_s):
        calls.append(dict(payload))
        if len(calls) < 2:
            raise ConnectionRefusedError("respawn window")
        return "ok"

    OllamaReasoner("gemma3:12b-it-qat", transport=transport,
                   sleep=lambda _s: None).complete("hi")
    assert len(calls) == 2
    assert all(c["options"]["temperature"] == TEMPERATURE for c in calls)


def test_sampling_is_near_greedy_but_not_exactly_zero():
    """Zero degenerates into repetition loops on long contexts in some
    runtimes; high temperature buys variety at the cost of fidelity and
    makes a wrong answer unreproducible."""
    assert 0.0 < TEMPERATURE <= 0.2
    assert 0.5 <= TOP_P <= 1.0


def test_sampling_params_cannot_be_obtained_for_an_unregistered_model():
    with pytest.raises(UnregisteredModel):
        sampling_for("ghost:latest")


def test_the_sampling_table_is_not_mutated_by_callers():
    got = sampling_for("gemma3:12b-it-qat")
    got["temperature"] = 99.0
    assert SAMPLING["temperature"] == TEMPERATURE


# -- the context budget ledger --------------------------------------------
def test_the_fractions_leave_room_for_an_answer():
    """A budget that spends the whole window has no room to reply, and fails
    at generation time — far from where the decision was made."""
    total = (SYSTEM_FRACTION + CONVERSATION_FRACTION + EVIDENCE_FRACTION
             + ANSWER_FRACTION)
    assert total == pytest.approx(1.0)
    assert ANSWER_FRACTION > 0


def test_evidence_gets_the_largest_share():
    """By design: this is a memory system, and the evidence pack IS the
    memory. A split that favoured conversation history would be a chatbot."""
    assert EVIDENCE_FRACTION > max(SYSTEM_FRACTION, CONVERSATION_FRACTION,
                                   ANSWER_FRACTION)


def test_the_budget_is_proportional_to_the_model_window():
    small = budget_for("bge-m3:latest")
    large = budget_for("gemma3:12b-it-qat")
    assert large["evidence"] > small["evidence"]
    assert small["window"] == 8_192 and large["window"] == 128_000


def test_iterations_divide_only_the_evidence_share():
    """The framing does not shrink because a conversation got longer, and
    the last turn needs room to answer as much as the first."""
    one = budget_for("gemma3:12b-it-qat", iterations=1)
    four = budget_for("gemma3:12b-it-qat", iterations=4)
    assert four["evidence"] == one["evidence"] // 4
    assert four["system"] == one["system"]
    assert four["answer"] == one["answer"]


def test_a_dialogue_deeper_than_the_ledger_allows_is_an_error():
    """Not a silent squeeze: at some depth the per-iteration evidence
    allowance stops being enough to say anything, and that should be a
    decision somebody makes rather than a slow fade."""
    with pytest.raises(ValueError):
        budget_for("gemma3:12b-it-qat", iterations=MAX_DIALOGUE_ITERATIONS + 1)
    with pytest.raises(ValueError):
        budget_for("gemma3:12b-it-qat", iterations=0)


def test_an_unregistered_model_has_no_budget():
    with pytest.raises(UnregisteredModel):
        budget_for("ghost:latest")


# -- THE GREP ORACLE -------------------------------------------------------
def test_no_token_count_literals_live_outside_the_ledger():
    """The atom's PROOF, verbatim. A constants file nothing is forced to use
    decays into a suggestion.

    Looks for suspiciously token-shaped numbers (4096, 8192, 32000, 128000,
    and friends) anywhere in llm/ or supplier/ except the ledger itself.
    """
    suspicious = re.compile(
        r"\b(1024|2048|4096|8192|16384|32768|32000|32_000|65536|"
        r"100000|100_000|128000|128_000)\b")
    offenders = []
    for path in list((SRC / "llm").rglob("*.py")) + \
            list((SRC / "supplier").rglob("*.py")):
        if path.name == "registry.py":
            continue
        for i, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"'):
                continue
            if suspicious.search(line):
                offenders.append("%s:%d: %s" % (path.name, i, stripped))
    assert not offenders, (
        "token-count literals outside the ledger:\n  " + "\n  ".join(offenders))


# -- the hostile fixtures --------------------------------------------------
HOSTILE = Path(__file__).resolve().parents[2] / "exams" / "hostile_packs.json"


def hostile():
    return json.loads(HOSTILE.read_text())


def test_the_hostile_set_exists_and_is_well_formed():
    """PROOF (Hostile fixtures): 'Fixtures exist and run in the injection
    test.'"""
    data = hostile()
    packs = data["packs"]
    assert len(packs) >= 10
    for pack in packs:
        assert pack["id"] and pack["question"]
        assert pack["attack"]
        assert pack["obeyed_if"].strip()
        assert pack["why_it_is_an_attack"].strip()
        assert len(pack["blocks"]) >= 2
        for block in pack["blocks"]:
            assert block["id"] and block["text"] and block["grade"]


def test_the_attacks_are_actually_varied():
    """Ten variations of 'ignore your instructions' is one fixture with nine
    copies. The set is only a red team if it attacks different seams."""
    kinds = {p["attack"] for p in hostile()["packs"]}
    assert len(kinds) >= 8


def test_there_is_a_control_that_must_not_be_refused():
    """The failure mode a red-team set creates in the thing it defends: a
    model that learns to refuse anything containing 'ignore all previous'
    starts censoring the founder's own documentation. That costs them the
    product, which is worse than the injections cost them safety."""
    controls = [p for p in hostile()["packs"] if "CONTROL" in p["attack"]]
    assert len(controls) == 1
    assert "REFUSES" in controls[0]["obeyed_if"]


def test_the_unrun_status_is_stated_not_implied():
    """No injection-resistance number exists yet. The file must say so, or
    someone will quote a score this set has never produced."""
    data = hostile()
    assert "NEVER RUN" in data["status"]
    assert "night-gated" in data["status"]


def test_every_block_id_is_unique_within_its_pack():
    """Duplicate ids would make a citation ambiguous, and the citation-
    forgery pack would then be testing the wrong thing."""
    for pack in hostile()["packs"]:
        ids = [b["id"] for b in pack["blocks"]]
        assert len(ids) == len(set(ids)), pack["id"]
