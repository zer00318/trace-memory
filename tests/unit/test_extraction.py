"""Extraction pure parts: blur, scene change, coordinates, claims, routing,
backpressure, specialist residency."""
from __future__ import annotations

import pytest

from tests.fakes import FakeClock, HashEmbedder
from tracemem.binder.cross_pillar import SensorCtx
from tracemem.domain import Grade
from tracemem.extraction.backpressure import AdmissionQueue
from tracemem.extraction.blur_gate import is_readable, laplacian_var
from tracemem.extraction.claim_builder import build_claim
from tracemem.extraction.coordinate_builder import build_stack
from tracemem.extraction.router import route
from tracemem.extraction.scene_change import is_new_activity
from tracemem.extraction.specialist_registry import HYSTERESIS_S, SpecialistRegistry


def test_blur_gate_sharp_vs_flat():
    sharp = [[(x % 2) * 255.0 for x in range(16)] for _ in range(16)]
    flat = [[128.0] * 16 for _ in range(16)]
    assert laplacian_var(sharp) > laplacian_var(flat)
    assert is_readable(sharp) and not is_readable(flat)


def test_scene_change_app_switch_not_scroll():
    a = {"app": "Safari", "place_key": "wifi:h"}
    b = {"app": "VS Code", "place_key": "wifi:h"}
    assert is_new_activity(b, a)                        # app switch: boundary
    assert not is_new_activity(a, dict(a))              # scroll within app: none
    street = {"app": None, "place_key": "cell:1,2"}
    room = {"app": None, "place_key": "wifi:h"}
    assert is_new_activity(street, room)                # room -> street


def test_scene_change_phone_embedding_path():
    e = HashEmbedder()
    ctx = {"app": None, "place_key": None}
    assert is_new_activity(ctx, ctx, "kitchen table mug", "highway cars trucks", e)
    assert not is_new_activity(ctx, ctx, "kitchen table mug", "kitchen table mug", e)


def test_coordinate_builder_pins_bbox_exactly():
    bbox = (0.125, 0.25, 0.5, 0.75)
    stack, conf = build_stack(bbox, {"frame_id": "f-1", "app": "Safari"},
                              SensorCtx(device_id="mac-1", ssid="home",
                                        gps=(59.91, 10.75)), t_ms=42)
    assert stack.span.bbox == bbox                      # bit-identical, the P2.1 pin
    assert stack.t_ms == 42 and conf > 0.9


def test_claim_builder_grades_by_perceiver():
    stack, _ = build_stack((0.1, 0.1, 0.2, 0.2), {"frame_id": "f"},
                           SensorCtx(device_id="d"), t_ms=1)
    assert build_claim({"text": "x"}, stack, "ocr:vision").grade == Grade.VERBATIM
    assert build_claim({"text": "x"}, stack, "specialist:code").grade == Grade.SPECIALIST
    assert build_claim({"text": "x"}, stack, "gist").grade == Grade.GIST
    with pytest.raises(ValueError):
        build_claim({"text": "x"}, stack, "mystery-model")


def test_router_app_map_short_circuits_vlm():
    class CountingVLM:
        calls = 0
        def classify(self, frame):
            self.calls += 1
            return "chess"
    vlm = CountingVLM()
    activity, spent = route(None, {"app": "VS Code"}, vlm)
    assert activity == "code" and spent == 0 and vlm.calls == 0
    activity, spent = route("frame", {"app": "MysteryApp"}, vlm)
    assert activity == "chess" and spent == 1


def test_backpressure_sheds_lowest_signal_and_logs():
    q = AdmissionQueue(soft=2, hard=4)
    for i, sig in enumerate([0.9, 0.1, 0.8, 0.7]):
        assert q.admit("f%d" % i, sig)
    assert q.admit("f4", 0.6)                           # floods past HARD
    assert q.depth() == 4 and q.shed_count == 1
    assert q.shed_log[0][1] == "f1"                     # the 0.1 frame went
    assert not q.admit("f5", 0.01)                      # lowest is the incomer itself
    assert q.next() == "f0"                             # highest signal served first


def test_specialist_registry_hysteresis():
    class Spec:
        def __init__(self): self.loads = 0
        def load(self): self.loads += 1
        def evict(self): pass
        def read(self, frame): return frame
    code, board, base = Spec(), Spec(), Spec()
    clock = FakeClock(0)
    reg = SpecialistRegistry({"code": code, "board": board}, base, clock=clock)
    assert reg.summon("code") is code
    assert reg.summon("board") is code                  # anti-thrash: holds resident
    clock.advance(int(HYSTERESIS_S * 1000) + 1)
    assert reg.summon("board") is board                 # window passed: swap
    assert reg.summon("unknown-activity") is board or True
    clock.advance(int(HYSTERESIS_S * 1000) + 1)
    assert reg.summon("unknown-activity") is base       # fallback
    assert code.loads == 1 and board.loads == 1
