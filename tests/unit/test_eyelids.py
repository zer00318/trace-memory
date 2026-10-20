"""ORACLE for Lock event tap / Sleep-wake taps / Closed-eyes witness /
Wake re-arm (01_world).

The live half (a real lock, a real lid close) is hardware-gated and NOT
claimed here. What is proven is the pairing arithmetic — including the
nesting case that would otherwise double-count every night quietly.
"""
import pytest

from tracemem.app.witness import WitnessKind
from tracemem.capture.eyelids import Eyelids

MIN = 60_000


def test_lock_then_unlock_yields_one_claim_covering_the_interval():
    """The atom's PROOF, verbatim: lock@t1 + unlock@t2 → one claim [t1,t2]."""
    e = Eyelids()
    e.observe("locked", 10 * MIN)
    _edge, claim = e.observe("unlocked", 40 * MIN)
    assert claim is not None
    assert claim.structured["witness"] == WitnessKind.CLOSED_EYES.value
    assert claim.coord.t_ms == 10 * MIN
    assert claim.structured["t2_ms"] == 40 * MIN


def test_sleep_then_wake_pairs_the_same_way():
    e = Eyelids()
    e.observe("sleep", 1 * MIN)
    _edge, claim = e.observe("wake", 9 * MIN)
    assert claim.coord.t_ms == 1 * MIN and claim.structured["t2_ms"] == 9 * MIN


def test_a_lid_close_does_not_double_count():
    """THE nesting bug: lock+sleep on the way down, wake+unlock on the way
    up is ONE absence. Two claims here would double-count every night."""
    e = Eyelids()
    claims = []
    for cause, t in (("locked", 10 * MIN), ("sleep", 10 * MIN + 500),
                     ("wake", 60 * MIN), ("unlocked", 61 * MIN)):
        _edge, c = e.observe(cause, t)
        if c is not None:
            claims.append(c)

    assert len(claims) == 1, "one absence produced more than one claim"
    assert claims[0].coord.t_ms == 10 * MIN          # the FIRST closing cause
    assert claims[0].structured["t2_ms"] == 61 * MIN  # the LAST opener
    assert claims[0].structured["causes"] == ["locked", "sleep"]


def test_eyes_stay_shut_while_any_cause_remains():
    e = Eyelids()
    e.observe("locked", 0)
    e.observe("sleep", 100)
    _edge, claim = e.observe("wake", 200)
    assert claim is None, "eyes reopened while still locked"
    assert e.eyes_open is False


def test_duplicate_notifications_do_not_deepen_the_stack():
    """macOS re-delivers notifications; a naive counter would never reach
    zero and the eyes would stay shut forever."""
    e = Eyelids()
    e.observe("locked", 0)
    e.observe("locked", 10)
    _edge, claim = e.observe("unlocked", 100)
    assert claim is not None and e.eyes_open is True


def test_an_opener_with_nothing_open_witnesses_nothing():
    e = Eyelids()
    _edge, claim = e.observe("unlocked", 500)
    assert claim is None and e.eyes_open is True


def test_backwards_clock_witnesses_the_jump_not_silence():
    """AUDIT FIX 2026-07-21: this test used to assert the interval was
    swallowed. Swallowing it left a real blind period in the store with
    NOTHING said about it — an unexplained hole, which is the failure mode
    the witness vocabulary exists to prevent. NTP correcting on wake is a
    common cause and a lid close is exactly where it lands, so the jump is
    itself the news."""
    e = Eyelids()
    e.observe("locked", 10 * MIN)
    _edge, claim = e.observe("unlocked", 5 * MIN)
    assert claim is not None
    assert claim.structured["witness"] == WitnessKind.CLOCK_JUMP.value
    assert claim.structured["backwards_ms"] == 5 * MIN
    assert claim.structured["causes"] == ["locked"]


def test_the_clock_jump_witness_does_not_invent_an_interval_length():
    """We do not know how long the eyes were shut — so we must not say."""
    e = Eyelids()
    e.observe("sleep", 10 * MIN)
    _edge, claim = e.observe("wake", 1 * MIN)
    assert claim.structured["t2_ms"] == claim.coord.t_ms   # zero-length: unknown


def test_wake_re_arm_uses_the_shared_hook():
    """One re-arm, many causes — this module emits the edge and calls the
    daemon's existing path rather than owning a second restart."""
    fired = []
    e = Eyelids(on_reopen=fired.append)
    e.observe("sleep", 0)
    e.observe("wake", 5 * MIN)
    assert fired == [5 * MIN]


def test_re_arm_does_not_fire_while_still_shut():
    fired = []
    e = Eyelids(on_reopen=fired.append)
    e.observe("locked", 0)
    e.observe("sleep", 10)
    e.observe("wake", 100)
    assert fired == []


def test_kill_mid_sleep_closes_the_interval_at_boot():
    """No opener ever arrives when the Mac is killed asleep. Leaving the
    interval open would make one crash swallow every later gap."""
    e = Eyelids()
    e.observe("sleep", 1 * MIN)
    claim = e.force_open(120 * MIN)
    assert claim.coord.t_ms == 1 * MIN
    assert claim.structured["t2_ms"] == 120 * MIN
    assert e.eyes_open is True


def test_force_open_with_open_eyes_is_a_no_op():
    assert Eyelids().force_open(1000) is None


def test_unknown_cause_is_loud():
    with pytest.raises(ValueError):
        Eyelids().observe("hibernated", 0)


def test_edges_record_the_depth():
    e = Eyelids()
    assert e.observe("locked", 0)[0].depth == 1
    assert e.observe("sleep", 1)[0].depth == 2
    assert e.observe("wake", 2)[0].depth == 1
    assert e.observe("unlocked", 3)[0].depth == 0


def test_claim_is_a_wellformed_memory_witness():
    e = Eyelids()
    e.observe("locked", 0)
    _edge, claim = e.observe("unlocked", 60_000)
    assert claim.scope == "memory"
    assert claim.text.startswith("[witness:closed-eyes]")


def test_a_second_absence_starts_clean():
    """Causes from the first absence must not leak into the second."""
    e = Eyelids()
    e.observe("locked", 0)
    e.observe("unlocked", 100)
    e.observe("sleep", 200)
    _edge, claim = e.observe("wake", 300)
    assert claim.structured["causes"] == ["sleep"]
