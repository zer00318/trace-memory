"""The alarm that was missing on 2026-08-05.

Every test here is a shape the sixteen-day outage actually had, or a shape
the OLD watchdog had that made it unreadable. A green suite over an alarm
that cannot fire is exactly the failure this file is about, so each case
asserts the VERDICT, never the plumbing.
"""
import sqlite3
import time

import pytest

from tools import blind_alarm as ba


def _store(tmp_path, rows):
    """A store with only the columns the alarm reads. Deliberately NOT a
    ClaimLog: the alarm must work on a store it cannot decrypt, which is why
    it keys off `bidder` (plaintext by design) and never opens a claim."""
    p = tmp_path / "s.sqlite3"
    db = sqlite3.connect(p)
    db.execute("CREATE TABLE claim (id TEXT PRIMARY KEY, t_ms INTEGER, "
               "bidder TEXT)")
    db.executemany("INSERT INTO claim VALUES (?,?,?)", rows)
    db.commit()
    db.close()
    return p


@pytest.fixture
def alive(monkeypatch):
    monkeypatch.setattr(ba, "daemon_running", lambda: True)


def test_a_store_fed_only_by_witnesses_is_BLIND(tmp_path, monkeypatch, alive):
    """THE OUTAGE ITSELF. Aug 5 → Aug 20: the daemon ran, the file grew, the
    mtime updated every 70s, and the only writer was the heartbeat. Any check
    that counts rows or stats the file calls this healthy."""
    now = time.time()
    old = int((now - 40 * 60) * 1000)
    rows = [("c%d" % i, old, "system:witness") for i in range(500)]
    rows.append(("real", int((now - 16 * 24 * 3600) * 1000), "peripheral:change"))
    monkeypatch.setattr(ba, "idle_seconds", lambda: 5.0)
    v = ba.check(_store(tmp_path, rows), now=now)
    assert v["fault"] == "blind"


def test_a_closed_eyelid_is_NOT_an_alarm(tmp_path, monkeypatch, alive):
    """F-EYELID closes the eye after 15 idle minutes BY FOUNDER RULING.
    Alarming on that would make the alarm a liar within one lunch break, and
    a liar is what the previous watchdog was."""
    now = time.time()
    rows = [("r", int((now - 3 * 3600) * 1000), "peripheral:change")]
    monkeypatch.setattr(ba, "idle_seconds", lambda: 3 * 3600.0)
    assert ba.check(_store(tmp_path, rows), now=now)["ok"] is True


def test_recent_perception_is_OK(tmp_path, monkeypatch, alive):
    now = time.time()
    rows = [("r", int((now - 30) * 1000), "ax:focused-window")]
    monkeypatch.setattr(ba, "idle_seconds", lambda: 2.0)
    assert ba.check(_store(tmp_path, rows), now=now)["ok"] is True


def test_a_dead_daemon_is_reported_before_anything_else(tmp_path, monkeypatch):
    """A fresh store plus no process must not read as 'empty-store' — the
    person needs the ACTIONABLE fault, and the action differs."""
    monkeypatch.setattr(ba, "daemon_running", lambda: False)
    monkeypatch.setattr(ba, "idle_seconds", lambda: 1.0)
    now = time.time()
    rows = [("r", int((now - 10) * 1000), "peripheral:change")]
    assert ba.check(_store(tmp_path, rows), now=now)["fault"] == "not-running"


def test_unreadable_idle_fails_LOUD(tmp_path, monkeypatch, alive):
    """If idle cannot be read we assume the founder is present, so silence
    still alarms. The whole point is that unknown must not resolve to
    healthy — that resolution is what cost sixteen days."""
    now = time.time()
    rows = [("r", int((now - 3 * 3600) * 1000), "peripheral:change")]
    monkeypatch.setattr(ba, "idle_seconds", lambda: -1.0)
    assert ba.check(_store(tmp_path, rows), now=now)["fault"] == "blind"


# ---- the alarm fired, and nobody could act on it --------------------------
#
# Both blackouts had the SAME cause and the notification never named it.
# Screen Recording is granted to a binary; this venv runs on Xcode's bundled
# Python; updating Xcode changes the binary and the grant evaporates. The
# daemon stays up, alive() keeps saying True, and the only symptom is claims
# that stop arriving. The banner said "the eye is running and not seeing",
# which is true, and needs an investigation before anything can be done.

def test_a_revoked_permission_is_NAMED_and_the_fix_is_carried(
        tmp_path, monkeypatch, alive):
    """THE ORACLE. When the cause is readable, the alarm says the cause and
    what to press — not a symptom that needs an afternoon to chase."""
    now = time.time()
    rows = [("real", int((now - 49 * 3600) * 1000), "peripheral:change")]
    monkeypatch.setattr(ba, "idle_seconds", lambda: 5.0)
    monkeypatch.setattr(ba, "_screen_recording_denied", lambda: True)

    v = ba.check(_store(tmp_path, rows), now=now)

    assert v["fault"] == "blind"
    assert v["permission_denied"] is True
    assert "SCREEN RECORDING IS DENIED" in v["detail"]
    assert "TRACE.app" in v["fix"], "the banner must carry the thing to press"


def test_a_blindness_of_unknown_cause_still_alarms_without_inventing_one(
        tmp_path, monkeypatch, alive):
    """The explanation is a bonus, never a precondition. An alarm that only
    fires when it can explain itself is an alarm that misses the novel
    failure, which is the only kind worth having one for."""
    now = time.time()
    rows = [("real", int((now - 3 * 3600) * 1000), "peripheral:change")]
    monkeypatch.setattr(ba, "idle_seconds", lambda: 5.0)
    monkeypatch.setattr(ba, "_screen_recording_denied", lambda: False)

    v = ba.check(_store(tmp_path, rows), now=now)

    assert v["fault"] == "blind"
    assert v["permission_denied"] is False
    assert not v["fix"], "no cause read means no remedy claimed"


def test_thirty_one_minutes_and_two_days_do_not_read_the_same(
        tmp_path, monkeypatch, alive):
    """The message that ran for 49 hours was word-for-word the one that runs
    at minute 31. A duration is the difference between a hiccup and the
    second multi-day blackout this product has had."""
    now = time.time()
    monkeypatch.setattr(ba, "idle_seconds", lambda: 5.0)
    monkeypatch.setattr(ba, "_screen_recording_denied", lambda: False)

    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    fresh = ba.check(_store(a, [
        ("r", int((now - 31 * 60) * 1000), "peripheral:change")]), now=now)
    chronic = ba.check(_store(b, [
        ("r", int((now - 49 * 3600) * 1000), "peripheral:change")]), now=now)

    assert "31 minutes" in fresh["detail"]
    assert "DAYS" in chronic["detail"], (
        "an outage measured in days must not be phrased like one measured "
        "in minutes")
