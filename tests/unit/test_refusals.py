"""ORACLE for Refusal discipline (09_yours > The refusals).

PROOF: "DISPATCH.md references the refusals; no refused feature appears in
any atom."

A refusal that exists only as a decision decays. Every one of the three is
something a competent engineer would propose in good faith — keylogging is
the obvious way to capture text the eye missed, telemetry is the obvious
way to learn what is breaking, a second frontier path is the obvious way to
ship one feature today. Each is correct engineering and a broken promise,
and the only defence against a good argument made at 2am is a decision that
was already made and is mechanically checked.

So this file checks two things a document cannot check itself: that the
forbidden APIs are absent from `src/`, and that no atom in the map has
started proposing a refused feature.
"""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "tracemem"
TREE = ROOT / "campaign" / "tree"
DISPATCH = ROOT / "campaign" / "DISPATCH.md"
# The campaign map and DISPATCH are the founder's local workspace, not part
# of the published code; the checks that read them run only where they exist.
# The mechanical checks on src/ below always run.
needs_campaign = pytest.mark.skipif(
    not (TREE.is_dir() and DISPATCH.is_file()),
    reason="campaign workspace is local to the founder's machine")

# The APIs each refusal actually forbids. Names, not intentions — an
# intention cannot be grepped.
FORBIDDEN_APIS = {
    "keylogging": [
        "CGEventTapCreate", "CGEventTap", "addGlobalMonitorForEventsMatchingMask",
        "IOHIDManagerCreate", "kCGEventKeyDown", "NSEventMaskKeyDown",
    ],
    "telemetry": [
        "analytics", "telemetry", "crashlytics", "sentry_sdk", "mixpanel",
        "amplitude", "posthog",
    ],
}

REFUSAL_NAMES = ("No keylogging", "No telemetry, no analytics",
                 "No cloud processing")


def source_files():
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in str(p)]


def code_lines(path):
    """Lines that are not comments or docstring prose.

    Necessary because this codebase documents its refusals IN the modules
    that could have violated them — `presence.py` explains why it uses idle
    APIs instead of a CGEventTap. A naive grep would flag the very comment
    that proves the rule is being followed.
    """
    text = path.read_text()
    # Strip triple-quoted blocks, then line comments.
    text = re.sub(r'"""(?:.|\n)*?"""', "", text)
    text = re.sub(r"'''(?:.|\n)*?'''", "", text)
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        code = line.split("#", 1)[0]
        if code.strip():
            out.append((i, code))
    return out


# -- the mechanical half ---------------------------------------------------
@pytest.mark.parametrize("refusal", sorted(FORBIDDEN_APIS))
def test_no_forbidden_api_appears_in_the_source(refusal):
    offenders = []
    for path in source_files():
        for lineno, code in code_lines(path):
            for api in FORBIDDEN_APIS[refusal]:
                if api.lower() in code.lower():
                    offenders.append("%s:%d: %s"
                                     % (path.relative_to(ROOT), lineno,
                                        code.strip()))
    assert not offenders, (
        "the '%s' refusal is violated in code:\n  " % refusal
        + "\n  ".join(offenders))


def test_the_keylogging_refusal_is_checked_against_real_api_names():
    """A test whose forbidden list is vague passes forever. These are the
    actual symbols a keyboard tap requires — if none of them can appear,
    the capability cannot be built by accident."""
    assert "CGEventTapCreate" in FORBIDDEN_APIS["keylogging"]
    assert "IOHIDManagerCreate" in FORBIDDEN_APIS["keylogging"]
    assert len(FORBIDDEN_APIS["keylogging"]) >= 5


def test_presence_sensing_uses_idle_apis_not_event_taps():
    """The refusal names the permitted alternative, so the alternative had
    better be what is actually there."""
    presence = SRC / "capture" / "presence.py"
    if not presence.exists():
        pytest.skip("presence module not present")
    text = presence.read_text().lower()
    assert "idle" in text


# -- no atom proposes a refused feature ------------------------------------
def _leaves(node, out, path=""):
    here = path + " > " + node.get("name", "")
    if not node.get("kids"):
        out.append((here, node))
    for kid in node.get("kids", []):
        _leaves(kid, out, here)


def all_leaves():
    out = []
    for f in sorted(TREE.glob("*.json")):
        _leaves(json.loads(f.read_text()), out, f.stem)
    return out


@needs_campaign
def test_no_atom_proposes_a_refused_feature():
    """The other half of the oracle. A refusal loses to a roadmap slowly:
    first an atom appears describing the thing, then it looks planned, then
    it looks agreed."""
    proposing = re.compile(
        r"\b(keylog\w*|key.?stroke capture|capture keystrokes|"
        r"usage analytics|send telemetry|crash upload|"
        r"second frontier|another frontier path)\b", re.I)

    offenders = []
    for path, leaf in all_leaves():
        # Leaves that RECORD a refusal necessarily name it. Skipping by the
        # word "refus" covers both "The refusals (written NOs)" and
        # 01_world's "Refused senses (law)" — the second one is why this
        # match is on the whole path rather than an exact list.
        if "refus" in path.lower():
            continue
        blob = " ".join(str(leaf.get(k, "")) for k in
                        ("name", "plain", "brief", "oracle", "state"))
        match = proposing.search(blob)
        if match:
            offenders.append("%s: %r" % (path, match.group(0)))
    assert not offenders, (
        "atoms proposing refused features:\n  " + "\n  ".join(offenders))


@needs_campaign
def test_the_refusals_are_still_in_the_map():
    """The cheapest way to defeat a refusal is to delete it. If one of these
    disappears, this test fails before anyone notices the absence."""
    names = {leaf.get("name") for _p, leaf in all_leaves()}
    for refusal in REFUSAL_NAMES:
        assert refusal in names, "refusal removed from the map: %s" % refusal


@needs_campaign
def test_every_refusal_is_recorded_as_complete():
    """A refusal is complete the moment it is recorded — there is nothing to
    build, only something to keep not building. A refusal sitting at 40%
    reads like unfinished work somebody could helpfully finish."""
    for path, leaf in all_leaves():
        if leaf.get("name") in REFUSAL_NAMES:
            assert leaf.get("pct") == 100, path


# -- DISPATCH points workers here -----------------------------------------
@needs_campaign
def test_dispatch_references_the_refusals():
    """PROOF, first clause. A worker reads DISPATCH and nothing else; a
    refusal it does not mention is a refusal a fresh session will not know
    about."""
    text = DISPATCH.read_text()
    assert "refusals" in text.lower()
    for refusal in REFUSAL_NAMES:
        assert refusal.split(",")[0] in text, refusal


@needs_campaign
def test_dispatch_says_a_refusal_answers_rather_than_weighs():
    """The distinction the whole atom turns on. 'Consider the refusals' is
    an invitation to trade them away."""
    # Whitespace-normalised: the phrase wraps across lines in the document,
    # and a test that only matched the unwrapped form would fail on
    # reflowing rather than on meaning.
    text = " ".join(DISPATCH.read_text().lower().split())
    assert "answered by the refusal" in text
    assert "founder ruling" in text


@needs_campaign
def test_dispatch_names_where_overturning_happens():
    text = DISPATCH.read_text().lower()
    assert "board" in text
    assert "never a worker" in text


# -- the ear does not phone home (found 2026-07-23) -----------------------
def test_the_ear_loads_its_model_offline():
    """REGRESSION, and a zero-egress property rather than a speed one.

    faster-whisper's default load performs a HuggingFace hub revision check
    over the network EVERY time, even when the weights are already cached.
    It surfaced as a suite that went from 16s to 10 minutes — one test at
    603.80s and 0% CPU — but the real problem is where that call sits:
    inside the capture path, in a product whose entitlements declare
    com.apple.security.network.client = false and whose own doc calls that
    "the entitlement that would let a memory of your whole life leave your
    Mac".

    It would also mean the ear silently stops working on a plane, in a way
    no error message would explain. Weights are a build-time artifact, not
    a runtime dependency.
    """
    source = (SRC / "extraction" / "asr_local.py").read_text()
    assert "local_files_only=True" in source


def test_the_semantic_embedder_loads_its_sealed_model_offline():
    source = (SRC / "supplier" / "embedder_st.py").read_text()
    assert "local_files_only=True" in source


def test_no_organ_downloads_anything_at_load_time():
    """The class, not the instance. Any adapter reaching the network to
    initialise itself is doing it in the capture path."""
    suspicious = ("from_pretrained(", "hf_hub_download", "snapshot_download",
                  "urlretrieve", "requests.get")
    offenders = []
    for path in source_files():
        for lineno, code in code_lines(path):
            for pattern in suspicious:
                if pattern in code and "local_files_only" not in code:
                    offenders.append("%s:%d: %s"
                                     % (path.relative_to(ROOT), lineno,
                                        code.strip()))
    assert not offenders, (
        "network fetch at model-load time:\n  " + "\n  ".join(offenders))
