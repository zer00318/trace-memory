"""OWNS: the face — a menubar item that says whether TRACE is watching, and
one click that turns it on (tree: 08_running > The menubar > Status item).

WHY A MENUBAR ITEM AND NOT A WINDOW. The napkin asks for "pause / resume /
status at one glance", and glance is the operative word: this is a thing that
runs all day and is looked at for one second at a time. A window you have to
find and open is a window that answers "is it watching?" only for people who
already suspected it wasn't.

WHY THIS IS NOT COSMETIC. The founder's eye has been blind for hours because
macOS grants Screen Recording to an APPLICATION IDENTITY, and TRACE had none —
just a Python binary that launchd happened to run. Every state that mattered
was true and invisible: the daemon was up, the socket answered, the store grew
(with witnesses about not seeing), and nothing anywhere said the word BLIND.
A face is where that stops being discoverable only by forensics.

THREE STATES, AND THEY ARE NOT THE SAME:

    ● watching   frames are landing
    ⚠ blind      the daemon is up and perceiving NOTHING — the state that
                 looked like health for hours
    ○ off        no daemon

"Running" and "watching" are different facts, and conflating them is exactly
the failure this product exists to refuse. The title shows which.

IT IS NOT THE DAEMON. It starts and stops the daemon through the same
`./trace` front door a person would use, and reads state through the ask
socket. A face that captured would be a second writer to a store whose whole
integrity argument is that it has one.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TRACE = ROOT / "trace"
SOCK = Path.home() / ".trace" / "ask.sock"

POLL_SECONDS = 5.0

# The macOS pane that grants Screen Recording. Opening it directly matters
# more than it looks: the founder's blocker is one switch inside a settings
# app with dozens of panes, and "go and find it" is where non-technical
# people stop.
SCREEN_RECORDING_PANE = (
    "x-apple.systempreferences:com.apple.preference.security"
    "?Privacy_ScreenCapture")

WATCHING, BLIND, OFF = "watching", "blind", "off"
_TITLE = {WATCHING: "TRACE ●", BLIND: "TRACE ⚠", OFF: "TRACE ○"}


def ask_daemon(op: str = "stats", timeout: float = 8.0, **kw):
    """One request to the running daemon, or None if there is not one."""
    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(timeout)
        c.connect(str(SOCK))
        c.sendall((json.dumps(dict({"op": op}, **kw)) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = c.recv(8192)
            if not chunk:
                return None
            buf += chunk
        c.close()
        return json.loads(buf.split(b"\n")[0].decode("utf-8", "replace"))
    except Exception:                                    # noqa: BLE001
        return None


def read_state(stats) -> str:
    """WATCHING / BLIND / OFF from one stats reply.

    A daemon that is up is not a daemon that is seeing. `organs.eye` is the
    only thing that knows the difference, and while the store is still binding
    nobody knows yet — which reads as blind rather than as watching, because
    the safe error here is to under-claim sight.
    """
    if not stats or not stats.get("ok"):
        return OFF
    if stats.get("waking"):
        return BLIND
    eye = (stats.get("organs") or {}).get("eye")
    return WATCHING if eye == "alive" else BLIND


def eye_problem(stats) -> str:
    """The sentence to show a person about why nothing is being seen."""
    if not stats:
        return ""
    if stats.get("waking"):
        total = stats.get("total") or 0
        if stats.get("phase") == "search index":
            return "Waking up — preparing the private search index."
        return ("Waking up — reading %s of %s memories back in."
                % (f"{stats.get('bound') or 0:,}", f"{total:,}"))
    organs = stats.get("organs") or {}
    if organs.get("eye") == "alive":
        return ""
    detail = (organs.get("eye_detail") or {}).get("last_error") or ""
    if any(t in detail for t in ("-3801", "TCC", "Screen Recording")):
        return ("macOS is refusing Screen Recording. TRACE is running and "
                "seeing nothing.")
    if detail:
        return "The eye cannot start: %s" % detail[:120]
    return "The eye is not watching."


LABEL = "com.trace.watcher"
APP_HELPER = Path("/Applications/TRACE.app/Contents/MacOS/TRACE")


def cold_start_from_picker(runner=None, helper=None):
    """Run the signed folder chooser without ever creating a second writer.

    Returns ``(state, message)`` where state is complete, cancelled, or
    failed.  The chooser itself belongs to the signed native helper: this
    function deliberately cannot accept a path, so the face cannot bypass
    the person's visible NSOpenPanel consent.

    The launch agent is restored only if it was loaded before this operation.
    That preserves an intentional pause while still making the normal
    watching -> import -> watching path one safe action.
    """
    run = runner or subprocess.run
    signed_helper = APP_HELPER if helper is None else Path(helper)
    if (not signed_helper.is_file() or
            not os.access(str(signed_helper), os.X_OK)):
        return ("failed", "The signed TRACE app is not installed. Reinstall "
                "TRACE, then try again.")

    user_domain = "gui/%d" % os.getuid()
    service = "%s/%s" % (user_domain, LABEL)
    plist = Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")
    loaded = run(["launchctl", "print", service],
                 capture_output=True).returncode == 0
    stopped = False
    restart_problem = ""
    outcome = ("failed", "TRACE did not start the signed folder reader.")
    try:
        if loaded:
            result = run(["launchctl", "bootout", service],
                         capture_output=True, text=True)
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "unknown error").strip()
                return ("failed", "TRACE could not pause its writer safely, so "
                        "no folder was read: %s" % detail[:240])
            stopped = True

        try:
            result = run([str(signed_helper), "--cold-start"],
                         capture_output=True, text=True)
        except Exception as exc:                         # noqa: BLE001
            outcome = ("failed", "TRACE could not start the signed folder "
                       "reader: %s" % exc)
        else:
            output = ((result.stdout or "") + "\n" +
                      (result.stderr or "")).strip()
            if result.returncode == 0:
                outcome = ("complete", output or
                           "Your selected folder is now part of TRACE's memory.")
            elif "cold start cancelled" in output.lower():
                outcome = ("cancelled", "Cancelled. No folder was read and no "
                           "memory was changed.")
            else:
                outcome = ("failed", "TRACE could not finish reading that folder:\n\n"
                           + (output[-1200:] or
                              "the signed reader exited unexpectedly"))
    finally:
        if loaded and stopped:
            boot = run(["launchctl", "bootstrap", user_domain, str(plist)],
                       capture_output=True, text=True)
            kick = run(["launchctl", "kickstart", "-k", service],
                       capture_output=True, text=True) if boot.returncode == 0 else None
            if boot.returncode != 0 or (kick and kick.returncode != 0):
                failed = boot if boot.returncode != 0 else kick
                restart_problem = ((failed.stderr or failed.stdout or
                                    "unknown launchd error").strip())[:240]

    if restart_problem:
        return ("failed", "%s\n\nThe folder operation ended, but TRACE could "
                "not resume watching: %s" % (outcome[1], restart_problem))
    if loaded:
        return (outcome[0], outcome[1] +
                "\n\nTRACE has resumed watching automatically.")
    return outcome


def scoped_key_ready(runner=None, helper=None) -> bool:
    """Whether the installed helper can read without any UI interaction."""
    run = runner or subprocess.run
    signed_helper = APP_HELPER if helper is None else Path(helper)
    if not signed_helper.is_file():
        return False
    try:
        result = run([str(signed_helper), "--probe-key"],
                     capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception:                                    # noqa: BLE001
        return False


def renew_scoped_key_access(runner=None, helper=None):
    """Ask macOS visibly to trust this signed build, without moving the key."""
    run = runner or subprocess.run
    signed_helper = APP_HELPER if helper is None else Path(helper)
    if not signed_helper.is_file():
        return False, "The signed TRACE app is not installed."
    try:
        result = run([str(signed_helper), "--renew-key-access"],
                     capture_output=True, text=True)
    except Exception as exc:                             # noqa: BLE001
        return False, str(exc)
    output = ((result.stdout or "") + "\n" +
              (result.stderr or "")).strip()
    if result.returncode == 0:
        return True, "Memory access is authorized for this signed TRACE build."
    return False, (output[-800:] or "macOS did not authorize memory access")


def _run(*args) -> None:
    """Start or stop the daemon.

    PREFERS `./trace`, the same front door a person would type — but falls
    back to launchctl when the repo is unreachable. That is not hypothetical:
    TRACE.app cannot read ~/Documents, because macOS puts it behind TCC and an
    app bundle has no grant there. launchctl needs none: the agent plist lives
    in ~/Library/LaunchAgents, which is not protected.
    """
    action = args[0] if args else "status"
    if TRACE.exists() and os.access(str(TRACE), os.X_OK):
        try:
            subprocess.Popen([str(TRACE), *args],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return
        except Exception:                                # noqa: BLE001
            pass
    domain = "gui/%d/%s" % (os.getuid(), LABEL)
    plist = Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")
    if action == "stop":
        cmd = ["launchctl", "bootout", domain]
    elif plist.exists():
        # kickstart -k restarts if loaded, starts if merely bootstrapped.
        subprocess.run(["launchctl", "bootstrap", "gui/%d" % os.getuid(),
                        str(plist)], capture_output=True)
        cmd = ["launchctl", "kickstart", "-k", domain]
    else:
        return
    subprocess.run(cmd, capture_output=True)


def build_app():
    """Construct the status item. Imported lazily so this module can be
    tested on a machine with no window server."""
    import AppKit
    import objc
    from Foundation import NSObject, NSTimer

    class TraceMenu(NSObject):
        def init(self):
            self = objc.super(TraceMenu, self).init()
            if self is None:
                return None
            bar = AppKit.NSStatusBar.systemStatusBar()
            self.item = bar.statusItemWithLength_(AppKit.NSVariableStatusItemLength)
            self.item.button().setTitle_(_TITLE[OFF])
            self.menu = AppKit.NSMenu.alloc().init()
            self.item.setMenu_(self.menu)
            self.stats = None
            self.state = OFF
            self.refresh_()
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                POLL_SECONDS, self, "refresh:", None, True)
            return self

        # -- the poll ------------------------------------------------------
        def refresh_(self, _timer=None):
            self.stats = ask_daemon("stats")
            self.state = read_state(self.stats)
            self.item.button().setTitle_(_TITLE[self.state])
            self._rebuild()

        # PURE PYTHON, NOT A SELECTOR. PyObjC bridges every method on an
        # NSObject subclass into Objective-C and matches the argument count to
        # the underscores in the name, so a plain helper raises
        # BadPrototypeError at class-definition time. These are ours, not the
        # runtime's.
        @objc.python_method
        def _add(self, title, sel=None, key=""):
            it = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                title, sel, key)
            if sel is not None:
                it.setTarget_(self)
            else:
                it.setEnabled_(False)
            self.menu.addItem_(it)
            return it

        @objc.python_method
        def _rebuild(self):
            self.menu.removeAllItems()
            s = self.stats or {}

            if self.state == WATCHING:
                self._add("Watching your screen")
            elif self.state == OFF:
                self._add("Not running")
            else:
                self._add("NOT WATCHING")
            problem = eye_problem(s)
            if problem:
                self._add("   " + problem)

            if s.get("ok") and not s.get("waking"):
                self._add("   %s memories · %s seen this hour"
                          % (f"{s.get('bound') or 0:,}", seen_this_hour(s)))
            self.menu.addItem_(AppKit.NSMenuItem.separatorItem())

            # THE BUTTON HE ASKED FOR.
            if self.state == OFF:
                self._add("Turn TRACE on", "turnOn:")
            else:
                self._add("Turn TRACE off", "turnOff:")

            # Only offered when it is the actual problem — a permanent
            # "fix permissions" item trains people to ignore it.
            if "Screen Recording" in problem:
                self._add("Fix it: open Screen Recording settings…",
                          "openScreenRecording:")

            self.menu.addItem_(AppKit.NSMenuItem.separatorItem())
            self._add("Ask a question…", "askQuestion:")
            self._add("Today's digest…", "showDigest:")
            self._add("Open the cockpit", "openCockpit:")
            self.menu.addItem_(AppKit.NSMenuItem.separatorItem())
            self._add("Quit this menu (TRACE keeps running)", "quit:", "q")

        # -- actions -------------------------------------------------------
        def turnOn_(self, _s):
            _run("start")
            self.refresh_()

        def turnOff_(self, _s):
            _run("stop")
            self.refresh_()

        def openScreenRecording_(self, _s):
            AppKit.NSWorkspace.sharedWorkspace().openURL_(
                AppKit.NSURL.URLWithString_(SCREEN_RECORDING_PANE))
            _alert("After you switch TRACE on in that list",
                   "Come back here and choose Turn TRACE off, then Turn "
                   "TRACE on. macOS only applies the new permission to a "
                   "freshly started program.")

        def askQuestion_(self, _s):
            q = _prompt("Ask your memory", "e.g. what was I reading earlier?")
            if not q:
                return
            r = ask_daemon("ask", timeout=300.0, question=q)
            if r is None:
                _alert("TRACE is not running", "Turn it on first.")
            elif not r.get("ok"):
                _alert("Could not answer", str(r.get("error"))[:400])
            elif r.get("abstained"):
                _alert("Not in memory",
                       "TRACE has no record that answers that. It will not "
                       "guess.")
            else:
                _alert(q, r.get("text", ""))

        def showDigest_(self, _s):
            r = ask_daemon("digest", timeout=300.0, hours=24)
            if r is None:
                _alert("TRACE is not running", "Turn it on first.")
            elif not r.get("ok"):
                _alert("No digest", str(r.get("error"))[:400])
            else:
                _alert("What TRACE saw and thinks", r.get("text", "")[:4000])

        def openCockpit_(self, _s):
            subprocess.Popen(
                [sys.executable, str(ROOT / "tools" / "cockpit.py")],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            AppKit.NSWorkspace.sharedWorkspace().openURL_(
                AppKit.NSURL.URLWithString_("http://localhost:4242"))

        def quit_(self, _s):
            AppKit.NSApplication.sharedApplication().terminate_(None)

    return TraceMenu


def seen_this_hour(stats) -> str:
    """Prefer the number the DAEMON reports; fall back to reading the store.

    The daemon can always see its own store; the face often cannot (macOS puts
    ~/Documents behind TCC and an app bundle has no grant). Asking the process
    that already has the file open is both cheaper and the only version that
    works when the face is a real application.
    """
    n = (stats or {}).get("seen_last_hour")
    if isinstance(n, int) and n >= 0:
        return f"{n:,}"
    return _seen_last_hour()


def _seen_last_hour() -> str:
    """Perceptions in the last hour, straight off the store.

    `bidder` is a plaintext column, so this reads correctly on the encrypted
    store with no key — and witnesses are excluded, because a count that rises
    while the daemon writes about its own blindness is the number that fooled
    everyone for hours.
    """
    import sqlite3
    import time
    store = ROOT / "data" / "trace_store.sqlite3"
    if not store.exists():
        return "?"
    since = int((time.time() - 3600) * 1000)
    for dsn, kw in ((f"file:{store}?mode=ro", {"uri": True}), (str(store), {})):
        try:
            con = sqlite3.connect(dsn, timeout=1.0, **kw)
        except Exception:                                # noqa: BLE001
            continue
        try:
            n = con.execute(
                "select count(*) from claim where t_ms > ? and "
                "bidder <> 'system:witness'", (since,)).fetchone()[0]
            return f"{n:,}"
        except Exception:                                # noqa: BLE001
            continue
        finally:
            con.close()
    return "?"


def _alert(title: str, body: str) -> None:
    import AppKit
    a = AppKit.NSAlert.alloc().init()
    a.setMessageText_(title[:120])
    a.setInformativeText_(body or "")
    a.runModal()


def _prompt(title: str, placeholder: str) -> str:
    import AppKit
    a = AppKit.NSAlert.alloc().init()
    a.setMessageText_(title)
    a.addButtonWithTitle_("Ask")
    a.addButtonWithTitle_("Cancel")
    field = AppKit.NSTextField.alloc().initWithFrame_(((0, 0), (320, 24)))
    field.setPlaceholderString_(placeholder)
    a.setAccessoryView_(field)
    a.window().setInitialFirstResponder_(field)
    if a.runModal() != AppKit.NSAlertFirstButtonReturn:
        return ""
    return str(field.stringValue()).strip()


def main() -> int:
    import AppKit
    app = AppKit.NSApplication.sharedApplication()
    # Accessory: a menubar item with no Dock icon and no window. This is a
    # thing that lives in the corner of the screen, not an app you switch to.
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    TraceMenu = build_app()
    menu = TraceMenu.alloc().init()
    app.setDelegate_(menu)
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
