"""The TODO ledger: one acceptance oracle per mechanism (spec 09 A8).

Convention: an oracle is xfail(strict=False) ONLY while its body is a stub.
When a body lands, the oracle is promoted to a real test — so `pytest -q`
always shows exactly how much of the system exists. What remains X-marked
below is exactly the hardware/model adapter set: things that need a camera,
a mic, macOS APIs, or model weights.
"""
import json

import pytest

from tests.fakes import FakeClock, FakeLease, HashEmbedder, ScriptedReasoner
from tests.helpers import make_claim

X = pytest.mark.xfail(raises=NotImplementedError, strict=False)


# ---- M1: claim log (spec 02) ----
def test_claim_log_append_is_idempotent(tmp_path):
    from tracemem.text.claim_log import ClaimLog
    log = ClaimLog(str(tmp_path / "c.db"), crypto=None)
    c = make_claim()
    log.append(c); log.append(c)
    assert len(list(log.read_range(0, 10_000))) == 1


# ---- M2: falsification slice (spec X) ----
def test_baseline_rag_answers_easy_factual(tmp_path):
    from tests.fakes import ContextAnswerReasoner
    from tracemem.evalx.baseline_rag import answer
    from tracemem.text.claim_log import ClaimLog
    log = ClaimLog(str(tmp_path / "c.db"))
    log.append(make_claim(text="reading the memory consolidation paper", t_ms=1000))
    log.append(make_claim(text="lunch order: ramen", t_ms=2000, frame="f-2"))
    a = answer("what paper was I reading?", log, HashEmbedder(),
               ContextAnswerReasoner(), budget=200)
    assert "memory consolidation" in a.text and not a.abstained


def test_decision_gate_is_deterministic():
    from tracemem.evalx.decision_gate import verdict
    v = verdict({"emergent_correct": 0.1, "factual_correct": 0.8})
    assert v.code == "CATHEDRAL_JUSTIFIED"
    assert verdict({"emergent_correct": 0.1, "factual_correct": 0.8}).code == v.code


def test_compression_probe_detects_plateau(tmp_path):
    from tracemem.evalx.compression_probe import novelty_curve, plateaus
    from tracemem.evalx.fixtures import synthetic_week
    from tracemem.text.claim_log import ClaimLog
    log = ClaimLog(str(tmp_path / "c.db"))
    for c in synthetic_week().claims:
        log.append(c)
    assert plateaus(novelty_curve(log, HashEmbedder()))


# ---- M3: binder reconcile (ruling A5) ----
def test_late_boundaries_rekey_with_tombstone_map():
    from tracemem.binder.reconcile import BoundaryReconciler
    claims = [make_claim(text="t%d" % t, t_ms=t, frame="f%d" % t)
              for t in (100, 900, 1500)]
    r = BoundaryReconciler(claim_source=lambda place, day: claims)
    first = r.apply_late_boundaries("wifi:aaaa", "d0", [2000])
    assert len(first.episodes) == 1
    old_id = next(iter(first.episodes.values())).id
    second = r.apply_late_boundaries("wifi:aaaa", "d0", [1000])   # late split
    assert old_id in second.tombstoned
    succ = r.successor_of(old_id)
    assert len(succ) == 2 and old_id not in succ                  # split resolves fully


# ---- M4: the heart (spec 04) ----
def test_guardrails_reject_raw_write():
    from tracemem.nodes.combination.guardrails import GuardrailViolation, assert_meta_write
    with pytest.raises(GuardrailViolation):
        assert_meta_write(make_claim())


def test_pattern_detector_needs_no_llm():
    import inspect
    from tracemem.nodes.combination.kinds import pattern
    from tracemem.domain import Attribute, Confidence, Grade, Node, NodeKind
    from tracemem.nodes.graph_store import MemoryGraphStore
    assert "reasoner" not in inspect.signature(pattern.detect).parameters
    g = MemoryGraphStore()
    n = Node(id="n1", kind=NodeKind.APP, attributes=[
        Attribute("app", "DoorDash", Grade.VERBATIM, Confidence(0.9),
                  ("e1", "e2", "e3"), 0)])
    g.upsert_node(n)
    props = pattern.detect(n, g)
    assert props[0].kind == "pattern" and set(props[0].supported_by) == {"e1", "e2", "e3"}


def test_causal_is_the_only_llm_spender():
    from tracemem.domain import (Attribute, Confidence, Edge, EdgeType, Grade,
                                 Node, NodeKind)
    from tracemem.nodes.combination.kinds import causal
    from tracemem.nodes.graph_store import MemoryGraphStore
    g = MemoryGraphStore()
    a = Node(id="a", kind=NodeKind.ENTITY, attributes=[
        Attribute("seen_as", "Sam", Grade.VERBATIM, Confidence(0.9), ("e1",), 0)])
    b = Node(id="b", kind=NodeKind.PLACE, attributes=[
        Attribute("place", "wifi:x", Grade.VERBATIM, Confidence(0.9), ("e2",), 0)])
    g.upsert_node(a); g.upsert_node(b)
    r = ScriptedReasoner()
    assert causal.propose(a, b, g, r).is_none and r.prompts == []   # no edge: zero spend
    g.upsert_edge(Edge("a", "b", EdgeType.CO_OCCURS, evidence_count=5))
    assert causal.propose(a, b, g, r).is_none and r.prompts == []   # wrong type: zero spend
    g.upsert_edge(Edge("a", "b", EdgeType.PRECEDES, evidence_count=3))
    causal.propose(a, b, g, r)
    assert len(r.prompts) == 1                                      # the ONE guarded call


def test_judge_floors_deep_causal_dreams():
    from tracemem.domain import Attribute, Confidence, Grade, Node, NodeKind
    from tracemem.nodes.combination.judge import judge
    from tracemem.nodes.combination.proposal import Proposal
    from tracemem.nodes.graph_store import MemoryGraphStore
    g = MemoryGraphStore()
    for nid in ("a", "b"):
        g.upsert_node(Node(id=nid, kind=NodeKind.ENTITY, attributes=[
            Attribute("seen_as", nid, Grade.GIST, Confidence(0.8), ("e-%s" % nid,), 0)]))
    j = judge(Proposal(kind="causal", claim_text="a causes b",
                       parent_node_ids=("a", "b"),
                       supported_by=("e-a", "e-b")), g)
    assert not j.accepted and "unearned dream" in j.reason


def test_night_is_idempotent_and_resumable():
    from tracemem.domain import (Attribute, Confidence, Edge, EdgeType, Grade,
                                 Node, NodeKind)
    from tracemem.nodes.graph_store import MemoryGraphStore
    from tracemem.nodes.marination.nightly import run_night
    g = MemoryGraphStore()
    for nid in ("a", "b"):
        g.upsert_node(Node(id=nid, kind=NodeKind.APP, activation=0.9,
                           attributes=[Attribute("app", "X", Grade.VERBATIM,
                                                 Confidence(0.9),
                                                 ("e1", "e2", "e3"), 0)]))
    n1 = run_night(g, None, None, FakeLease(), FakeClock(1000))
    n2 = run_night(g, None, None, FakeLease(), FakeClock(1000))
    assert n1.accepted >= 1 and n2.accepted == 0          # idempotent re-run

    class NoBudget:
        def may_spend(self, n): return False
        def spend(self, n): pass
    g.upsert_edge(Edge("a", "b", EdgeType.CO_OCCURS))
    n3 = run_night(g, None, NoBudget(), FakeLease(), FakeClock(1000))
    assert n3.status == "RESUMABLE"                       # checkpointed, not lost


def test_pruner_never_touches_raw():
    import ast, inspect
    from tracemem.nodes.retention import pruner
    tree = ast.parse(inspect.getsource(pruner))
    imported = {n.module for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom) and n.module}
    assert not any("claim_log" in m or "text" == m.split(".")[-1] for m in imported)
    assert "claim_store" not in inspect.signature(pruner.prune).parameters


# ---- M5: supplier (spec 05) ----
def test_retriever_leaves_store_activations_unchanged():
    from tracemem.domain import Attribute, Confidence, Grade, Node, NodeKind
    from tracemem.nodes.graph_store import MemoryGraphStore
    from tracemem.supplier.cue import parse_cue
    from tracemem.supplier.retriever import retrieve
    from tracemem.supplier.vector_index import VectorIndex
    g = MemoryGraphStore()
    g.upsert_node(Node(id="n1", kind=NodeKind.TOPIC, activation=0.42,
                       attributes=[Attribute("topic", "kiln pottery",
                                             Grade.GIST, Confidence(0.8), ("e1",), 0)]))
    idx = VectorIndex(HashEmbedder()); idx.rebuild_from(g)
    before = g.nodes["n1"].activation
    retrieve(parse_cue("kiln pottery"), g, idx)
    assert g.nodes["n1"].activation == before             # S2: query never self-reinforces


def test_packer_never_exceeds_budget():
    from tracemem.supplier.packer import pack
    from tracemem.supplier.cue import parse_cue
    from tracemem.supplier.retriever import Candidate
    from tracemem.domain import Attribute, Confidence, Grade, Node, NodeKind
    cands = [Candidate(node=Node(id="n%d" % i, kind=NodeKind.TOPIC, attributes=[
        Attribute("topic", "word " * 30, Grade.GIST, Confidence(0.8), ("e1",), 0)]),
        entry_sim=0.9, scratch_activation=0.9) for i in range(50)]
    for q in ("what usually happens?", "what did I read?", 'find "exact words"', "hmm"):
        ctx = pack(cands, parse_cue(q), 100, tokenizer=lambda s: len(s.split()))
        assert ctx.tokens <= 100


def test_cue_parser_defaults_to_hybrid():
    from tracemem.supplier.cue import Precision, parse_cue
    assert parse_cue("hmm", clock=None).precision == Precision.HYBRID


# ---- M6: container (spec 06) ----
def test_trace_header_readable_without_key():
    from tracemem.container.format import pack, peek_header
    blob = pack([make_claim()], key=b"k" * 32, header_meta={"device_id": "mac-1"})
    h = peek_header(blob)                                  # NO key involved
    assert h["count"] == 1 and h["device_id"] == "mac-1"


def test_pillar_boundary_rejects_frames():
    from tracemem.container.pillar_boundary import EgressViolation, assert_egress_safe
    assert_egress_safe([make_claim()])
    with pytest.raises(EgressViolation):
        assert_egress_safe([object()])
    with pytest.raises(EgressViolation):
        assert_egress_safe([make_claim(text="x" * 10 + "A" * 600)])   # smuggled blob


def test_sync_converges_under_shuffle_and_replay(tmp_path):
    import random
    from tracemem.container.keyring import Keyring
    from tracemem.container.sync import SyncPeer
    from tracemem.text.claim_log import ClaimLog
    class KC:
        def __init__(self): self.d = {}
        def get(self, slot): return self.d.get(slot)
        def set(self, slot, value): self.d[slot] = value
    kr = Keyring(KC())
    logs = [ClaimLog(str(tmp_path / ("p%d.db" % i))) for i in (0, 1)]
    claims = [make_claim(text="c%d" % i, t_ms=i * 10, frame="f%d" % i) for i in range(9)]
    random.Random(3).shuffle(claims)
    for i, c in enumerate(claims):
        logs[i % 2].append(c)
    a, b = SyncPeer(kr, logs[0]), SyncPeer(kr, logs[1])
    a.exchange(b); b.exchange(a); a.exchange(b)            # replay is free
    assert a.have() == b.have() == {c.id for c in claims}


# ---- M8: orchestration (spec 08) ----
def test_spotlight_never_holds_two_models():
    from tracemem.app.scheduler import Spotlight, SpotlightBusy
    s = Spotlight(swap_table={"m1": 5.0, "m2": 5.0}, clock=FakeClock(0))
    with s.acquire("m1", "SEE", 1):
        assert s.resident == "m1"
        with pytest.raises(SpotlightBusy):
            with s.acquire("m2", "DREAM", 0):
                pass
    assert s.resident == "m1"                              # released turn, weights stay


def test_budgets_survive_crash(tmp_path):
    from tracemem.app.budgets import TokenLedger
    p = str(tmp_path / "b.db")
    TokenLedger(p, cap=100).spend(5)                       # process "dies" here
    survivor = TokenLedger(p, cap=100)
    assert survivor.spent() == 5 and survivor.may_spend(95) and not survivor.may_spend(96)


# ---- still-stubbed hardware/model adapters (the honest remainder) ----
def test_ocr_adapter_emits_bbox_claims():
    """PROMOTED 2026-07-19: the OCR seam is real (Apple Vision). Reads a
    banked gold frame; skips (never xfails) when no frame is banked."""
    from pathlib import Path

    from tracemem.extraction.ocr_vision import VisionOCR
    frames = sorted(Path(__file__).resolve().parents[2].glob("gold/frames/auto_*.png"))
    if not frames:
        pytest.skip("no banked frames on this machine")
    runs = VisionOCR().read(str(frames[0]))
    assert runs, "a real screen frame yielded zero text"
    assert all(0.0 <= r.x <= 1.0 and 0.0 <= r.y <= 1.0 and r.confidence > 0
               for r in runs), "bboxes must be normalized with confidence"
    with pytest.raises(TypeError):
        VisionOCR().read(None)          # the bridge guard, forever

def test_asr_adapter_transcribes(tmp_path):
    """PROMOTED 2026-07-20: the ear is real (faster-whisper CPU int8).
    Speaks a known sentence via macOS TTS, hears it back with timestamps.
    Founder-voice WER stays gold-gated (F2) — this proves the adapter,
    not the accuracy ceiling."""
    import subprocess

    from tracemem.extraction.asr_local import LocalASR
    clip = tmp_path / "clip.aiff"
    r = subprocess.run(["say", "-o", str(clip),
                        "the kiln firing moved to thursday afternoon"],
                       capture_output=True)
    if r.returncode != 0 or not clip.exists():
        pytest.skip("macOS `say` unavailable in this environment")
    utts = LocalASR().transcribe(clip)
    heard = " ".join(u.text for u in utts).lower()
    # Witnessed 2026-07-20: base model hears "kiln"→"kill" on TTS audio —
    # a real word-error, recorded on the board as F2's justification. The
    # oracle proves the adapter (words + time), not the accuracy ceiling.
    assert "thursday" in heard and "moved" in heard, f"ear misheard: {heard!r}"
    for u in utts:
        assert 0 <= u.t0_ms <= u.t1_ms, "utterance time must be sane"
        for _w, t0, t1 in u.words:
            assert u.t0_ms - 500 <= t0 <= t1 <= u.t1_ms + 500
    with pytest.raises(TypeError):
        LocalASR().transcribe(None)         # the bridge guard, forever


def test_attention_tap_reads_frontmost():
    """PROMOTED 2026-07-20: the focus sense is real (NSWorkspace + window
    server). Reads THIS Mac now; window title may honestly be "" without
    Screen Recording permission — that is a reported hole, not a failure."""
    from tracemem.extraction.attention_mac import attention_span
    span = attention_span()
    if not span.app:
        pytest.skip("no frontmost application (headless session)")
    assert span.pid > 0
    assert 0.0 <= span.cursor_x <= 1.0 and 0.0 <= span.cursor_y <= 1.0
    assert isinstance(span.window, str)     # "" is honest, None is not
    with pytest.raises(TypeError):
        attention_span("now")               # the guard, forever

def test_pipeline_cascade_on_fakes():
    """The full frame fate: static skips pre-GPU; text screen OCRs with bboxes;
    frame deleted; only Claims reach the hub; known app routes with zero VLM."""
    from tracemem.binder.cross_pillar import SensorCtx
    from tracemem.domain import Claim
    from tracemem.extraction.pipeline import PipelineDeps, PipelineState, process

    sharp = [[(x * 37 + y * 91) % 256 for x in range(64)] for y in range(40)]

    class FakeOCR:
        calls = 0
        def read(self, frame):
            FakeOCR.calls += 1
            return [{"text": "kiln firing moved to thursday",
                     "bbox": (0.1, 0.2, 0.7, 0.3), "confidence": 0.95}]

    class StubHub:
        def __init__(self): self.posted = []
        def post_claims(self, claims): self.posted.extend(claims)

    class NeverVLM:
        def classify(self, frame): raise AssertionError("VLM must not run for a known app")

    deleted = []
    hub = StubHub()
    deps = PipelineDeps(ocr=FakeOCR(), hub=hub, vlm=NeverVLM(),
                        deleter=lambda f: deleted.append(id(f)))
    state = PipelineState()
    ctx = {"app": "VS Code", "frame_id": "f-1"}
    sctx = SensorCtx("mac-1", ssid="HomeNet", gps=(48.1, 11.5))

    out = process(sharp, ctx, sctx, deps, state, t_ms=1000)
    assert out.verdict == "read" and out.claims_posted == 1 and not out.used_gpu
    claim = hub.posted[0]
    assert isinstance(claim, Claim)
    assert claim.coord.span.bbox == (0.1, 0.2, 0.7, 0.3)     # P2.1: bbox survives
    assert deleted, "the frame must die at the membrane (X1)"

    # identical second frame: the change gate stops it before OCR (X3)
    before = FakeOCR.calls
    out2 = process([row[:] for row in sharp], ctx, sctx, deps, state, t_ms=2000)
    assert out2.verdict == "skipped" and FakeOCR.calls == before

@X
def test_specialists_load_and_read():
    from tracemem.extraction.specialists.base_vlm import BaseVLMEye
    BaseVLMEye().load()

def test_ollama_retry_discipline():
    """Refused/reset retries with backoff (respawn window, witnessed 2026-07-12);
    timeouts NEVER retry (swap-deadlock class). WO-010 code half; live
    round-trip stays night-gated."""

    # The tag is a REGISTERED one (was a bare "m" until 2026-07-22): the
    # adapter now gates on the model registry in its constructor, so a
    # made-up tag no longer reaches the retry logic at all. Using a real
    # entry also makes this test exercise the path a night run takes.
    import socket
    from tracemem.llm.reasoner_ollama import OllamaReasoner, ReasonerUnavailable

    calls = {"n": 0}
    def flaky(url, payload, timeout_s):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionRefusedError("respawn window")
        return "answer"
    slept = []
    r = OllamaReasoner("gemma3:12b-it-qat", transport=flaky, sleep=slept.append)
    assert r.complete("hi") == "answer" and calls["n"] == 3
    assert slept == [1.0, 3.0]                       # exponential, not hammering

    def wedged(url, payload, timeout_s):
        raise socket.timeout("model wedged")
    r2 = OllamaReasoner("gemma3:12b-it-qat", transport=wedged, sleep=slept.append)
    with pytest.raises((socket.timeout, TimeoutError)):
        r2.complete("hi")
    assert len(slept) == 2                           # zero sleeps added: no retry

    def dead(url, payload, timeout_s):
        raise ConnectionRefusedError("gone")
    r3 = OllamaReasoner("gemma3:12b-it-qat", transport=dead, sleep=lambda s: None)
    with pytest.raises(ReasonerUnavailable):
        r3.complete("hi")


def test_sodium_crypto_round_trips():
    """WO-012: real XChaCha20-Poly1305 (PyCryptodome), header as AAD."""
    from tracemem.container.crypto import AuthFailure, SodiumCrypto
    key = b"k" * 32
    c = SodiumCrypto(key)
    header = b'{"device_id":"mac-1"}'
    nonce, ct = c.encrypt(header, b"the raw claim body")
    assert c.decrypt(header, nonce, ct) == b"the raw claim body"
    with pytest.raises(AuthFailure):
        SodiumCrypto(b"x" * 32).decrypt(header, nonce, ct)          # wrong key
    with pytest.raises(AuthFailure):
        c.decrypt(b'{"device_id":"evil"}', nonce, ct)               # tampered header (AAD)
    with pytest.raises(AuthFailure):
        c.decrypt(header, nonce, ct[:-1] + bytes([ct[-1] ^ 1]))     # flipped body bit
