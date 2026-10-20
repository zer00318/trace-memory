"""OWNS: TRACE.app — the actual application window.

Not a menubar item. A thing you double-click, with a window, that says in
large letters whether your memory is being recorded and has a button that
turns it on.

WHY THE BUNDLE IS THE POINT, not decoration. macOS attributes Screen
Recording to the RESPONSIBLE PROCESS, and until now TRACE had no application
identity at all — just a Python binary that a terminal, or launchd, happened
to run. So the permission was granted to whatever started it and evaporated
the moment anything else did. The founder's eye was blind for hours because
of exactly this. A signed .app bundle is a stable identity macOS can hold a
grant against, and a daemon launched BY that app inherits it as the
responsible parent. The window is what he asked for; the bundle is what makes
the permission stop moving.

WHAT THE WINDOW REFUSES TO DO: say "running" when it means "up". The headline
is derived from whether the eye organ is ALIVE and from nothing else, because
a process being up while perceiving nothing is the precise failure that hid
for hours behind every other surface.
"""
from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

# IMPORTS BOTH WAYS ON PURPOSE. In the repo this is tracemem.app.menubar;
# inside TRACE.app the same file sits flat beside this one, because the bundle
# cannot import from ~/Documents — macOS puts that behind TCC.
try:
    from tracemem.app.menubar import (BLIND, OFF, ROOT, SCREEN_RECORDING_PANE,
                                      TRACE, WATCHING, _run, ask_daemon,
                                      cold_start_from_picker, eye_problem,
                                      read_state, renew_scoped_key_access,
                                      scoped_key_ready, seen_this_hour)
except ImportError:                                      # bundled
    from menubar import (BLIND, OFF, ROOT, SCREEN_RECORDING_PANE,  # noqa: F401
                         TRACE, WATCHING, _run, ask_daemon, eye_problem,
                         cold_start_from_picker, read_state,
                         renew_scoped_key_access, scoped_key_ready,
                         seen_this_hour)

POLL_SECONDS = 3.0

_HEADLINE = {
    WATCHING: ("TRACE is remembering", (0.24, 0.72, 0.31)),
    BLIND:    ("TRACE is NOT remembering", (0.97, 0.32, 0.29)),
    OFF:      ("TRACE is off", (0.55, 0.58, 0.61)),
}


def build():
    import AppKit
    import objc
    from Foundation import NSMakeRect, NSObject, NSTimer

    W, H = 560, 620

    def label(frame, size=13, bold=False, color=None):
        f = AppKit.NSTextField.alloc().initWithFrame_(frame)
        f.setBezeled_(False)
        f.setDrawsBackground_(False)
        f.setEditable_(False)
        f.setSelectable_(True)
        f.setFont_(AppKit.NSFont.boldSystemFontOfSize_(size) if bold
                   else AppKit.NSFont.systemFontOfSize_(size))
        if color:
            f.setTextColor_(AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                *color, 1.0))
        return f

    class TraceWindow(NSObject):
        def init(self):
            self = objc.super(TraceWindow, self).init()
            if self is None:
                return None
            self.stats = None
            self.state = OFF

            style = (AppKit.NSWindowStyleMaskTitled
                     | AppKit.NSWindowStyleMaskClosable
                     | AppKit.NSWindowStyleMaskMiniaturizable)
            self.win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, W, H), style, AppKit.NSBackingStoreBuffered, False)
            self.win.setTitle_("TRACE")
            self.win.center()
            self.win.setReleasedWhenClosed_(False)
            v = self.win.contentView()

            y = H - 70
            self.headline = label(NSMakeRect(24, y, W - 48, 34), size=25, bold=True)
            v.addSubview_(self.headline)

            y -= 30
            self.why = label(NSMakeRect(24, y, W - 48, 22), size=12)
            self.why.setTextColor_(AppKit.NSColor.secondaryLabelColor())
            v.addSubview_(self.why)

            y -= 46
            self.button = AppKit.NSButton.alloc().initWithFrame_(
                NSMakeRect(24, y, 180, 34))
            self.button.setBezelStyle_(AppKit.NSBezelStyleRounded)
            self.button.setTarget_(self)
            self.button.setAction_("toggle:")
            self.button.setKeyEquivalent_("\r")
            v.addSubview_(self.button)

            self.fixbtn = AppKit.NSButton.alloc().initWithFrame_(
                NSMakeRect(214, y, 300, 34))
            self.fixbtn.setBezelStyle_(AppKit.NSBezelStyleRounded)
            self.fixbtn.setTitle_("Fix permission…")
            self.fixbtn.setTarget_(self)
            self.fixbtn.setAction_("fixProblem:")
            self.fixbtn.setHidden_(True)
            v.addSubview_(self.fixbtn)

            y -= 34
            self.counts = label(NSMakeRect(24, y, W - 230, 20), size=12)
            self.counts.setTextColor_(AppKit.NSColor.secondaryLabelColor())
            v.addSubview_(self.counts)

            # THE INSTALL-DAY DOOR STAYS REACHABLE AFTER INSTALL DAY. A
            # cancelled folder picker must not turn the whole owned-past path
            # into a terminal-only feature. The signed helper presents the
            # consent panel; the Python face never receives or invents a path.
            self.coldbtn = AppKit.NSButton.alloc().initWithFrame_(
                NSMakeRect(W - 214, y - 5, 190, 30))
            self.coldbtn.setBezelStyle_(AppKit.NSBezelStyleRounded)
            self.coldbtn.setTitle_("Build memory from a folder…")
            self.coldbtn.setTarget_(self)
            self.coldbtn.setAction_("startColdStart:")
            v.addSubview_(self.coldbtn)
            self._cold_worker = None
            self._key_worker = None
            self._key_locked = False

            # -- ask --------------------------------------------------------
            y -= 44
            v.addSubview_(label(NSMakeRect(24, y, 200, 20), size=12, bold=True))
            v.subviews()[-1].setStringValue_("Ask your memory")

            y -= 32
            self.qfield = AppKit.NSTextField.alloc().initWithFrame_(
                NSMakeRect(24, y, W - 150, 26))
            self.qfield.setPlaceholderString_("what was I reading earlier?")
            self.qfield.setTarget_(self)
            self.qfield.setAction_("askQuestion:")
            v.addSubview_(self.qfield)

            ask = AppKit.NSButton.alloc().initWithFrame_(
                NSMakeRect(W - 120, y - 2, 96, 30))
            ask.setBezelStyle_(AppKit.NSBezelStyleRounded)
            ask.setTitle_("Ask")
            ask.setTarget_(self)
            ask.setAction_("askQuestion:")
            v.addSubview_(ask)

            # THE CORRECTION BUTTON. `Ask/'That's wrong'` was a recorded law
            # with no way to reach it: grading resolved ids against the last
            # DIGEST only, so a person could correct what the night reported
            # and not the answer in front of them. Hidden until there is an
            # answer with an id, because a correction button that fails on
            # press is worse than no button — the founder walks away believing
            # he has corrected something.
            self._gradeable = None
            self._wrongbtn = AppKit.NSButton.alloc().initWithFrame_(
                NSMakeRect(W - 232, y - 2, 104, 30))
            self._wrongbtn.setBezelStyle_(AppKit.NSBezelStyleRounded)
            self._wrongbtn.setTitle_("That's wrong")
            self._wrongbtn.setTarget_(self)
            self._wrongbtn.setAction_("markWrong:")
            self._wrongbtn.setHidden_(True)
            v.addSubview_(self._wrongbtn)

            # -- the scrolling answer / digest area --------------------------
            y -= 12
            scroll = AppKit.NSScrollView.alloc().initWithFrame_(
                NSMakeRect(24, 70, W - 48, y - 76))
            scroll.setHasVerticalScroller_(True)
            scroll.setBorderType_(AppKit.NSBezelBorder)
            self.text = AppKit.NSTextView.alloc().initWithFrame_(
                NSMakeRect(0, 0, W - 50, y - 78))
            self.text.setEditable_(False)
            self.text.setFont_(AppKit.NSFont.userFixedPitchFontOfSize_(11))
            scroll.setDocumentView_(self.text)
            v.addSubview_(scroll)

            # -- footer buttons ---------------------------------------------
            for i, (title, sel) in enumerate([
                    ("Digest", "showDigest:"),
                    ("Questions", "showHistory:"),
                    ("Live map", "showMap:"),
                    ("Settings", "showSettings:"),
                    ("Cockpit", "openCockpit:"),
                    ("Refresh", "refresh:")]):
                # Six compact actions across the footer.
                b = AppKit.NSButton.alloc().initWithFrame_(
                    NSMakeRect(17 + i * 88, 24, 84, 30))
                b.setBezelStyle_(AppKit.NSBezelStyleRounded)
                b.setTitle_(title)
                b.setTarget_(self)
                b.setAction_(sel)
                v.addSubview_(b)

            self.refresh_(None)
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                POLL_SECONDS, self, "refresh:", None, True)
            # BELT AND BRACES. applicationDidFinishLaunching is the correct
            # place to show a window, and it is not reliable here: the
            # interpreter re-execs itself through its own Python.app to become
            # a GUI process, and whether the delegate is installed before the
            # launch notification fires depends on that hand-off. A window
            # that exists, is the right size, and is never displayed is the
            # exact failure this app was built to stop happening elsewhere —
            # so it is shown from the run loop as well, where nothing can be
            # missed. Ordering an already-front window front again is a no-op.
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                0.3, self, "showWindow:", None, False)
            return self

        def showWindow_(self, _t):
            import AppKit
            self.win.makeKeyAndOrderFront_(None)
            AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

        # SHOWN FROM THE APP LIFECYCLE, NOT FROM init(). Ordering a window
        # front before NSApplication has finished launching leaves it created,
        # correctly sized, and never displayed — which is exactly what
        # happened: the process was up, `TRACE 560x648` existed in the window
        # list, and the screen showed nothing. AppKit puts a window on screen
        # when the app is ready, and applicationDidFinishLaunching is when it
        # says so.
        def applicationDidFinishLaunching_(self, _n):
            import AppKit
            self.win.makeKeyAndOrderFront_(None)
            AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

        def applicationShouldHandleReopen_hasVisibleWindows_(self, _a, visible):
            """Clicking the Dock icon of a running TRACE must bring the window
            back, not silently do nothing."""
            if not visible:
                self.win.makeKeyAndOrderFront_(None)
            return True

        # -- state ----------------------------------------------------------
        def refresh_(self, _t):
            import AppKit
            self.stats = ask_daemon("stats")
            self.state = read_state(self.stats)
            title, rgb = _HEADLINE[self.state]
            self.headline.setStringValue_(title)
            self.headline.setTextColor_(
                AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(*rgb, 1.0))
            why = eye_problem(self.stats)
            # A rebuilt executable may need one owner-approved Keychain ACL
            # renewal. Probe only while off: a live daemon already proves the
            # key, and spawning a helper every poll while watching is waste.
            self._key_locked = (self.state == OFF and not scoped_key_ready())
            if self._key_locked:
                why = ("macOS needs your approval before this updated TRACE "
                       "can reopen its memory.")
            self.why.setStringValue_(why)
            self.button.setTitle_(
                "Turn TRACE on" if self.state == OFF else "Turn TRACE off")
            if self._key_locked:
                self.fixbtn.setTitle_("Authorize memory…")
                self.fixbtn.setHidden_(False)
            else:
                self.fixbtn.setTitle_("Fix permission…")
                self.fixbtn.setHidden_("Screen Recording" not in why)
            s = self.stats or {}
            if s.get("ok") and not s.get("waking"):
                self.counts.setStringValue_(
                    "%s memories held · %s seen in the last hour · %s things it knows"
                    % (f"{s.get('bound') or 0:,}", seen_this_hour(s),
                       f"{s.get('nodes') or 0:,}"))
            else:
                self.counts.setStringValue_("")

        @objc.python_method
        def _say(self, text):
            self.text.setString_(text or "")

        # -- actions ----------------------------------------------------------
        def toggle_(self, _s):
            self._say("working…")
            _run("stop" if self.state != OFF else "start")
            self._say("TRACE is %s. This takes up to a minute while it reads "
                      "your memories back in."
                      % ("stopping" if self.state != OFF else "starting"))

        def fixProblem_(self, _s):
            if self._key_locked:
                self._authorizeMemory()
                return
            import AppKit
            AppKit.NSWorkspace.sharedWorkspace().openURL_(
                AppKit.NSURL.URLWithString_(SCREEN_RECORDING_PANE))
            self._say(
                "System Settings is now open at Screen Recording.\n\n"
                "1. Switch TRACE on in that list (if it is not listed, click +\n"
                "   and choose TRACE from Applications).\n"
                "2. Come back here and press 'Turn TRACE off', then\n"
                "   'Turn TRACE on'.\n\n"
                "macOS only applies a new permission to a freshly started\n"
                "program, which is why the restart is needed.")

        @objc.python_method
        def _authorizeMemory(self):
            if self._key_worker and self._key_worker.is_alive():
                return
            self.fixbtn.setEnabled_(False)
            self._say(
                "macOS may ask for your login password. Approve TRACE once; "
                "the memory key stays in Keychain and is never displayed, "
                "copied, or replaced.")
            self._key_worker = threading.Thread(
                target=self._runKeyRenewal, name="trace-key-renewal", daemon=True)
            self._key_worker.start()

        @objc.python_method
        def _runKeyRenewal(self):
            result = renew_scoped_key_access()
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "keyRenewalFinished:", result, False)

        def keyRenewalFinished_(self, result):
            ok, message = result
            self.fixbtn.setEnabled_(True)
            if ok:
                self._say(message + "\n\nTRACE is starting now.")
                _run("start")
            else:
                self._say("Memory access was not authorized. Nothing was "
                          "changed.\n\n" + message)
            self.refresh_(None)

        def startColdStart_(self, _s):
            """Let the signed app ask for a folder, off the AppKit thread."""
            if self._cold_worker and self._cold_worker.is_alive():
                return
            self.coldbtn.setEnabled_(False)
            self._say(
                "Choose one folder in the panel that opens. TRACE will pause "
                "watching while it reads, keep exact restart receipts, and "
                "resume automatically. Nothing is uploaded or copied.")
            self._cold_worker = threading.Thread(
                target=self._runColdStart, name="trace-cold-start", daemon=True)
            self._cold_worker.start()

        @objc.python_method
        def _runColdStart(self):
            result = cold_start_from_picker()
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "coldStartFinished:", result, False)

        def coldStartFinished_(self, result):
            state, message = result
            self.coldbtn.setEnabled_(True)
            if state == "complete":
                self._say("Folder memory is ready.\n\n" + message)
            elif state == "cancelled":
                self._say(message)
            else:
                self._say("Folder memory was not completed.\n\n" + message)
            self.refresh_(None)

        def askQuestion_(self, _s):
            q = str(self.qfield.stringValue()).strip()
            if not q:
                return
            self._say("thinking…")
            self._gradeable = None
            self._wrongbtn.setHidden_(True)
            r = ask_daemon("ask", timeout=300.0, question=q)
            if r is None:
                self._say("TRACE is not running. Turn it on first.")
            elif not r.get("ok"):
                self._say("Could not answer:\n\n%s" % r.get("error"))
            elif r.get("abstained"):
                self._say("(not in memory)\n\nTRACE has no record that answers "
                          "that, and it will not guess.")
            else:
                cites = r.get("citations") or []
                self._say("%s\n\n%s" % (
                    r.get("text", ""),
                    ("— from " + ", ".join(str(c)[:8] for c in cites))
                    if cites else ""))
                # OFFERED ONLY WHEN THERE IS SOMETHING TO GRADE. A "that's
                # wrong" button under an answer the daemon cannot name would
                # fail on press, and a correction button that fails is worse
                # than none: the founder believes he has corrected it.
                self._gradeable = r.get("line")
                self._wrongbtn.setHidden_(not self._gradeable)

        @objc.python_method
        def _changeSetting(self):
            """The values a person owns, changed from the screen that shows
            them.

            Four of these were read by NOBODY until 2026-08-25 — written to
            the file, printed here, and consulted by no part of the running
            product. Offering them from the screen is only honest now that
            changing one does something.
            """
            import AppKit
            api = self._settings_api()
            names = sorted(api.settable().items())
            alert = AppKit.NSAlert.alloc().init()
            alert.setMessageText_("Change a setting")
            alert.setInformativeText_(
                "\n".join("%s — %s" % (k, why) for k, why in names)
                + "\n\nType:  name value")
            field = AppKit.NSTextField.alloc().initWithFrame_(
                NSMakeRect(0, 0, 320, 24))
            field.setPlaceholderString_("eyelid_idle_minutes 45")
            alert.setAccessoryView_(field)
            alert.addButtonWithTitle_("Change it")
            alert.addButtonWithTitle_("Cancel")
            if alert.runModal() != AppKit.NSAlertFirstButtonReturn:
                return
            said = str(field.stringValue()).strip().split(None, 1)
            if len(said) < 2:
                self._say("Type a name and a value, like:\n\n"
                          "  eyelid_idle_minutes 45")
                return
            try:
                st = api.set_value(said[0], said[1])
            except Exception as e:                       # noqa: BLE001
                self._say("Could not change that:\n\n%s" % e)
                return
            self._say("%s\n\nChanged. The eye and the night read this live "
                      "— no restart." % api.describe(st))

        def markWrong_(self, _s):
            """Ask what the truth is, then record it as the founder's words.

            THE CORRECTION IS THE VALUABLE PART, not the verdict — it becomes
            the highest-graded statement in the store the moment it lands
            (app/founder.grade_claim). So this asks for it rather than
            recording a bare "wrong", and an empty box still records the
            verdict rather than losing the press.

            Nothing is edited and no threshold moves: the grade is appended
            as OWNED evidence and the night re-derives against it (F-GRADE —
            a founder answer is evidence, never a tuning knob).
            """
            import AppKit
            if not self._gradeable:
                return
            alert = AppKit.NSAlert.alloc().init()
            alert.setMessageText_("What is the truth?")
            alert.setInformativeText_(
                "One sentence. TRACE stores it in your words as the "
                "highest-graded thing it knows, and re-derives from it "
                "tonight. It never edits what it saw.")
            field = AppKit.NSTextField.alloc().initWithFrame_(
                NSMakeRect(0, 0, 320, 24))
            field.setPlaceholderString_("that was Tuesday, not Monday")
            alert.setAccessoryView_(field)
            alert.addButtonWithTitle_("Record")
            alert.addButtonWithTitle_("Cancel")
            if alert.runModal() != AppKit.NSAlertFirstButtonReturn:
                return
            r = ask_daemon("grade", timeout=60.0, line=self._gradeable,
                           verdict="wrong",
                           correction=str(field.stringValue()).strip())
            if r is None:
                self._say("TRACE is not running, so nothing was recorded.")
            elif not r.get("ok"):
                self._say("Could not record that: %s" % r.get("error"))
            else:
                # ECHOED BACK. A claim written in someone's name that they
                # never read is the start of a memory they cannot trust.
                self._say("Recorded, in your words:\n\n  %s\n\n"
                          "TRACE will re-derive from this tonight. It has not "
                          "edited anything it saw." % r.get("recorded"))
                self._gradeable = None
                self._wrongbtn.setHidden_(True)

        def showDigest_(self, _s):
            self._say("reading…")
            r = ask_daemon("digest", timeout=300.0, hours=24)
            if r is None:
                self._say("TRACE is not running. Turn it on first.")
            elif not r.get("ok"):
                self._say(str(r.get("error")))
            else:
                self._say(r.get("text", ""))

        def showHistory_(self, _s):
            """Your own questions, read back to you.

            The memory remembers what you wondered, not only what it saw. It
            also makes the correction path reach further than the last
            answer — you can see a thing you asked on Tuesday.
            """
            self._say("reading…")
            r = ask_daemon("history", timeout=120.0, limit=50)
            if r is None:
                self._say("TRACE is not running. Turn it on first.")
            elif not r.get("ok"):
                self._say(str(r.get("error")))
            else:
                self._say(r.get("text", ""))

        def showMap_(self, _s):
            try:
                from tracemem.app.graph_window import open_live_map
            except ImportError:                          # bundled beside window.py
                from graph_window import open_live_map   # type: ignore
            open_live_map()

        @objc.python_method
        def _settings_api(self):
            try:
                from tracemem.app import settings as api
            except ImportError:                          # bundled
                import settings as api                   # type: ignore
            return api

        def showSettings_(self, _s):
            """WHAT YOU HAVE CHOSEN — and a way to change it.

            Read from the same file the daemon reads and written through the
            same functions `./trace settings` calls, so the screen, the
            terminal and the eye cannot drift into disagreeing about what is
            excluded.
            """
            try:
                api = self._settings_api()
                self._say("%s\n\n---\nedit this file:\n%s"
                          % (api.describe(api.load()), api.SETTINGS_PATH))
            except Exception as e:                       # noqa: BLE001
                self._say("could not read your settings: %s" % e)
                return
            self._offerExclusion()

        @objc.python_method
        def _offerExclusion(self):
            """Ask whether to add or drop an exclusion.

            A SCREEN YOU CAN ONLY READ IS A FILE PATH WITH EXTRA STEPS. The
            blocklist engine has worked since July and the only way to put a
            rule in it was to hand-edit JSON — so the control existed and
            nobody could reach it.
            """
            import AppKit
            alert = AppKit.NSAlert.alloc().init()
            alert.setMessageText_("Ignore something?")
            alert.setInformativeText_(
                "TRACE will not record it, and the gap is written down as "
                "your choice rather than left as a hole.\n\n"
                "app:com.example.bank   ·   site:bank.com   ·   title:Password")
            field = AppKit.NSTextField.alloc().initWithFrame_(
                NSMakeRect(0, 0, 320, 24))
            field.setPlaceholderString_("site:bank.com")
            alert.setAccessoryView_(field)
            alert.addButtonWithTitle_("Ignore it")
            alert.addButtonWithTitle_("Stop ignoring it")
            alert.addButtonWithTitle_("Change a setting…")
            alert.addButtonWithTitle_("Cancel")
            choice = alert.runModal()
            # Fourth button = Cancel. AppKit only names the first three.
            if choice == AppKit.NSAlertThirdButtonReturn + 1:
                return
            if choice == AppKit.NSAlertThirdButtonReturn:
                self._changeSetting()
                return
            spec = str(field.stringValue()).strip()
            kind, _, value = spec.partition(":")
            if not value.strip():
                self._say("Say which kind: app:, site: or title:\n\n"
                          "  site:bank.com")
                return
            api = self._settings_api()
            adding = (choice == AppKit.NSAlertFirstButtonReturn)
            try:
                fn = api.add_rule if adding else api.remove_rule
                st = fn(kind.strip(), value.strip())
            except Exception as e:                       # noqa: BLE001
                self._say("Could not change that: %s" % e)
                return
            self._say("%s\n\n%s %s — the eye picks this up within a beat, "
                      "with no restart."
                      % (api.describe(st),
                         "Now ignoring" if adding else "No longer ignoring",
                         spec))

        def openCockpit_(self, _s):
            import AppKit
            subprocess.Popen([sys.executable, str(ROOT / "tools" / "cockpit.py")],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            AppKit.NSWorkspace.sharedWorkspace().openURL_(
                AppKit.NSURL.URLWithString_("http://localhost:4242"))

        # Closing the window quits: this app IS the window. The daemon keeps
        # running — quitting the face must never stop the memory.
        def windowWillClose_(self, _n):
            import AppKit
            AppKit.NSApplication.sharedApplication().terminate_(None)

    return TraceWindow


def main() -> int:
    import AppKit
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
    TraceWindow = build()
    w = TraceWindow.alloc().init()
    w.win.setDelegate_(w)
    app.setDelegate_(w)
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
