"""ORACLE for the Growth meter (03_text > Storage math).

PROOF: "The number appears on the cockpit and updates daily."

The cockpit half is checked by writing into campaign/status.json's `numbers`
block, which tools/cockpit.py already renders — so "appears on the cockpit"
is a fact about the file the cockpit reads, not about a screenshot.

The tests that matter most are the ones asserting the meter REFUSES to
project. Two readings a minute apart extrapolate cheerfully to terabytes a
year, and a fabricated growth figure is worse than none: the disk-pressure
thresholds and the storage projection are both currently DECLARED numbers
waiting for this meter to replace them, and replacing a guess with a
confident wrong measurement is a step backwards.
"""
import json

import pytest

from tools.growth_meter import (
    MIN_READINGS, MIN_SPAN_H, load_history, measure, rate, update_status,
)

HOUR_MS = 3_600_000


def _write_store(tmp_path, db=1000, wal=0, shm=0):
    path = tmp_path / "store.sqlite3"
    path.write_bytes(b"x" * db)
    if wal:
        (tmp_path / "store.sqlite3-wal").write_bytes(b"w" * wal)
    if shm:
        (tmp_path / "store.sqlite3-shm").write_bytes(b"s" * shm)
    return str(path)


# -- the measurement -------------------------------------------------------
def test_it_measures_a_real_file(tmp_path):
    path = _write_store(tmp_path, db=4096)
    got = measure(path)
    assert got["exists"] is True
    assert got["bytes"] == 4096
    assert got["parts"]["db"] == 4096


def test_the_wal_sidecars_are_counted(tmp_path):
    """SQLite in WAL mode is three files, and the WAL can be a large share
    between checkpoints. Counting only the main file under-reports exactly
    when the store is busiest — the moment the number matters."""
    path = _write_store(tmp_path, db=1000, wal=5000, shm=300)
    got = measure(path)
    assert got["bytes"] == 6300
    assert got["parts"]["-wal"] == 5000
    assert got["parts"]["-shm"] == 300


def test_a_missing_store_is_reported_not_crashed(tmp_path):
    got = measure(str(tmp_path / "nothing.sqlite3"))
    assert got["exists"] is False
    assert got["bytes"] == 0


# -- the refusal to project ------------------------------------------------
def test_no_history_yields_no_numbers():
    assert rate([]) == {"growth_readings": 0}


def test_one_reading_cannot_be_a_rate():
    got = rate([{"t_ms": 0, "bytes": 100, "exists": True}])
    assert "store_bytes_per_day" not in got


def test_two_readings_a_minute_apart_refuse_to_extrapolate():
    """THE refusal that matters. A minute of growth times 1440 is a number
    that would look authoritative in a status file and be worthless."""
    history = [{"t_ms": 0, "bytes": 1_000_000, "exists": True},
               {"t_ms": 60_000, "bytes": 2_000_000, "exists": True}]
    got = rate(history)
    assert "store_bytes_per_day" not in got
    assert "store_gb_per_year" not in got
    assert "too early" in got["growth_status"]


def test_the_refusal_says_what_is_missing():
    """An absent number invites a look at the series; a silent absence just
    looks like a bug."""
    got = rate([{"t_ms": 0, "bytes": 1, "exists": True},
                {"t_ms": HOUR_MS, "bytes": 2, "exists": True}])
    assert str(MIN_READINGS) in got["growth_status"]
    assert "have 2" in got["growth_status"]


def test_enough_readings_over_enough_time_do_produce_a_rate():
    history = [
        {"t_ms": 0, "bytes": 1_000_000_000, "exists": True},
        {"t_ms": 12 * HOUR_MS, "bytes": 1_500_000_000, "exists": True},
        {"t_ms": 24 * HOUR_MS, "bytes": 2_000_000_000, "exists": True},
    ]
    got = rate(history)
    assert got["growth_status"] == "measured"
    assert got["store_bytes_per_day"] == pytest.approx(1_000_000_000, rel=0.01)
    assert got["store_gb_per_year"] == pytest.approx(340.0, rel=0.02)


def test_the_span_floor_is_honoured_exactly():
    """Just under the floor refuses; just over it measures. A boundary that
    is documented but not enforced is decoration."""
    def hist(hours):
        return [{"t_ms": int(i * hours * HOUR_MS / 2), "bytes": 100 + i,
                 "exists": True} for i in range(3)]

    assert "store_bytes_per_day" not in rate(hist(MIN_SPAN_H - 1))
    assert "store_bytes_per_day" in rate(hist(MIN_SPAN_H + 1))


def test_readings_from_before_the_store_existed_are_ignored():
    """A meter started before first light records zeros. Including them
    would put a fake step-change at the moment the store was created."""
    history = [
        {"t_ms": 0, "bytes": 0, "exists": False},
        {"t_ms": 12 * HOUR_MS, "bytes": 1_000_000, "exists": True},
        {"t_ms": 24 * HOUR_MS, "bytes": 2_000_000, "exists": True},
        {"t_ms": 36 * HOUR_MS, "bytes": 3_000_000, "exists": True},
    ]
    got = rate(history)
    assert got["growth_readings"] == 3
    assert got["store_bytes_per_day"] == pytest.approx(2_000_000, rel=0.01)


def test_a_shrinking_store_reports_a_negative_rate_rather_than_hiding_it():
    """A vacuum or a purge genuinely shrinks the store. Clamping to zero
    would hide a real event; the projection being negative is a signal to
    read the series, which is correct."""
    history = [
        {"t_ms": 0, "bytes": 3_000_000_000, "exists": True},
        {"t_ms": 12 * HOUR_MS, "bytes": 2_000_000_000, "exists": True},
        {"t_ms": 24 * HOUR_MS, "bytes": 1_000_000_000, "exists": True},
    ]
    assert rate(history)["store_bytes_per_day"] < 0


# -- the cockpit half ------------------------------------------------------
def test_the_numbers_land_where_the_cockpit_reads_them(tmp_path):
    """PROOF: 'the number appears on the cockpit'. The cockpit renders
    status.json's `numbers` block, so writing there IS appearing."""
    status = tmp_path / "status.json"
    status.write_text(json.dumps({
        "updated": "2026-07-22",
        "numbers": {"tests_green": 828},
        "now": [],
    }))

    update_status({"store_bytes": 123, "store_bytes_per_day": 45},
                  status_path=str(status))

    after = json.loads(status.read_text())
    assert after["numbers"]["store_bytes"] == 123
    assert after["numbers"]["store_bytes_per_day"] == 45
    assert after["numbers"]["tests_green"] == 828   # nothing else disturbed
    assert after["updated"] == "2026-07-22"


def test_history_round_trips(tmp_path):
    from tools.growth_meter import append_history
    path = str(tmp_path / "growth.jsonl")
    append_history({"t_ms": 1, "bytes": 10, "exists": True}, path)
    append_history({"t_ms": 2, "bytes": 20, "exists": True}, path)
    got = load_history(path)
    assert [h["bytes"] for h in got] == [10, 20]


def test_loading_a_missing_history_is_empty_not_an_error(tmp_path):
    assert load_history(str(tmp_path / "never.jsonl")) == []


# -- end to end ------------------------------------------------------------
def test_the_tool_runs_against_a_real_file(tmp_path):
    from tools.growth_meter import main
    store = _write_store(tmp_path, db=2048, wal=1024)
    status = tmp_path / "status.json"
    status.write_text(json.dumps({"updated": "x", "numbers": {}, "now": []}))
    history = str(tmp_path / "g.jsonl")

    assert main(["--store", store, "--history", history,
                 "--status", str(status)]) == 0

    numbers = json.loads(status.read_text())["numbers"]
    assert numbers["store_bytes"] == 3072
    assert numbers["growth_readings"] == 1
    assert "store_bytes_per_day" not in numbers   # one reading earns nothing


def test_dry_run_touches_nothing(tmp_path):
    from tools.growth_meter import main
    store = _write_store(tmp_path)
    status = tmp_path / "status.json"
    status.write_text(json.dumps({"numbers": {}}))
    history = tmp_path / "g.jsonl"

    main(["--store", store, "--history", str(history),
          "--status", str(status), "--dry-run"])

    assert not history.exists()
    assert json.loads(status.read_text())["numbers"] == {}


def test_current_size_is_reported_from_the_very_first_reading():
    """Today's store size needs no history — only the RATE does. Withholding
    a fact already in hand until a second reading exists would leave the
    cockpit blank about something it knows."""
    got = rate([{"t_ms": 0, "bytes": 4096, "exists": True}])
    assert got["store_bytes"] == 4096
    assert "store_bytes_per_day" not in got
