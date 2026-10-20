#!/usr/bin/env python3
"""INSTALL / UNINSTALL the launchd agent — TRACE without a terminal.

    ./trace install      register the agent; it starts now and at every login
    ./trace uninstall    remove it and leave the machine as found

WHY THIS IS ITS OWN FILE AND NOT A `make` TARGET: the plist is GENERATED, not
copied. The one checked into `resources/` carries the founder's own absolute
paths, and a path checked into a repo is documentation — the moment anyone
else runs this, or the repo moves, a copied plist points at a Python that is
not there and launchd fails silently at login. So the paths are read off the
running installation and written fresh.

WHAT "LEAVE THE MACHINE AS FOUND" MEANS, precisely: uninstall bootouts the
agent, removes the plist from ~/Library/LaunchAgents, and stops the running
daemon. It does NOT touch the store. Someone uninstalling the app is asking
for the app to stop, not for their memory to be erased, and the two must
never be the same command.
"""
from __future__ import annotations

import plistlib
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LABEL = "com.trace.watcher"
AGENT_DIR = Path.home() / "Library" / "LaunchAgents"
AGENT = AGENT_DIR / ("%s.plist" % LABEL)
TEMPLATE = ROOT / "resources" / "com.trace.watcher.plist"
LOG_DIR = Path.home() / "Library" / "Logs" / "TRACE"


def _domain() -> str:
    import os
    return "gui/%d" % os.getuid()


INSTALLED_APP = Path("/Applications/TRACE.app")
HELPER = INSTALLED_APP / "Contents" / "MacOS" / "TRACE"


def _verify_installed_app() -> None:
    """Refuse to point launchd at a helper that is missing or unsealed."""
    if not HELPER.exists():
        raise SystemExit(
            "no signed helper at %s — build and install TRACE.app first" % HELPER)
    verified = subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", str(INSTALLED_APP)],
        capture_output=True, text=True)
    if verified.returncode != 0:
        raise SystemExit("installed TRACE.app signature is invalid: %s" %
                         verified.stderr.strip())


def build_plist(root: Path = ROOT, *, verify_app: bool = True) -> dict:
    """The agent, aimed at THIS installation.

    Every law is inherited from the checked-in template rather than retyped:
    if someone changes KeepAlive or ThrottleInterval there, the installed
    agent changes with it, and `tests/unit/test_plist.py` guards the template.
    Retyping them here would create a second source of truth that drifts.

    `verify_app=False` exists for the tests of WHAT is generated: whether the
    paths are this machine's and the laws are inherited is a property of the
    generator, and must not depend on whether the machine running the suite
    happens to have TRACE.app installed (it made four tests red on any
    machine without it, 2026-10-07). Installing always verifies.
    """
    data = plistlib.loads(TEMPLATE.read_bytes())
    if verify_app:
        _verify_installed_app()
    data["ProgramArguments"] = [str(HELPER), "--daemon"]
    # LOGS LIVE WHERE macOS KEEPS LOGS, not in /tmp (2026-10-09). The
    # template said /tmp/trace_watcher*.log; macOS purges /tmp, the files
    # were unlinked under the running daemon, and when the night stuck and
    # the model server died there was no record of either. launchd does not
    # expand "~", so the template's paths are made absolute here.
    for key in ("StandardOutPath", "StandardErrorPath"):
        if str(data.get(key, "")).startswith("~/"):
            data[key] = str(Path.home() / data[key][2:])
    data["WorkingDirectory"] = str(root)
    return data


def _launchctl(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


_RUNTIME_MARKER = "TRACE.app/Contents/Resources/runtime/entry.py --daemon"


def _stop_orphaned_runtime(timeout_s: float = 15.0) -> bool:
    """Stop a daemon child left behind by a pre-forwarding native helper.

    New helpers forward launchd's SIGTERM. This bounded cleanup is the
    migration path for already-running older helpers; it targets only the
    sealed runtime command.  A daemon wedged inside a bounded read may not
    reach Python's SIGTERM flag, so after the clean-stop grace period the
    installer uses SIGKILL — the same last resort as ``./trace stop``.  The
    append log is crash-tested for this exact failure.  We still refuse to
    bootstrap if that exact process remains, so two writers are impossible.
    """
    def pids():
        result = subprocess.run(["ps", "-axo", "pid=,command="],
                                capture_output=True, text=True)
        out = []
        for line in result.stdout.splitlines():
            pid_text, _, command = line.strip().partition(" ")
            if (_RUNTIME_MARKER in command and pid_text.isdigit()
                    and int(pid_text) != os.getpid()):
                out.append(int(pid_text))
        return out

    found = pids()
    for pid in found:
        try:
            os.kill(pid, 15)  # SIGTERM: Python records a clean stop
        except ProcessLookupError:
            pass
    deadline = time.time() + timeout_s
    while found and time.time() < deadline:
        time.sleep(0.1)
        found = pids()
    if found:
        print("prior daemon did not reach a clean stop; forcing the sealed "
              "runtime to release the one-writer lock: %s"
              % ", ".join(map(str, found)), file=sys.stderr)
        for pid in found:
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass
        force_deadline = time.time() + 5.0
        while found and time.time() < force_deadline:
            time.sleep(0.1)
            found = pids()
    if found:
        print("refusing to start a second writer; prior daemon survived stop: %s"
              % ", ".join(map(str, found)), file=sys.stderr)
        return False
    return True


def install(root: Path = ROOT) -> int:
    data = build_plist(root)
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # Boot out any previous copy first. `bootstrap` on an already-loaded label
    # fails with a bare "Input/output error" that names nothing a person can
    # act on, and the second install of the day is the common case.
    _launchctl("bootout", "%s/%s" % (_domain(), LABEL))
    if not _stop_orphaned_runtime():
        return 1
    AGENT.write_bytes(plistlib.dumps(data))
    # A persistent disabled override (from an older `unload -w`, or the
    # login-item switch) makes an otherwise valid bootstrap fail with opaque
    # error 5. Install is explicit consent to enable this exact label.
    _launchctl("enable", "%s/%s" % (_domain(), LABEL))
    r = _launchctl("bootstrap", _domain(), str(AGENT))
    if r.returncode != 0:
        print("launchctl bootstrap failed: %s%s"
              % (r.stderr.strip(), r.stdout.strip()), file=sys.stderr)
        return 1
    print("installed: %s" % AGENT)
    print("  runs: %s" % " ".join(data["ProgramArguments"]))
    print("  logs: %s" % data.get("StandardErrorPath"))
    return _verify_it_can_actually_see()


def _verify_it_can_actually_see(timeout_s: float = 240.0) -> int:
    """Watch the daemon we just installed until it says whether it can SEE.

    THIS EXISTS BECAUSE INSTALLING BLINDED THE FOUNDER'S EYE and told him it
    was working. The old last line was "TRACE now starts itself at login. It
    is already running." — true, and the most misleading sentence available,
    because the thing it started could not capture a single frame.

    macOS grants Screen Recording to the RESPONSIBLE PROCESS. Running from a
    terminal, that was the terminal, which had been granted. launchd spawns
    the interpreter directly, so the grant does not carry, SCShareableContent
    answers TCC -3801, and the eye is dark from the first second. Every other
    signal stayed green: the daemon ran, the socket answered, the store grew
    with witnesses, the night even started thinking (because a dead eye frees
    the GPU). Hours of nothing, reported as success.

    So this does not PREDICT whether the grant will carry — predicting TCC
    from outside is guesswork. It waits and LOOKS, which is the only thing
    that can be trusted, and refuses to print "it works" until the daemon
    itself says the eye is alive.
    """
    import json
    import socket as _socket
    import time

    sock = Path.home() / ".trace" / "ask.sock"
    deadline = time.time() + timeout_s

    def _stats():
        try:
            c = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
            c.settimeout(15)
            c.connect(str(sock))
            c.sendall(b'{"op":"stats"}\n')
            buf = b""
            while b"\n" not in buf:
                chunk = c.recv(4096)
                if not chunk:
                    return None
                buf += chunk
            c.close()
            return json.loads(buf.split(b"\n")[0].decode("utf-8", "replace"))
        except Exception:                                # noqa: BLE001
            return None

    print("\nchecking that it can actually SEE (this takes a minute — it "
          "binds\neverything already in your store before the eye starts)...")
    last_note = None
    while time.time() < deadline:
        st = _stats()
        if st is None:
            time.sleep(3)
            continue
        if st.get("waking"):
            note = "  binding %s of %s" % (st.get("bound"), st.get("total"))
            if note != last_note:
                print(note)
                last_note = note
            time.sleep(5)
            continue
        eye = (st.get("organs") or {}).get("eye")
        detail = ((st.get("organs") or {}).get("eye_detail") or {}
                  ).get("last_error") or ""
        if eye == "alive":
            print("\nTRACE starts itself at login, and the eye IS watching.")
            return 0
        if eye == "parked" and not detail:
            reason = (st.get("eyelid") or {}).get("closed_because") or "idle"
            print("\nTRACE starts itself at login. The eye started correctly "
                  "and is now closed on purpose (%s); it reopens by itself."
                  % reason)
            return 0
        if eye is None:
            time.sleep(3)
            continue
        described_state = ("permission denied before parking"
                           if eye == "parked" and detail else eye)
        print("\n!! INSTALLED, BUT THE EYE IS NOT WATCHING (state: %s)"
              % described_state)
        if "-3801" in detail or "TCC" in detail or "Screen Recording" in detail:
            print("""
macOS gives Screen Recording to whoever STARTS the program. You granted it
to your terminal; now that TRACE is started by macOS itself at login, that
permission does not carry over, and the eye is being refused silently.

Fix it once for the signed product:
  1. System Settings > Privacy & Security > Screen Recording
  2. switch ON TRACE (press + and choose this app if it is absent):
     /Applications/TRACE.app
  3. then run:  ./trace stop && ./trace start

Until then TRACE is running and remembering nothing it sees.""")
        elif detail:
            print("   reason: %s" % detail[:200])
        print("\nCheck again any time with:  ./trace status")
        return 1

    print("\n!! could not confirm the eye within %ds. Check:  ./trace status"
          % int(timeout_s))
    return 1


def uninstall() -> int:
    _launchctl("bootout", "%s/%s" % (_domain(), LABEL))
    existed = AGENT.exists()
    if existed:
        AGENT.unlink()
    # The running process too — bootout stops what launchd started, but a
    # daemon started by hand from the terminal is not launchd's to stop.
    subprocess.run(["pkill", "-f",
                    "TRACE.app/Contents/Resources/runtime/entry.py --daemon"],
                   capture_output=True)
    subprocess.run(["pkill", "-f",
                    "/Applications/TRACE.app/Contents/MacOS/TRACE --daemon"],
                   capture_output=True)
    print("uninstalled" if existed else "was not installed")
    print("YOUR MEMORY IS UNTOUCHED — the store stays at data/. Uninstalling "
          "asks the app to stop, never for your memory to be erased.")
    return 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    action = argv[0] if argv else "install"
    if action == "install":
        return install()
    if action == "uninstall":
        return uninstall()
    print("usage: install_agent.py [install|uninstall]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
