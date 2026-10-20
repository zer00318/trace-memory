"""THE GAZE GAUNTLET — traps for the feeding economy's new organs.

Retina: pins can't freeze the ring, budgets are laws, refusal is loud.
Peripheral: change has a WHERE, dwell fires once per stillness, the rate
is honest. Auction: one gaze, one meal, broadcast to all; hungers age
upward; the bias law (drop any stall, nothing notices) holds.
"""
from tracemem.extraction.retina import (Retina, FrameRecord, PIN_TTL_MS)
from tracemem.extraction.peripheral import Peripheral, GRID_X, GRID_Y
from tracemem.extraction.diet import Diet, Bid, DietRegistry
from tracemem.extraction.auction import Auction, CHANGE_VALUE


# ---------------------------------------------------------------- retina --
def _rec(t, nbytes=100, display="main"):
    return FrameRecord(t_ms=t, display=display, frame=f"px@{t}", nbytes=nbytes)


def test_ring_evicts_oldest_first():
    r = Retina(budget_bytes=250)
    r.push(_rec(1)); r.push(_rec(2))
    evicted = r.push(_rec(3))
    assert [e.t_ms for e in evicted] == [1]
    assert r.scene_now().t_ms == 3


def test_pinned_records_survive_eviction():
    r = Retina(budget_bytes=400)              # pin budget = 200 bytes
    r.push(_rec(1)); r.push(_rec(2))
    pin = r.pin(since_ms=0, now_ms=2)
    assert pin is not None and len(pin.records) == 2
    r.push(_rec(3)); r.push(_rec(4))
    evicted = r.push(_rec(5))                 # over budget: something must go
    assert [e.t_ms for e in evicted] == [3]   # oldest UNPINNED dies
    assert {rec.t_ms for rec in pin.records} == {1, 2}   # the pin stays whole


def test_pin_refusal_is_loud_over_budget():
    r = Retina(budget_bytes=200)              # pin budget = 100 bytes
    r.push(_rec(1)); r.push(_rec(2))          # 200 bytes of scene
    assert r.pin(since_ms=0, now_ms=2) is None   # refuses, never silently clips


def test_abandoned_pins_die_on_the_clock():
    r = Retina(budget_bytes=1000)
    r.push(_rec(1))
    pin = r.pin(since_ms=0, now_ms=0)
    assert pin is not None
    assert r.expire_pins(now_ms=PIN_TTL_MS - 1) == []
    assert r.expire_pins(now_ms=PIN_TTL_MS) == [pin.token]
    assert r.bytes_pinned == 0


def test_shrink_gives_memory_back_before_the_mac_swaps():
    r = Retina(budget_bytes=1000)
    for t in range(1, 6):
        r.push(_rec(t))
    evicted = r.shrink(250)
    assert [e.t_ms for e in evicted] == [1, 2, 3]
    assert r.bytes_used <= 250


def test_streams_are_per_display():
    r = Retina(budget_bytes=10_000)
    r.push(_rec(1, display="left")); r.push(_rec(2, display="right"))
    assert r.scene_now("left").t_ms == 1
    assert [x.t_ms for x in r.stream_since(0, "right")] == [2]


# ------------------------------------------------------------ peripheral --
def _grid(h=20, w=32, base=100):
    return [[base] * w for _ in range(h)]


def _bump(frame, region, delta=50):
    h, w = len(frame), len(frame[0])
    bh, bw = h // GRID_Y, w // GRID_X
    gx, gy = region
    out = [row[:] for row in frame]
    for y in range(gy * bh, (gy + 1) * bh):
        for x in range(gx * bw, (gx + 1) * bw):
            out[y][x] += delta
    return out


def test_change_has_a_where():
    p = Peripheral()
    p.observe(_grid(), t_ms=0)                       # first sight
    events = p.observe(_bump(_grid(), (3, 4)), t_ms=100)
    assert [e.region for e in events if e.kind == "change"] == [(3, 4)]


def test_dwell_fires_once_per_stillness_period():
    p = Peripheral(dwell_ms=1000)
    p.observe(_grid(), t_ms=0)
    still = [e for e in p.observe(_grid(), t_ms=1000) if e.kind == "dwell"]
    assert len(still) == GRID_X * GRID_Y             # every region earned it
    again = [e for e in p.observe(_grid(), t_ms=2000) if e.kind == "dwell"]
    assert again == []                               # never repeats itself


def test_change_rearms_dwell():
    p = Peripheral(dwell_ms=1000)
    p.observe(_grid(), t_ms=0)
    p.observe(_grid(), t_ms=1000)                    # dwell fired
    reborn = _bump(_grid(), (0, 0))
    p.observe(reborn, t_ms=1100)                     # region reborn (change)
    events = p.observe(reborn, t_ms=2200)            # then HELD still
    assert ("dwell", (0, 0)) in [(e.kind, e.region) for e in events]


def test_honest_rate_is_counted_not_estimated():
    p = Peripheral()
    p.mark_offered(4)
    p.observe(_grid(), t_ms=0)
    p.observe(_grid(), t_ms=16)
    rate = p.honest_rate()
    assert rate == {"offered": 4, "watched": 2, "rate": 0.5}


# --------------------------------------------------------------- auction --
class _Ev:
    def __init__(self, kind, region):
        self.kind, self.region = kind, region


def _auction(*diets):
    reg = DietRegistry()
    for d in diets:
        reg.register(d)
    calls = []
    a = Auction(reg, prepare=lambda region: calls.append(region) or f"meal@{region}")
    return a, reg, calls


def test_one_gaze_one_meal_broadcast_to_all():
    a, reg, calls = _auction(Diet("ocr"), Diet("study"),
                             Diet("continuity", continuous=True))
    a.on_events([_Ev("change", (1, 1))])
    gaze = a.beat(now_ms=0)
    assert gaze.region == (1, 1)
    assert calls == [(1, 1)]                         # prepared ONCE
    assert set(gaze.served) == {"ocr", "study"}      # continuous taps upstream
    assert a.beat(now_ms=16) is None                 # world must speak again


def test_winner_take_all_is_serial():
    a, _, calls = _auction(Diet("ocr"))
    a.on_events([_Ev("change", (0, 0)), _Ev("change", (2, 2)),
                 _Ev("change", (2, 2))])
    gaze = a.beat(now_ms=0)
    assert gaze.region == (2, 2)                     # loudest wins
    assert len(calls) == 1                           # one fovea, one meal


def test_hungers_age_upward_and_beat_fresh_noise():
    a, _, _ = _auction(Diet("study"))
    a.post_bid(Bid(bidder="node:jar", value=0.5, born_ms=0, region=(5, 5)))
    a.on_events([_Ev("change", (0, 0))])             # fresh noise: 1.0
    early = a.priority_map(now_ms=0)
    assert early[(0, 0)] > early[(5, 5)]             # noise wins young...
    gaze = a.beat(now_ms=20_000)                     # ...but hunger grows
    assert gaze.region == (5, 5)
    assert gaze.satisfied[0].bidder == "node:jar"    # closed loop


def test_satisfied_bids_are_retired():
    a, _, _ = _auction(Diet("study"))
    a.post_bid(Bid(bidder="node:jar", value=2.0, born_ms=0, region=(5, 5)))
    a.beat(now_ms=0)
    assert a.beat(now_ms=16) is None                 # hunger fed, map quiet


def test_bias_law_any_stall_droppable_nothing_notices():
    a, reg, _ = _auction(Diet("ocr"), Diet("chess-stall"))
    assert reg.drop("chess-stall") is True
    assert reg.drop("chess-stall") is False          # double-drop never raises
    a.on_events([_Ev("change", (1, 1))])
    gaze = a.beat(now_ms=0)
    assert gaze.served == ("ocr",)                   # the system didn't notice
