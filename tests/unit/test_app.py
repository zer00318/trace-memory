"""App layer: ledgers, the two-scarcity Spotlight, hub ingest+ask, wiring."""
from __future__ import annotations

import pytest

from tests.fakes import ContextAnswerReasoner, FakeClock, HashEmbedder
from tests.helpers import make_claim
from tracemem.app.budgets import InterruptionLedger, TokenLedger
from tracemem.app.scheduler import HYSTERESIS_MS, Spotlight, SpotlightBusy
from tracemem.app.wiring import build

MS_DAY = 86_400_000


def test_interruption_cap_queue_and_staleness(tmp_path):
    led = InterruptionLedger(str(tmp_path / "i.db"), cap_per_day=3)
    for i in range(3):
        assert led.may_ask(1000 + i)
        led.record_ask(1000 + i)
    assert not led.may_ask(2000)                       # 4th today: denied
    assert led.may_ask(MS_DAY + 1000)                  # tomorrow: fresh cap
    led.enqueue("q1", 1000)
    led.enqueue("q2", MS_DAY)
    assert led.drain_queue(MS_DAY + 1) == ["q1", "q2"]
    # stale expiry at t=3.5d: q1 (age 3.5d) expires, q2 (age 2.5d) survives
    remaining = led.drain_queue(int(3.5 * MS_DAY))
    assert remaining == ["q2"]


def test_spotlight_capture_blocks_night():
    s = Spotlight({}, FakeClock(0))
    s.set_capture(True)
    with pytest.raises(SpotlightBusy):
        with s.acquire("m", role="DREAM", priority=0):
            pass
    s.set_capture(False)
    with s.acquire("m", role="DREAM", priority=0):
        pass                                           # capture off: night may run


def test_spotlight_hysteresis_vs_queue_jump():
    clock = FakeClock(0)
    s = Spotlight({"a": 5.0, "b": 5.0}, clock)
    with s.acquire("a", role="SEE", priority=0):
        pass
    with pytest.raises(SpotlightBusy):                 # background flip inside window
        with s.acquire("b", role="DREAM", priority=0):
            pass
    with s.acquire("b", role="ANSWER", priority=10):   # user turn jumps the queue
        assert s.resident == "b"
    clock.advance(HYSTERESIS_MS + 1)
    with s.acquire("a", role="DREAM", priority=0):     # window passed: swap allowed
        assert s.resident == "a"


def test_spotlight_preempt_signal():
    s = Spotlight({}, FakeClock(0))
    with s.acquire("m", role="DREAM", priority=0):
        assert not s.preempted()
        s.signal_query()
        assert s.preempted()                           # cooperative: dream must yield
    assert not s.preempted()                           # cleared on release


def test_wiring_builds_and_hub_answers():
    clock = FakeClock(0)
    hub = build({"embedder": HashEmbedder(), "reasoner": ContextAnswerReasoner(),
                 "clock": clock})
    claims = [
        make_claim(text="chatting with Sam about the quarterly report",
                   t_ms=1_000, frame="f-1"),
        make_claim(text="Sam sent the quarterly report draft",
                   t_ms=61_000, frame="f-2"),
        make_claim(text="editing the quarterly report numbers",
                   t_ms=121_000, frame="f-3"),
    ]
    hub.post_scene_change(200_000)
    result = hub.post_claims(claims)
    assert result["appended"] == 3 and result["episodes"] >= 1
    assert hub.w.graph_store.nodes                     # birth ran: entities exist
    clock.advance(1_000)
    ans = hub.ask("what was the quarterly report about?")
    assert not ans.abstained
    assert "quarterly" in ans.text.lower()
    assert hub.health()["ingest"][0] == "OK"
    clock.advance(10 ** 9)
    assert hub.health()["ingest"][0] == "DEAD"         # silence is visible


def test_hub_abstains_without_context():
    hub = build({"embedder": HashEmbedder(), "reasoner": ContextAnswerReasoner(),
                 "clock": FakeClock(0)})
    assert hub.ask("who phoned me?").abstained


def test_wiring_refuses_silent_defaults():
    with pytest.raises(ValueError):
        build({"reasoner": ContextAnswerReasoner()})
    with pytest.raises(ValueError):
        build({"embedder": HashEmbedder()})


def test_the_never_lie_guard_downgrades_an_unverified_answer_in_the_product():
    """The guard is wired into hub.ask(), not just the eval tool. A model
    that ANSWERS with a citation but whose verification pass says the block
    does not support it must yield 'not in memory' — the product would
    rather stay silent than hand back a cited fabrication.

    A minimal reasoner double: it answers every question by echoing the first
    memory block with its id (a plausible-looking cited answer), and its
    verification reply is 'no'. If the guard were not wired, the fabrication
    would be returned; with it, the answer is downgraded.
    """
    from tracemem.app.wiring import build
    from tests.fakes import FakeClock, HashEmbedder
    from tests.helpers import make_claim

    class LyingReasoner:
        def complete(self, prompt):
            if "fact-checker" in prompt:
                return "no"                     # the block does NOT support it
            body = prompt.split("QUESTION:")[0]
            for line in body.splitlines():
                line = line.strip()
                if line.startswith("(") and ")" in line:
                    src = line.split("|")[-1].split(")")[0].split(",")[0].strip()
                    text = line.split(")", 1)[1].strip()
                    return "%s [%s]" % (text, src)   # a cited answer
            return "not in memory"

    hub = build({"embedder": HashEmbedder(), "reasoner": LyingReasoner(),
                 "clock": FakeClock(0)})
    hub.post_scene_change(200_000)
    hub.post_claims([
        make_claim(text="editing the quarterly report numbers", t_ms=1_000, frame="f-1"),
        make_claim(text="Sam sent the quarterly report", t_ms=61_000, frame="f-2"),
    ])

    ans = hub.ask("what is my bank balance?")
    assert ans.abstained is True, "the guard let an unsupported answer through"
    assert ans.text == "not in memory"


def test_the_guard_can_be_turned_off_and_the_answer_flows():
    """The opt-out is explicit and documented at the call site. With it off,
    the same cited answer is returned unguarded — proving the downgrade above
    was the guard's doing, not something else."""
    from tracemem.app.wiring import build
    from tests.fakes import FakeClock, HashEmbedder
    from tests.helpers import make_claim

    class LyingReasoner:
        def complete(self, prompt):
            if "fact-checker" in prompt:
                return "no"
            body = prompt.split("QUESTION:")[0]
            for line in body.splitlines():
                line = line.strip()
                if line.startswith("(") and ")" in line:
                    src = line.split("|")[-1].split(")")[0].split(",")[0].strip()
                    text = line.split(")", 1)[1].strip()
                    return "%s [%s]" % (text, src)
            return "not in memory"

    hub = build({"embedder": HashEmbedder(), "reasoner": LyingReasoner(),
                 "clock": FakeClock(0), "verify_answers": False})
    hub.post_scene_change(200_000)
    hub.post_claims([
        make_claim(text="editing the quarterly report numbers", t_ms=1_000, frame="f-1"),
    ])
    ans = hub.ask("what is my bank balance?")
    assert ans.abstained is False            # unguarded, the fabrication flows
