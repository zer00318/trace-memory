"""An eye shut on purpose must not be reported as an eye that has failed.

WITNESSED 2026-08-24. The founder's screen was locked. The eyelid had closed
the eye exactly as it is supposed to. `./trace status` printed:

    !! THE EYE IS NOT WATCHING (state: parked)
       reason: RuntimeError('ScreenCaptureKit reports zero displays')

Both halves were wrong to show. The eye was healthy, and the error was a
stale leftover from a single start attempt made hours earlier while the
display slept — already retried past, `errors: 1`.

The cost is not the wrong words, it is what they train. This alarm exists to
catch a genuinely blinded eye; that has happened twice and cost sixteen days
of capture once. If it also fires every time its owner walks away from the
desk, it gets ignored, and the real event arrives disguised as the noise.

So the rule these tests hold: the eyelid's own reason is the authority,
because the eyelid is the component that made the decision.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _ask_module():
    spec = importlib.util.spec_from_file_location(
        "_ask_under_test", ROOT / "tools" / "ask.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_ask_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def _run(monkeypatch, capsys, reply):
    ask = _ask_module()

    class FakeFace:
        def __init__(self, path):
            pass

        def send(self, **req):
            assert req["op"] == "stats"
            return reply

        def close(self):
            pass

    monkeypatch.setattr(ask, "Face", FakeFace)
    ask.main(["--stats"])
    return capsys.readouterr().out


BASE = {"ok": True, "bound": 10, "pending": 0, "episodes": 2, "nodes": 5}
STALE = {"last_error": "RuntimeError('ScreenCaptureKit reports zero displays')"}


@pytest.mark.parametrize("because,phrase", [
    ("locked", "the screen is locked"),
    ("idle", "nobody has touched the machine"),
])
def test_deliberate_close_is_not_an_alarm(monkeypatch, capsys, because, phrase):
    out = _run(monkeypatch, capsys, dict(
        BASE,
        organs={"eye": "parked", "eye_detail": STALE},
        eyelid={"eyes_open": False, "closed_because": because}))
    assert "NOT WATCHING" not in out
    assert phrase in out
    # The stale error must not be shown either. It is true that it once
    # happened and false that it is why the eye is closed now, which is the
    # most expensive kind of status line: accurate and misleading.
    assert "ScreenCaptureKit" not in out
    # Say that it comes back on its own, or a calm line still reads as a
    # thing the founder has to go fix.
    assert "reopens by itself" in out


def test_blind_while_it_should_be_open_still_alarms(monkeypatch, capsys):
    """The case the line exists for. Nothing above may soften this."""
    out = _run(monkeypatch, capsys, dict(
        BASE,
        organs={"eye": "dead", "eye_detail": STALE},
        eyelid={"eyes_open": True, "closed_because": None}))
    assert "!! THE EYE IS NOT WATCHING (state: dead)" in out
    assert "ScreenCaptureKit" in out


def test_parked_while_the_eyelid_says_open_still_alarms(monkeypatch, capsys):
    """Disagreement is not permission to be quiet.

    `parked` alone must not buy silence — only parked WITH the eyelid
    confirming it shut the eye. If the two disagree, something is wrong that
    nobody has a name for yet, and that is precisely when the founder should
    hear about it.
    """
    out = _run(monkeypatch, capsys, dict(
        BASE,
        organs={"eye": "parked", "eye_detail": STALE},
        eyelid={"eyes_open": True, "closed_because": None}))
    assert "NOT WATCHING" in out


def test_missing_eyelid_report_still_alarms(monkeypatch, capsys):
    """An older daemon publishes no eyelid block. Absence of evidence that
    the close was deliberate is not evidence that it was."""
    out = _run(monkeypatch, capsys, dict(
        BASE, organs={"eye": "parked", "eye_detail": STALE}))
    assert "NOT WATCHING" in out


def test_alive_eye_says_nothing_either_way(monkeypatch, capsys):
    out = _run(monkeypatch, capsys, dict(
        BASE, organs={"eye": "alive", "eye_detail": {}},
        eyelid={"eyes_open": True, "closed_because": None}))
    assert "NOT WATCHING" not in out
    assert "closed on purpose" not in out


def test_a_daemon_older_than_the_code_says_so(monkeypatch, capsys):
    """A daemon is as old as its last restart, and the code on disk moves.

    Six capabilities landed on 2026-08-25 and every one answered `unknown op`
    against the daemon that was up, with nothing anywhere saying a restart
    was all it needed. A person reads the docs, runs the command, gets an
    error, and concludes the feature is broken.
    """
    out = _run(monkeypatch, capsys, dict(
        BASE, organs={"eye": "alive"},
        eyelid={"eyes_open": True, "closed_because": None},
        ops=["ask", "stats", "night", "digest", "grade"]))
    assert "OLDER THAN THE CODE ON DISK" in out
    assert "follow a citation to what it saw" in out
    assert "./trace stop && ./trace start" in out


def test_a_current_daemon_says_nothing_about_restarting(monkeypatch, capsys):
    """The nag must disappear the moment it is untrue, or it becomes another
    alarm people learn to scroll past."""
    from tracemem.app.ask_socket import AskSocket

    out = _run(monkeypatch, capsys, dict(
        BASE, organs={"eye": "alive"},
        eyelid={"eyes_open": True, "closed_because": None},
        ops=sorted(AskSocket.OPS)))
    assert "OLDER THAN THE CODE" not in out


def test_the_op_list_is_generated_not_hand_kept():
    """A new op that forgets to join the list would make the face advertise
    a capability the daemon does not serve — the opposite failure."""
    import inspect

    from tracemem.app import ask_socket

    src = inspect.getsource(ask_socket)
    for op in ask_socket.AskSocket.OPS:
        assert 'op == "%s"' % op in src or op in ("ask", "stats"), \
            "OPS advertises %r and no handler answers it" % op
