"""ORACLE for Free-space monitor + Degrade ladder (11_machine > Disk pressure).

PROOF (monitor): "Test with a small quota volume: thresholds fire in order."
PROOF (ladder):  "Quota test: degradation follows the declared order; store
                  never corrupts."

On the quota volume: creating one needs `hdiutil`/root and a real disk
image, which is a drill (Full-disk test), not a unit test. What a unit test
CAN do — and what the ladder actually needs proven — is that the level
machine is correct at every boundary and that the order of death is the
declared one. So the free-space READ is exercised against the real
filesystem (it is three lines and it is where a units mistake would hide),
and the threshold sequence is driven by a scripted volume.

The distinction is kept honest: `test_the_reader_reads_a_real_volume` is
the only test here touching the real disk, and it asserts nothing about
thresholds — it exists so that a wrong statvfs field cannot pass.
"""
import os

import pytest

from tracemem.app.disk_pressure import (
    DEGRADE_BYTES, GIB, HYSTERESIS_BYTES, LADDER, STOP_BYTES, WARN_BYTES,
    DegradeLadder, FreeSpaceMonitor, Level, free_bytes, level_for,
    order_of_death, severity, shed_for,
)
from tracemem.app.witness import WitnessKind


class FakeVolume:
    """A volume whose free space is whatever the test last said it was."""

    def __init__(self, free):
        self.free = free

    def __call__(self, _path):
        return self.free


# -- the real read ---------------------------------------------------------
def test_the_reader_reads_a_real_volume(tmp_path):
    """Not a threshold test: this is here so that reading the wrong statvfs
    field, or multiplying by the wrong block size, cannot pass unnoticed."""
    got = free_bytes(str(tmp_path))
    st = os.statvfs(str(tmp_path))
    assert got == st.f_bavail * st.f_frsize
    assert got != st.f_bfree * st.f_frsize or st.f_bavail == st.f_bfree
    assert 0 < got < 2 ** 60


def test_the_reader_uses_available_not_free(tmp_path):
    """f_bfree counts the root-reserved pool this process cannot write into.
    Using it would keep the monitor calm while writes were already failing."""
    st = os.statvfs(str(tmp_path))
    assert free_bytes(str(tmp_path)) <= st.f_bfree * st.f_frsize


# -- the level machine -----------------------------------------------------
@pytest.mark.parametrize("free,expected", [
    (100 * GIB, Level.OK),
    (WARN_BYTES, Level.OK),                 # boundary is exclusive: at it, still OK
    (WARN_BYTES - 1, Level.WARN),
    (DEGRADE_BYTES, Level.WARN),
    (DEGRADE_BYTES - 1, Level.DEGRADE),
    (STOP_BYTES, Level.DEGRADE),
    (STOP_BYTES - 1, Level.STOP),
    (0, Level.STOP),
])
def test_every_boundary_lands_on_the_right_level(free, expected):
    assert level_for(free) is expected


def test_severity_is_ordered():
    assert severity(Level.OK) < severity(Level.WARN) < severity(Level.DEGRADE) \
        < severity(Level.STOP)


# -- THE ORACLE: thresholds fire in order ----------------------------------
def test_thresholds_fire_in_order_as_the_volume_fills():
    vol = FakeVolume(100 * GIB)
    seen = []
    mon = FreeSpaceMonitor("/fake", sink=lambda lvl, free: seen.append(lvl),
                           reader=vol)

    fired = []
    for free in (100 * GIB, 15 * GIB, 4 * GIB, 512 * 1024 * 1024):
        vol.free = free
        w = mon.poll(now_ms=1000 + free)
        if w is not None:
            fired.append(w.structured["level"])

    assert fired == ["warn", "degrade", "stop"]
    assert [lvl.value for lvl in seen] == ["warn", "degrade", "stop"]


def test_each_crossing_is_witnessed_exactly_once():
    """The nudge law: a threshold that keeps being true must not keep
    speaking. Coverage math reads these; a repeated witness is a lie about
    how many times the disk filled up."""
    vol = FakeVolume(4 * GIB)
    mon = FreeSpaceMonitor("/fake", reader=vol)

    first = mon.poll(1000)
    assert first is not None and first.structured["level"] == "degrade"
    for t in range(1001, 1010):
        assert mon.poll(t) is None
    assert mon.crossings == 1


def test_the_witness_is_a_real_witness_claim():
    vol = FakeVolume(512 * 1024 * 1024)
    w = FreeSpaceMonitor("/fake", reader=vol).poll(now_ms=42)
    assert w.structured["witness"] == WitnessKind.DISK_PRESSURE.value
    assert w.scope == "memory"
    assert w.structured["shed"] == list(LADDER)
    assert w.structured["free_bytes"] == 512 * 1024 * 1024
    assert "stop" in w.text


def test_recovery_needs_real_headroom_not_one_byte():
    """A volume hovering on a boundary must not flap. Without hysteresis the
    witness stream becomes noise, and noisy witnesses get ignored — which
    costs the coverage audit its only input."""
    vol = FakeVolume(4 * GIB)
    mon = FreeSpaceMonitor("/fake", reader=vol)
    assert mon.poll(1).structured["level"] == "degrade"

    vol.free = DEGRADE_BYTES + 1            # technically better, barely
    assert mon.poll(2) is None
    assert mon.level is Level.DEGRADE

    vol.free = DEGRADE_BYTES + HYSTERESIS_BYTES + 1
    w = mon.poll(3)
    assert w is not None and w.structured["level"] == "warn"


def test_recovery_is_witnessed_too():
    """Coming back is as much a fact as going down — an absence answer needs
    to know when the hole CLOSED."""
    vol = FakeVolume(512 * 1024 * 1024)
    mon = FreeSpaceMonitor("/fake", reader=vol)
    mon.poll(1)
    vol.free = 100 * GIB
    w = mon.poll(2)
    assert w.structured["level"] == "ok"
    assert w.structured["was"] == "stop"
    assert "better" in w.text


# -- the ladder ------------------------------------------------------------
def test_the_declared_order_of_death():
    """The brief's order, verbatim: rebuildable first, the eye last."""
    assert order_of_death() == ["indexes", "deep-extraction", "capture"]


def test_shedding_is_cumulative_and_capture_dies_last():
    assert shed_for(Level.OK) == ()
    assert shed_for(Level.WARN) == ()
    assert shed_for(Level.DEGRADE) == ("indexes", "deep-extraction")
    assert shed_for(Level.STOP) == ("indexes", "deep-extraction", "capture")
    for level in (Level.OK, Level.WARN, Level.DEGRADE):
        assert "capture" not in shed_for(level)


def test_the_store_is_never_a_rung():
    """THE property the ladder exists for: when capture stops, the store
    must still be able to write the witness that says capture stopped.
    Otherwise the product loses the ability to admit it is failing at the
    exact moment it starts failing, and the user sees silence — which is
    indistinguishable from 'nothing happened'."""
    ladder = DegradeLadder()
    for level in Level:
        ladder.apply(level)
        assert ladder.store_writes_permitted() is True
        assert all("store" not in rung for rung in ladder.off)


def test_asking_whether_store_writes_are_a_rung_is_an_error():
    ladder = DegradeLadder()
    with pytest.raises(ValueError):
        ladder.is_running("store-writes")


def test_the_ladder_reports_deltas_not_just_state():
    """A caller must not have to re-stop what is already stopped; that is how
    an organ gets torn down and rebuilt on every poll."""
    ladder = DegradeLadder()
    assert ladder.apply(Level.DEGRADE)["newly_off"] == ["indexes", "deep-extraction"]
    assert ladder.apply(Level.DEGRADE)["newly_off"] == []
    step = ladder.apply(Level.STOP)
    assert step["newly_off"] == ["capture"] and step["restored"] == []
    back = ladder.apply(Level.OK)
    assert back["restored"] == ["indexes", "deep-extraction", "capture"]
    assert back["off"] == []


def test_recovery_restores_in_the_reverse_order_of_death():
    """Capture — the thing whose loss is unrecoverable — comes back first."""
    ladder = DegradeLadder()
    ladder.apply(Level.STOP)
    restored = ladder.apply(Level.DEGRADE)["restored"]
    assert restored == ["capture"]


def test_the_monitor_and_the_ladder_actually_compose():
    """Each is proven alone above. This is the wire between them — the thing
    that is missing from most of this repo's green modules."""
    vol = FakeVolume(100 * GIB)
    ladder = DegradeLadder()
    mon = FreeSpaceMonitor("/fake", sink=lambda lvl, _free: ladder.apply(lvl),
                           reader=vol)

    vol.free = 4 * GIB
    mon.poll(1)
    assert ladder.is_running("capture") is True
    assert ladder.is_running("indexes") is False

    vol.free = 100 * 1024 * 1024
    mon.poll(2)
    assert ladder.is_running("capture") is False
    assert ladder.store_writes_permitted() is True
