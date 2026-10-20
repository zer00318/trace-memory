"""ORACLE for Idle seconds + Away witness (01_world).

The atom's PROOF: away@t1 + return@t2 → one claim covering [t1, t2].
These tests also pin the backdating correction, which is the part that
would fail silently: an away claim whose start is one threshold too late
still looks like a perfectly good claim.
"""
from tracemem.app.witness import WitnessKind
from tracemem.capture.presence import (
    AWAY, PRESENT, UNKNOWN, PresenceSense, away_spans, presence_from_idle,
)

MIN = 60_000


def test_the_pure_rule():
    assert presence_from_idle(10.0, 300.0) == PRESENT
    assert presence_from_idle(300.0, 300.0) == AWAY
    assert presence_from_idle(900.0, 300.0) == AWAY


def test_unknown_idle_is_a_hole_never_present():
    """A failed read must not assert a human was at the machine."""
    assert presence_from_idle(None, 300.0) == UNKNOWN


def test_away_then_return_yields_one_claim_covering_the_interval():
    """The atom's PROOF, verbatim."""
    s = PresenceSense(threshold_s=300.0)
    s.observe(0.0, 10 * MIN)                       # present at 00:10
    s.observe(300.0, 15 * MIN)                     # threshold tripped at 00:15
    _edge, claim = s.observe_and_witness(0.0, 20 * MIN)   # back at 00:20

    assert claim is not None
    assert claim.structured["witness"] == WitnessKind.AWAY.value
    # Backdated: 300s of idle had already run when the poll saw it, so the
    # departure was 00:10, not 00:15.
    assert claim.coord.t_ms == 10 * MIN
    assert claim.structured["t2_ms"] == 20 * MIN


def test_the_backdating_is_the_whole_point():
    """Without it, every away claim starts one threshold late — a wrong
    number that looks exactly like a right one."""
    s = PresenceSense(threshold_s=300.0)
    s.observe(0.0, 0)
    s.observe(420.0, 20 * MIN)          # 7 minutes idle seen at 00:20
    _e, claim = s.observe_and_witness(0.0, 25 * MIN)
    assert claim.coord.t_ms == 20 * MIN - 420_000   # left at 00:13


def test_stillness_emits_no_edges():
    s = PresenceSense(threshold_s=300.0)
    s.observe(1.0, 1000)
    assert s.observe(2.0, 2000) is None
    assert s.observe(3.0, 3000) is None
    assert len(s.edges) == 1


def test_returning_without_having_left_witnesses_nothing():
    s = PresenceSense(threshold_s=300.0)
    s.observe(0.0, 1000)
    assert s.close_away_interval(2000) is None


def test_a_dark_sense_does_not_close_an_open_absence():
    """Quartz died mid-absence: we cannot say when they came back, so we
    do not say it."""
    s = PresenceSense(threshold_s=300.0)
    s.observe(0.0, 0)
    s.observe(600.0, 10 * MIN)              # away
    s.observe(None, 12 * MIN)               # sense goes dark
    _e, claim = s.observe_and_witness(0.0, 15 * MIN)
    assert claim is None


def test_backwards_clock_witnesses_nothing():
    """Departure backdates to 00:10; a "return" stamped 00:05 means the
    clock moved backwards under us. Witness nothing rather than a claim
    that ends before it starts."""
    s = PresenceSense(threshold_s=300.0)
    s.observe(0.0, 0)
    s.observe(600.0, 20 * MIN)              # 10 min idle seen at 00:20 → left 00:10
    assert s.close_away_interval(5 * MIN) is None


def test_away_witness_is_a_wellformed_memory_claim():
    s = PresenceSense(threshold_s=300.0)
    s.observe(0.0, 0)
    s.observe(300.0, 10 * MIN)
    _e, claim = s.observe_and_witness(0.0, 20 * MIN)
    assert claim.scope == "memory"
    assert claim.text.startswith("[witness:away]")
    assert claim.structured["threshold_s"] == 300.0


def test_away_spans_are_readable_by_the_binder():
    """The shape the binder consumes to refuse spanning an empty chair."""
    s = PresenceSense(threshold_s=300.0)
    s.observe(0.0, 0)
    s.observe(300.0, 10 * MIN)
    _e, claim = s.observe_and_witness(0.0, 20 * MIN)
    assert away_spans([claim]) == [(5 * MIN, 20 * MIN)]


# -- the sleep lie the audit found -----------------------------------------
def test_a_sleeping_mac_does_not_claim_the_screen_was_lit():
    """AUDIT FIX 2026-07-21: CGEventSourceSecondsSinceLastEventType keeps
    counting through sleep, so the first poll after an overnight sleep read
    ~8h idle, backdated past the sleep, and emitted 'screen lit, nobody home
    for 28810s'. The screen was off and nobody was home because the machine
    was shut. A durable claim asserting something that never happened."""
    s = PresenceSense(threshold_s=300.0)
    s.observe(5.0, 0)                                   # present, awake
    s.observe(8 * 3600.0, 8 * 3600 * 1000, eyes_open=False)   # asleep
    _edge, claim = s.observe_and_witness(0.0, 8 * 3600 * 1000 + 10_000,
                                         eyes_open=True)
    assert claim is None, "claimed an absence across a sleep"


def test_eyes_shut_reads_unknown_not_away():
    """The closed-eyes witness owns that interval; presence must not."""
    s = PresenceSense(threshold_s=300.0)
    s.observe(0.0, 0)
    edge = s.observe(9999.0, 10 * MIN, eyes_open=False)
    assert edge.state == UNKNOWN


def test_backdating_is_floored_at_the_wake_time():
    """After a wake, the idle clock remembers the sleep. That memory is not
    evidence about a person, so the departure cannot precede the wake."""
    s = PresenceSense(threshold_s=300.0)
    woke_at = 8 * 3600 * 1000
    s.observe(0.0, woke_at, awake_since_ms=woke_at)
    s.observe(8 * 3600.0, woke_at + 6 * MIN, awake_since_ms=woke_at)
    _e, claim = s.observe_and_witness(0.0, woke_at + 10 * MIN,
                                       awake_since_ms=woke_at)
    assert claim is not None
    assert claim.coord.t_ms == woke_at, "backdated across the sleep"


def test_attention_gaps_merge_instead_of_double_counting():
    """A lid closed at lunch makes BOTH an away witness (idle clock) and a
    closed-eyes witness (the lid) for the same hour. Returning both would
    make the binder count one absence twice, plausibly."""
    from tracemem.app.witness import WitnessKind, witness
    from tracemem.capture.presence import attention_gaps
    away = witness(WitnessKind.AWAY, t_ms=10 * MIN, span_t2_ms=60 * MIN)
    shut = witness(WitnessKind.CLOSED_EYES, t_ms=12 * MIN, span_t2_ms=58 * MIN)
    assert attention_gaps([away, shut]) == [(10 * MIN, 60 * MIN)]


def test_attention_gaps_keeps_genuinely_separate_absences():
    from tracemem.app.witness import WitnessKind, witness
    from tracemem.capture.presence import attention_gaps
    a = witness(WitnessKind.AWAY, t_ms=10 * MIN, span_t2_ms=20 * MIN)
    b = witness(WitnessKind.CLOSED_EYES, t_ms=60 * MIN, span_t2_ms=90 * MIN)
    assert attention_gaps([a, b]) == [(10 * MIN, 20 * MIN), (60 * MIN, 90 * MIN)]


def test_away_spans_no_longer_folds_in_closed_eyes():
    from tracemem.app.witness import WitnessKind, witness
    shut = witness(WitnessKind.CLOSED_EYES, t_ms=1, span_t2_ms=2)
    assert away_spans([shut]) == []


def test_an_absence_open_when_the_eyes_shut_is_closed_not_dropped():
    """RE-AUDIT FIX 2026-07-21: the eyes closing on an OPEN absence used to
    drop it silently — trading a false claim for a silent hole, which is the
    same failure with better manners. We DO know how long it ran: from the
    departure until the moment the eyes shut."""
    s = PresenceSense(threshold_s=300.0)
    s.observe(0.0, 0)                                   # present at 00:00
    s.observe(600.0, 20 * MIN)                          # away, backdated to 00:10
    _edge, claim = s.observe_and_witness(600.0, 30 * MIN, eyes_open=False)
    assert claim is not None, "an open absence vanished when the lid shut"
    assert claim.coord.t_ms == 10 * MIN
    assert claim.structured["t2_ms"] == 30 * MIN        # closed at the lid


def test_a_dark_idle_sense_still_drops_rather_than_inventing_an_end():
    """The other UNKNOWN case must keep its old behaviour: with no idle
    reading at all we cannot bound the end, so we say nothing."""
    s = PresenceSense(threshold_s=300.0)
    s.observe(0.0, 0)
    s.observe(600.0, 10 * MIN)
    _edge, claim = s.observe_and_witness(None, 12 * MIN)
    assert claim is None
