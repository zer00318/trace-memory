"""THE ONE TRUE ALARM — is the memory actually seeing anything?

WHY THIS EXISTS. Capture died 2026-08-05 06:29 and nobody knew for sixteen
days. Two mechanisms hid it, and this file is aimed at both:

  · The status number could not fall. `./trace status` printed a CUMULATIVE
    claim count, so a dead eye and a healthy one print the same rising
    number. Here the only number that matters is an AGE, which rises exactly
    when something is wrong.
  · The alarm channel was 100% false positives. The VLM-era watchdog fired
    five DOWN lines every 120 seconds about services decommissioned on
    purpose in July, so the one true alarm on 2026-08-05 arrived in a channel
    nobody could still read. This file therefore alarms on ONE condition and
    stays silent otherwise; a watchman that cries every two minutes is worse
    than no watchman, which is the measured lesson, not an opinion.

THE CONDITION, and why each clause is load-bearing:

    blind := no NON-WITNESS claim for BLIND_AFTER_S
             AND the founder is at the machine (idle < AWAKE_IDLE_S)

`bidder <> 'system:witness'` is the whole trick: witness claims are the
daemon's own heartbeat, so a store where only witnesses land is a store whose
senses are dead — and that is precisely the shape the outage had.

The idle clause is what keeps this quiet and therefore believable. F-EYELID
closes the eye after 15 minutes of no input, by founder ruling, so silence
while away is CORRECT behaviour and must never alarm. AWAKE_IDLE_S is set
below that threshold on purpose: if the founder has touched the machine in
the last ten minutes, the screen is changing and claims must be landing.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "trace_store.sqlite3"
STATE = Path.home() / ".trace" / "blind_alarm.json"

# No claim for this long while the founder is present = blind.
BLIND_AFTER_S = 30 * 60
# "Present" means input within this long. Below F-EYELID's 15-minute close,
# so a legitimately closed eye can never trip this.
AWAKE_IDLE_S = 10 * 60
# Never notify more than once per this interval for the same fault. The
# alarm must survive being ignored for a day without becoming noise.
RENOTIFY_S = 60 * 60


def idle_seconds() -> float:
    """Seconds since the last human input, or -1 if it cannot be read.

    Unreadable idle is treated as PRESENT by the caller (fail loud), because
    the failure this file exists to catch is silence being mistaken for
    health.
    """
    try:
        out = subprocess.run(
            ["ioreg", "-c", "IOHIDSystem", "-d", "4", "-r"],
            capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return -1.0
    for line in out.splitlines():
        if "HIDIdleTime" in line:
            try:
                return int(line.split("=")[-1].strip()) / 1e9
            except ValueError:
                return -1.0
    return -1.0


def daemon_running() -> bool:
    return subprocess.run(["pgrep", "-f", "tracemem --eye"],
                          capture_output=True).returncode == 0


def last_perception_ms(store: Path):
    """When a SENSE last wrote. Witnesses excluded — see module docstring."""
    if not store.exists():
        return None
    db = sqlite3.connect("file:%s?mode=ro" % store, uri=True)
    try:
        row = db.execute(
            "SELECT MAX(t_ms) FROM claim WHERE bidder <> 'system:witness'"
        ).fetchone()
    finally:
        db.close()
    return row[0] if row and row[0] is not None else None


def notify(title: str, message: str) -> None:
    """A macOS notification. Local only — the no-telemetry refusal stands."""
    try:
        subprocess.run(
            ["osascript", "-e",
             'display notification %s with title %s sound name "Basso"'
             % (json.dumps(message), json.dumps(title))],
            capture_output=True, timeout=10)
    except Exception:
        pass


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1))


def _screen_recording_denied() -> bool:
    """Is the eye dark because macOS took the permission away?

    THE ALARM KNEW SOMETHING WAS WRONG AND NOT WHAT TO DO ABOUT IT. Both
    multi-day blackouts had the same cause and the notification never named
    it: Screen Recording is granted to a BINARY, this venv runs on Xcode's
    bundled Python, and updating Xcode changes that binary — so the grant
    evaporates, the daemon stays up, `alive()` keeps saying True, and the
    only symptom is claims that stop arriving.

    `app/permissions` has read the real system call since July and had no
    production caller. It does now: an alarm that names the cause is a
    different object from one that reports a symptom, because the second
    needs somebody to go and investigate and the first can be acted on from
    the notification banner.

    Read defensively — an alarm must never fail because its explanation did.
    """
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from tracemem.app.permissions import Status, _read_screen_recording
        return _read_screen_recording() is Status.DENIED
    except Exception:                                         # noqa: BLE001
        return False


def _how_long(age_s: float) -> str:
    """A duration a person can act on. THIRTY-ONE MINUTES AND TWO DAYS MUST
    NOT READ THE SAME — the last message this alarm sent said 'the eye is
    running and not seeing' on hour one and on hour forty-nine, in the same
    words, and the second one is a different emergency."""
    if age_s < 5400:
        return "%.0f minutes" % (age_s / 60.0)
    if age_s < 172800:
        return "%.1f hours" % (age_s / 3600.0)
    return "%.1f DAYS" % (age_s / 86400.0)


# What to do about it, in the order a person would do it. Kept next to the
# alarm rather than in a document, because the moment somebody reads this
# sentence is the moment they can fix it.
FIX_SCREEN_RECORDING = (
    "macOS has taken Screen Recording away from the program running TRACE. "
    "Open %s, press 'Fix permission…', switch TRACE on, then turn TRACE off "
    "and on again — after that the grant belongs to TRACE itself rather than "
    "to whatever launched it." % (ROOT / "dist" / "TRACE.app"))


def check(store: Path = STORE, now: float = None) -> dict:
    """The verdict. Pure enough to test: everything it reads is injected or
    read once, and it returns the reason rather than printing it."""
    now = time.time() if now is None else now
    last = last_perception_ms(store)
    idle = idle_seconds()
    running = daemon_running()
    age_s = None if last is None else now - last / 1000.0
    present = idle < 0 or idle < AWAKE_IDLE_S

    if not running:
        return {"ok": False, "fault": "not-running", "age_s": age_s,
                "idle_s": idle,
                "detail": "no `tracemem --eye` process — TRACE is not watching"}
    if last is None:
        return {"ok": False, "fault": "empty-store", "age_s": None,
                "idle_s": idle, "detail": "the store holds no perception"}
    if age_s > BLIND_AFTER_S and present:
        denied = _screen_recording_denied()
        detail = ("nothing seen for %s while you were at the machine — the "
                  "eye is running and not seeing" % _how_long(age_s))
        if denied:
            detail = ("nothing seen for %s. SCREEN RECORDING IS DENIED to "
                      "the program running TRACE." % _how_long(age_s))
        return {"ok": False, "fault": "blind", "age_s": age_s, "idle_s": idle,
                "permission_denied": denied,
                "detail": detail,
                "fix": FIX_SCREEN_RECORDING if denied else ""}
    return {"ok": True, "fault": None, "age_s": age_s, "idle_s": idle,
            "detail": "last saw something %.1f min ago" % (age_s / 60.0)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default=str(STORE))
    ap.add_argument("--quiet", action="store_true",
                    help="exit code only; no notification, no print")
    args = ap.parse_args(argv)

    v = check(Path(args.store))
    line = "%s  %s  %s" % (
        time.strftime("%Y-%m-%d %H:%M:%S"),
        "OK   " if v["ok"] else "BLIND",
        v["detail"])

    if not args.quiet:
        print(line, flush=True)
        if v.get("fix"):
            print("       FIX: %s" % v["fix"], flush=True)

    state = _load_state()
    if v["ok"]:
        # Recovery is worth one notification: it closes the loop for a person
        # who saw the alarm and acted.
        if state.get("fault") and not args.quiet:
            notify("TRACE is watching again", v["detail"])
        _save_state({"fault": None, "at": time.time()})
        return 0

    last_notified = state.get("notified_at", 0)
    same_fault = state.get("fault") == v["fault"]
    if not args.quiet and (not same_fault
                           or time.time() - last_notified > RENOTIFY_S):
        # The banner carries the REMEDY when one is known. A notification
        # that only reports a symptom is a notification that needs an
        # investigation before anything can happen, and an investigation is
        # exactly what does not happen on a Tuesday.
        notify("TRACE IS NOT SEEING",
               v["detail"] + (("  " + v["fix"]) if v.get("fix") else ""))
        state["notified_at"] = time.time()
    state["fault"] = v["fault"]
    state["at"] = time.time()
    _save_state(state)
    return 1


if __name__ == "__main__":
    sys.exit(main())
