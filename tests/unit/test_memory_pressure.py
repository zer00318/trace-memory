"""ORACLE for Pressure tap + Ring shrink response (11_machine > Memory pressure).

PROOF (tap):    "Simulated pressure → events flow to both consumers, witnessed."
PROOF (shrink): "Pressure test: ring shrinks, vm swap delta zero, capture continues."

The swap-delta clause is NOT tested here and is not silently skipped: it
requires `memory_pressure -l critical`, which stresses the whole machine,
and this Mac runs the old stack's live capture that the laws forbid
disturbing. It is a drill, recorded as un-run on the board. Everything that
does not need a stressed machine is proven here, including one test that
reads the REAL kernel value so a broken sysctl call cannot pass.
"""
import pytest

from tracemem.app.memory_pressure import (
    MIN_VIABLE_RING_BYTES, MIB, Pressure, PressureTap, RingShrinkResponse,
    read_pressure, ring_budget_for, severity,
)
from tracemem.app.witness import WitnessKind
from tracemem.extraction.retina import DEFAULT_BUDGET_BYTES, FrameRecord, Retina


# -- the real read ---------------------------------------------------------
def test_the_kernel_actually_answers():
    """Not a mock: this calls sysctlbyname on the real machine. If the name
    or the ctypes signature is wrong, this is the test that says so."""
    got = read_pressure()
    assert isinstance(got, Pressure)


def test_the_live_read_returns_a_real_pressure_level():
    """A REAL sysctl read, asserting a fact about the CODE.

    FIXED 2026-07-28: this used to assert `read_pressure() is NORMAL` — and
    its own docstring conceded that is "a fact about the test environment,
    not about the code". It came true. Running the daemon alongside a 9 GB
    resident model put this Mac at 30% free memory, the read correctly
    returned WARN, and the suite went red because the machine was BUSY.

    That is backwards twice over. A test that fails when the environment is
    loaded fails exactly when you most need to run it, and — worse here —
    it turns the sensor being RIGHT into a red suite, which trains everyone
    to distrust it.

    What is actually the code's business, and is kept: the read reaches the
    kernel and comes back as a real level, so a byte-order or size mistake
    (garbage, or a constant CRITICAL) still fails.
    """
    got = read_pressure()
    assert isinstance(got, Pressure)
    assert got in (Pressure.NORMAL, Pressure.WARN, Pressure.CRITICAL)


def test_the_live_read_is_stable_across_two_calls():
    """Garbage from a mis-sized sysctl read tends to vary run to run; a real
    level does not flip between two reads a moment apart."""
    assert read_pressure() is read_pressure()


def test_an_unknown_kernel_value_is_treated_as_bad_news(monkeypatch):
    """The safe direction. A value this code does not understand must not
    resolve to 'everything is fine' — that is the assumption that costs
    data."""
    import tracemem.app.memory_pressure as mp
    monkeypatch.setattr(mp, "_read_sysctl_int", lambda _name: 99)
    assert mp.read_pressure() is Pressure.CRITICAL


# -- the budget ladder -----------------------------------------------------
def test_severity_is_ordered():
    assert severity(Pressure.NORMAL) < severity(Pressure.WARN) \
        < severity(Pressure.CRITICAL)


def test_warn_halves_and_critical_goes_to_the_minimum():
    assert ring_budget_for(Pressure.NORMAL) == DEFAULT_BUDGET_BYTES
    assert ring_budget_for(Pressure.WARN) == DEFAULT_BUDGET_BYTES // 2
    assert ring_budget_for(Pressure.CRITICAL) == MIN_VIABLE_RING_BYTES


def test_the_minimum_is_never_undercut_by_the_fraction():
    """A small ceiling halved could land below the minimum viable ring. The
    floor wins — a ring too small to hold two frames cannot see motion, and
    a blind eye that still reports itself running is the failure this whole
    section exists to prevent."""
    tiny = 100 * MIB
    assert ring_budget_for(Pressure.WARN, tiny) == MIN_VIABLE_RING_BYTES
    assert ring_budget_for(Pressure.CRITICAL, tiny) == MIN_VIABLE_RING_BYTES


def test_the_minimum_holds_at_least_two_full_frames():
    """The anchor the constant is justified by, asserted rather than
    described: a 4K frame at 4 bytes/pixel is ~32 MiB."""
    four_k_frame = 3840 * 2160 * 4
    assert MIN_VIABLE_RING_BYTES >= 2 * four_k_frame


# -- THE ORACLE: events flow to both consumers, witnessed ------------------
def test_events_flow_to_both_consumers_and_are_witnessed():
    seen_a, seen_b = [], []
    tap = PressureTap(consumers=[seen_a.append, seen_b.append])

    w = tap.observe(Pressure.WARN, now_ms=1000)
    assert seen_a == [Pressure.WARN] and seen_b == [Pressure.WARN]
    assert w.structured["witness"] == WitnessKind.MEMORY_PRESSURE.value
    assert w.structured["level"] == "warn"
    assert w.scope == "memory"

    w2 = tap.observe(Pressure.CRITICAL, now_ms=2000)
    assert seen_a == [Pressure.WARN, Pressure.CRITICAL]
    assert w2.structured["was"] == "warn"
    assert tap.events == 2


def test_an_unchanged_level_is_not_an_event():
    """The tap is polled continuously. If steady state produced witnesses,
    the store would fill with them and the coverage audit would drown."""
    seen = []
    tap = PressureTap(consumers=[seen.append])
    assert tap.observe(Pressure.WARN, 1) is not None
    for t in range(2, 20):
        assert tap.observe(Pressure.WARN, t) is None
    assert seen == [Pressure.WARN]
    assert tap.events == 1


def test_recovery_is_an_event_too():
    tap = PressureTap()
    tap.observe(Pressure.CRITICAL, 1)
    w = tap.observe(Pressure.NORMAL, 2)
    assert w.structured["level"] == "normal" and w.structured["was"] == "critical"


def test_polling_uses_the_injected_reader():
    levels = iter([Pressure.NORMAL, Pressure.CRITICAL])
    tap = PressureTap(reader=lambda: next(levels))
    assert tap.poll(1) is None                 # already NORMAL
    assert tap.poll(2).structured["level"] == "critical"


def test_subscribers_can_be_added_after_construction():
    tap = PressureTap()
    seen = []
    tap.subscribe(seen.append)
    tap.observe(Pressure.WARN, 1)
    assert seen == [Pressure.WARN]


# -- the wire: tap -> retina ----------------------------------------------
def _fill(retina, n, nbytes):
    for i in range(n):
        retina.push(FrameRecord(t_ms=i, display="main", frame=object(),
                               nbytes=nbytes))


def test_the_ring_actually_shrinks_under_pressure():
    """THE WIRE. Both halves are individually green in this repo already;
    this asserts they are connected, which is the thing that was missing."""
    retina = Retina()
    _fill(retina, 8, 30 * MIB)                 # 240 MiB, under the 256 ceiling
    before = retina.bytes_used
    assert before > MIN_VIABLE_RING_BYTES

    tap = PressureTap(consumers=[RingShrinkResponse(retina)])
    tap.observe(Pressure.CRITICAL, now_ms=1)

    assert retina.budget_bytes == MIN_VIABLE_RING_BYTES
    assert retina.bytes_used <= MIN_VIABLE_RING_BYTES


def test_capture_continues_after_the_shrink():
    """'Capture continues' is half the oracle. A shrink that leaves the ring
    unable to accept the next frame is a stop wearing a shrink's clothes."""
    retina = Retina()
    _fill(retina, 8, 30 * MIB)
    tap = PressureTap(consumers=[RingShrinkResponse(retina)])
    tap.observe(Pressure.CRITICAL, now_ms=1)

    retina.push(FrameRecord(t_ms=999, display="main", frame=object(),
                           nbytes=30 * MIB))
    assert retina.scene_now() is not None
    assert retina.scene_now().t_ms == 999


def test_recovery_restores_the_original_ceiling_not_the_shrunk_one():
    """Without holding the ceiling, one bad afternoon costs the eye its
    resolution permanently — the ring would 'recover' to whatever it was
    last shrunk to."""
    retina = Retina()
    response = RingShrinkResponse(retina)
    tap = PressureTap(consumers=[response])

    tap.observe(Pressure.CRITICAL, 1)
    assert retina.budget_bytes == MIN_VIABLE_RING_BYTES
    tap.observe(Pressure.NORMAL, 2)
    assert retina.budget_bytes == DEFAULT_BUDGET_BYTES


def test_the_response_counts_what_it_cost():
    """Evictions are lost frames. The number must be visible, not implied."""
    retina = Retina()
    _fill(retina, 8, 30 * MIB)
    response = RingShrinkResponse(retina)
    PressureTap(consumers=[response]).observe(Pressure.CRITICAL, 1)
    assert response.evicted_total > 0
