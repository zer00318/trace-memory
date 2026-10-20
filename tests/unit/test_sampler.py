"""ORACLE for the sampler — the wiring half the audit found missing.

Scheduling is driven by a fake clock, so these assertions are exact rather
than timing-dependent.
"""
from tracemem.capture.sampler import Channel, MachineSampler


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def sampler_with(channels, clock=None, **kw):
    clock = clock or FakeClock()
    s = MachineSampler(clock=clock, wall_clock=lambda: clock() ,
                       channels=channels, **kw)
    return s, clock


def test_each_channel_is_polled_at_its_own_rate():
    """The reason it is one loop and not five threads."""
    fast_reads, slow_reads = [], []
    channels = [
        Channel("fast", 10.0, lambda: (fast_reads.append(1), {"v": len(fast_reads)})[1]),
        Channel("slow", 30.0, lambda: (slow_reads.append(1), {"v": len(slow_reads)})[1]),
    ]
    s, clock = sampler_with(channels)
    for _ in range(7):                       # 0,10,20,...,60 seconds
        s.tick()
        clock.advance(10.0)
    assert len(fast_reads) == 7
    assert len(slow_reads) == 3              # 0s, 30s, 60s


def test_a_channel_not_yet_due_is_skipped():
    reads = []
    s, clock = sampler_with([Channel("c", 30.0, lambda: (reads.append(1), {"v": 1})[1])])
    s.tick()
    clock.advance(5.0)
    s.tick()
    assert len(reads) == 1


def test_only_changes_land_as_events():
    s, clock = sampler_with([Channel("power", 1.0, lambda: {"on_battery": False})])
    first = s.tick()
    assert len(first) == 1 and first[0].first is True
    for _ in range(5):
        clock.advance(1.0)
        assert s.tick() == []


def test_a_real_change_lands():
    state = {"on_battery": False}
    s, clock = sampler_with([Channel("power", 1.0, lambda: dict(state))])
    s.tick()
    state["on_battery"] = True
    clock.advance(1.0)
    events = s.tick()
    assert len(events) == 1 and events[0].value is True


def test_one_dying_channel_does_not_stop_the_others():
    """A transient Quartz error at 3am must not silently end all sensing."""
    def boom():
        raise RuntimeError("Quartz went away")

    good = []
    channels = [Channel("bad", 1.0, boom),
                Channel("good", 1.0, lambda: (good.append(1), {"v": len(good)})[1])]
    s, clock = sampler_with(channels)
    for _ in range(3):
        s.tick()
        clock.advance(1.0)
    assert len(good) == 3
    assert s.stats.errors["bad"] == 3
    assert channels[0].last_error and "Quartz" in channels[0].last_error


def test_a_failed_channel_is_retried_not_benched():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("nope")
        return {"v": 1}

    s, clock = sampler_with([Channel("flaky", 1.0, flaky)])
    for _ in range(4):
        s.tick()
        clock.advance(1.0)
    assert len(calls) == 4
    assert s.stats.polls.get("flaky") == 2


def test_a_channel_reopens_fresh_after_an_outage():
    """No transition may be claimed across an interval nobody witnessed."""
    mode = {"fail": False, "v": "a"}

    def read():
        if mode["fail"]:
            raise RuntimeError("dark")
        return {"v": mode["v"]}

    s, clock = sampler_with([Channel("c", 1.0, read)])
    s.tick()                                  # first reading: v=a
    mode["fail"] = True
    clock.advance(1.0)
    s.tick()                                  # dark
    mode["fail"], mode["v"] = False, "b"
    clock.advance(1.0)
    events = s.tick()
    assert events and events[0].first is True, "claimed a change across a blind gap"


def test_presence_witnesses_reach_the_sink():
    """The whole point of wiring: an absence becomes a claim somewhere."""
    caught = []
    idle = {"s": 0.0}
    clock = FakeClock()
    s = MachineSampler(clock=clock, wall_clock=lambda: clock(),
                       on_witness=caught.append, channels=[])
    s.channels = [Channel("presence", 10.0, s._read_presence)]

    import tracemem.capture.sampler as mod
    original = mod.read_idle_seconds
    mod.read_idle_seconds = lambda: idle["s"]
    try:
        s.tick()                              # present
        idle["s"] = 600.0
        clock.advance(600.0)
        s.tick()                              # away (backdated)
        idle["s"] = 0.0
        clock.advance(60.0)
        s.tick()                              # back
    finally:
        mod.read_idle_seconds = original

    assert len(caught) == 1
    assert caught[0].structured["witness"] == "away"
    assert s.stats.witnesses == 1


def test_events_reach_the_event_sink():
    caught = []
    s, clock = sampler_with([Channel("power", 1.0, lambda: {"on_battery": False})],
                            on_event=caught.append)
    s.tick()
    assert len(caught) == 1


def test_stats_count_what_actually_happened():
    s, clock = sampler_with([Channel("c", 1.0, lambda: {"v": 1})])
    for _ in range(3):
        s.tick()
        clock.advance(1.0)
    assert s.stats.ticks == 3 and s.stats.polls["c"] == 3 and s.stats.events == 1


def test_the_default_channels_are_the_real_senses():
    names = {c.name for c in MachineSampler().channels}
    assert names == {"power", "network", "presence"}


def test_a_live_tick_reads_this_machine():
    """One real pass over the real senses — no fakes anywhere."""
    s = MachineSampler()
    events = s.tick()
    assert s.stats.polls.get("power") == 1
    assert s.stream.value("power", "source"), "no real power source read"
    assert s.stream.value("presence", "state") in ("present", "away")
    assert all(e.first for e in events), "first pass should be openings"


# -- RE-AUDIT 2026-07-21: the sleep lie was still LIVE through this seam ---
def test_the_sampler_does_not_claim_an_absence_across_a_sleep():
    """THE regression the re-audit caught: the fix lived in presence.py and
    the sampler bypassed it with `lambda: True`, so replaying an overnight
    sleep through the only existing wiring reproduced the lie in full —
    '[witness:away] screen lit, nobody home for 29100s'."""
    caught = []
    idle = {"s": 5.0}
    clock = FakeClock()
    s = MachineSampler(clock=clock, wall_clock=lambda: clock(),
                       on_witness=caught.append, channels=[])
    s.channels = [Channel("presence", 10.0, s._read_presence)]

    import tracemem.capture.sampler as mod
    original = mod.read_idle_seconds
    mod.read_idle_seconds = lambda: idle["s"]
    try:
        s.tick()                                  # present, awake
        s.eyelid_event("sleep", int(clock() * 1000))
        clock.advance(8 * 3600)                   # an overnight sleep
        idle["s"] = 8 * 3600.0                    # the idle clock counted through
        s.tick()
        s.eyelid_event("wake", int(clock() * 1000))
        idle["s"] = 0.0
        clock.advance(10.0)
        s.tick()                                  # first poll after waking
    finally:
        mod.read_idle_seconds = original

    away = [c for c in caught if c.structured["witness"] == "away"]
    assert not away, f"claimed an absence across a sleep: {[c.text for c in away]}"


def test_the_sampler_owns_a_real_eyelid_state():
    """`eyes_open` must be wired to something that can actually say no."""
    s = MachineSampler()
    assert s.eyelids is not None
    assert s._eyes_open() is True
    s.eyelid_event("sleep", 1000)
    assert s._eyes_open() is False, "the sampler cannot see that the eyes shut"


def test_an_eyelid_pair_reaches_the_witness_sink():
    caught = []
    s = MachineSampler(on_witness=caught.append)
    s.eyelid_event("locked", 10_000)
    s.eyelid_event("unlocked", 70_000)
    assert len(caught) == 1
    assert caught[0].structured["witness"] == "closed-eyes"
    assert s.stats.witnesses == 1


def test_presence_and_the_eyelids_read_one_state_not_two():
    """Two eyelid states that can disagree is the bug this routing avoids."""
    s = MachineSampler()
    s.eyelid_event("locked", 1000)
    assert s.eyelids.eyes_open is False and s._eyes_open() is False
