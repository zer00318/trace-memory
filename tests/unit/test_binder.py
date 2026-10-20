"""Spec 03 oracles — deterministic hippocampus."""
import random

from tests.helpers import make_claim
from tracemem.binder.cross_pillar import SensorCtx, place_key
from tracemem.binder.event_segmenter import boundaries, epoch_index
from tracemem.binder.projection import episode_key, project
from tracemem.domain import Modality


# ---- segmentation (03 §2) ----
def test_boundaries_include_scene_changes_and_forced_cuts():
    b = boundaries([600_000], 0, 5_400_000, max_episode_ms=1_800_000)
    assert 600_000 in b
    assert 2_400_000 in b and 4_200_000 in b     # forced cuts every 30min after t=600s

def test_epoch_index_is_pure_and_order_free():
    b = [1000, 5000]
    assert epoch_index(500, b) == 0
    assert epoch_index(3000, b) == 1
    assert epoch_index(9000, b) == 2


# ---- projection (03 §3): B1/B3/B4 ----
def _claims():
    return [make_claim(text="t%d" % i, t_ms=i * 1000, frame="f%d" % i) for i in range(12)]

def test_projection_order_independent():
    b = [4_000, 8_000]
    claims = _claims()
    shuffled = claims[:]
    random.Random(7).shuffle(shuffled)
    assert project(claims, b) == project(shuffled, b)

def test_projection_idempotent_replay():
    b = [4_000]
    claims = _claims()
    once = project(claims, b)
    twice = project(claims, b, existing=once)
    assert once == twice

def test_late_claim_grows_monotonically():
    b = [4_000]
    claims = _claims()
    eps = project(claims[:-1], b)
    late = claims[-1]
    grown = project([late], b, existing=eps)
    k = episode_key(late, b)
    before = eps.get(k)
    assert set(before.claim_ids if before else ()) <= set(grown[k].claim_ids)
    assert late.id in grown[k].claim_ids


# ---- cross-modal is EMERGENT (03 §4) ----
def test_cross_modal_binding_is_emergent():
    b = [100_000]
    seen = make_claim(text="on screen", t_ms=1000, modality=Modality.SCREEN, frame="fA")
    heard = make_claim(text="HEARD hello", t_ms=2000, modality=Modality.MIC,
                       frame="fB", perceiver="asr", bbox=None)
    eps = project([seen, heard], b)
    assert len(eps) == 1                          # same (place, epoch) → same episode
    ep = next(iter(eps.values()))
    assert seen.id in ep.claim_ids and heard.id in ep.claim_ids


# ---- place_key (03 §5 + ruling A2) ----
def test_ssid_plus_gps_agreeing_binds_high_confidence():
    k, conf = place_key(SensorCtx("mac-1", ssid="HomeNet", gps=(48.1, 11.5)))
    k2, conf2 = place_key(SensorCtx("ios-1", ssid="HomeNet", gps=(48.1, 11.5)))
    assert k == k2 and conf >= 0.9 and conf2 >= 0.9

def test_public_ssid_demotes_to_gps_cell():
    k, conf = place_key(SensorCtx("ios-1", ssid="attwifi", gps=(48.1, 11.5)),
                        public_ssids=frozenset({"attwifi"}))
    assert k.startswith("cell:") and conf < 0.9

def test_unknown_location_never_merges():
    k1, c1 = place_key(SensorCtx("mac-1"))
    k2, c2 = place_key(SensorCtx("ios-1"))
    assert k1 != k2 and c1 == 0.0 and c2 == 0.0

def test_unknown_ssid_without_gps_stays_per_device():
    k, conf = place_key(SensorCtx("ios-1", ssid="RandomCafe"))
    assert k == "dev:ios-1" and conf == 0.0


# ---- B1/B2: no reasoner/LLM import anywhere in the binder package ----
def test_binder_package_imports_no_reasoner():
    import ast
    import pathlib

    import tracemem.binder as binder_pkg

    pkg_dir = pathlib.Path(binder_pkg.__file__).parent
    forbidden = ("tracemem.llm", "tracemem.ports")  # binder needs neither the mouth nor GPU seams
    for py in pkg_dir.glob("*.py"):
        tree = ast.parse(py.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                assert not any(n.startswith(f) for f in forbidden), (
                    "%s imports %s — binder must stay deterministic (B1)" % (py.name, n))


# -- a moment may not span an empty chair (Away witness, second clause) ----
def test_boundaries_break_at_attention_gaps():
    from tracemem.binder.event_segmenter import boundaries_with_gaps
    bounds = boundaries_with_gaps([], [(10_000, 40_000)], 0, 60_000)
    assert 10_000 in bounds and 40_000 in bounds


def test_the_binder_splits_a_moment_across_an_absence(tmp_path):
    """AUDIT FIX 2026-07-21: the binder ignored away/closed-eyes witnesses,
    so claims before lunch and claims after it landed in ONE moment — an
    episode asserting continuous activity across an hour nobody was there."""
    from tracemem.app.witness import WitnessKind, witness
    from tracemem.binder.binder import Binder
    from tracemem.text.claim_log import ClaimLog
    from tests.helpers import make_claim

    log = ClaimLog(str(tmp_path / "gaps.sqlite3"))
    log.append(make_claim("before lunch", t_ms=5_000))
    log.append(witness(WitnessKind.AWAY, t_ms=10_000, span_t2_ms=40_000))
    log.append(make_claim("after lunch", t_ms=50_000, frame="f-2"))

    episodes = Binder(log).run([], 0, 60_000)
    spans = [(e.t_range.start_ms, e.t_range.end_ms) for e in episodes]
    assert len(episodes) >= 2, f"absence did not split the moment: {spans}"
    assert not any(s <= 5_000 and e >= 50_000 for s, e in spans), \
        "one episode spanned the whole absence"


def test_overlapping_away_and_closed_eyes_cut_once(tmp_path):
    """The merge in attention_gaps means one absence is one cut, not two."""
    from tracemem.app.witness import WitnessKind, witness
    from tracemem.capture.presence import attention_gaps
    a = witness(WitnessKind.AWAY, t_ms=10_000, span_t2_ms=40_000)
    b = witness(WitnessKind.CLOSED_EYES, t_ms=12_000, span_t2_ms=38_000)
    assert attention_gaps([a, b]) == [(10_000, 40_000)]


def test_an_absence_starting_before_the_window_still_cuts(tmp_path):
    """RE-AUDIT 2026-07-21: a gap claim is stamped when the absence STARTED,
    so an overnight absence reaching into the morning had its claim outside
    the run window and was invisible — the day's first moment silently began
    inside an absence. Every morning, not an exotic case."""
    from tracemem.app.witness import WitnessKind, witness
    from tracemem.binder.binder import Binder
    from tracemem.text.claim_log import ClaimLog
    from tests.helpers import make_claim

    HOUR = 3_600_000
    log = ClaimLog(str(tmp_path / "overnight.sqlite3"))
    # Stamped at 22:00 the previous evening, running until 08:00.
    log.append(witness(WitnessKind.CLOSED_EYES, t_ms=-2 * HOUR,
                       span_t2_ms=8 * HOUR))
    log.append(make_claim("first thing after waking", t_ms=9 * HOUR))

    bounds = Binder(log).run([], 0, 12 * HOUR)
    assert bounds, "no episodes at all"
    starts = [e.t_range.start_ms for e in bounds]
    assert any(s >= 8 * HOUR for s in starts), \
        f"no moment began after the absence ended: {starts}"


def test_a_clock_jump_cuts_even_though_its_span_is_unknown(tmp_path):
    """The CLOCK_JUMP witness has a zero-length span because the blind
    interval's true length is unknown — but a moment must still not run
    straight through the point where the eyes were shut."""
    from tracemem.app.witness import WitnessKind, witness
    from tracemem.capture.presence import attention_gaps
    j = witness(WitnessKind.CLOCK_JUMP, t_ms=5_000, span_t2_ms=5_000)
    assert attention_gaps([j]) == [(5_000, 5_000)]


def test_the_cached_episode_grid_is_identical_to_rebuilding_it():
    """THE GRID IS THE EPISODE'S IDENTITY.

    `episode_key` is (place, epoch_index(t, bounds)), so the absolute index
    into the boundary list names the episode. The list is anchored at epoch
    zero to keep that name stable across batches — which meant rebuilding
    992,889 entries on every flush (0.10s and ~8MB per call, measured
    2026-08-23, growing by one entry every thirty minutes forever).

    Caching is only safe if it is EXACTLY equal to rebuilding. This asserts
    that over a stream of batches and scene changes, including one arriving
    out of order — the case that shifts indices and silently renames
    episodes.
    """
    from tracemem.app.hub import Hub, Wiring
    from tracemem.binder.event_segmenter import boundaries

    hub = Hub(Wiring(claim_log=None, graph_store=None, vector_index=None,
                     reasoner=None, spotlight=None, clock=None))
    t = 1_787_000_000_000
    for step in range(1, 12):
        now = t + step * 17 * 60_000
        if step == 4:
            hub.post_scene_change(t + 3 * 17 * 60_000)
        if step == 8:
            hub.post_scene_change(t + 2 * 17 * 60_000)      # OUT OF ORDER
        cached = list(hub._bounds_for(now))
        fresh = boundaries(hub._scene_changes, 0, now + 1)
        assert cached == fresh, (
            "cached grid diverged at step %d — episode ids would silently "
            "change" % step)
