#!/usr/bin/env python3
"""THE FIRST FACE. Ask your memory a question, from a terminal.

    $ python -m tracemem --ask --eye          # in one terminal: the daemon
    $ python tools/ask.py                     # in another: the face
    > what was I reading this morning?

This is not `10_faces > Ask > The window` — that leaf wants a native window
with a global hotkey, and it stays at 0% until one exists. What this IS: the
protocol that window will speak, proven end to end by a client a person can
actually use today. Building the socket without a client would be one more
seam with no caller, which is the failure this campaign keeps rediscovering;
building the window first would be styling a protocol nobody had run.

It talks to the daemon over a UNIX domain socket, never a port — see
packaging/entitlements.md, `com.apple.security.network.server` false.

    --socket PATH   somewhere other than ~/.trace/ask.sock
    --stats         print what the memory currently holds, and exit
    QUESTION...     ask one question and exit (otherwise: a prompt loop)
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path

DEFAULT_SOCKET = Path.home() / ".trace" / "ask.sock"


class Face:
    def __init__(self, path):
        self.path = Path(path)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(600)
        try:
            self.sock.connect(str(self.path))
        except (FileNotFoundError, ConnectionRefusedError):
            raise SystemExit(
                "no daemon is listening on %s.\n"
                "Start one with:  python -m tracemem --ask --eye" % self.path)
        self._buf = b""

    def send(self, **req) -> dict:
        self.sock.sendall((json.dumps(req) + "\n").encode())
        while b"\n" not in self._buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise SystemExit("the daemon closed the connection")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return json.loads(line)

    def close(self):
        self.sock.close()


def _render(r: dict) -> None:
    if not r.get("ok"):
        print("  ! %s" % r.get("error", "unknown error"))
        return
    if r.get("abstained"):
        # The abstention is the product working, not failing. Printing it as
        # an error would teach the founder to distrust the one behaviour that
        # separates this from a system that makes things up.
        print("  (not in memory)")
        return
    print("  %s" % r["text"])
    if r.get("citations"):
        # HOW MUCH TO TRUST EACH RECEIPT, said in words rather than as the
        # name of an enum. An answer resting on a VLM's glance used to look
        # exactly like one resting on text the machine actually read.
        grades = r.get("citation_grades") or {}
        parts = []
        for c in r["citations"]:
            cid = str(c)[:8]
            how = grades.get(cid)
            parts.append("%s (%s)" % (cid, how) if how else cid)
        print("  — from %s" % ", ".join(parts))
    # HOW TO PUSH BACK, SHOWN WHERE THE ANSWER IS. The grade op has existed
    # for a month and was unreachable in practice: it needs a line id, and
    # nothing ever printed one next to an answer. A correction path nobody
    # can find is the same as no correction path.
    if r.get("citations"):
        # FOLLOWABLE, not decorative. The whole point of a citation is that
        # you can go and look at what the machine actually saw.
        print("     look:   ./trace ask --moment %s"
              % str(r["citations"][0])[:8])
    if r.get("line"):
        print("     wrong?  ./trace ask grade %s wrong \"what really "
              "happened\"" % r["line"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ask")
    ap.add_argument("--socket", default=str(DEFAULT_SOCKET))
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--history", nargs="?", const="", metavar="WORDS",
                    help="what you have asked before, optionally filtered")
    ap.add_argument("--questions", action="store_true",
                    help="what TRACE cannot work out alone")
    ap.add_argument("--answer", nargs=2, metavar=("QID", "TEXT"),
                    help="settle one of its questions, in your words")
    ap.add_argument("--morning", action="store_true",
                    help="what it would tell you unprompted (often nothing)")
    ap.add_argument("--moment", metavar="CITE",
                    help="show the moment behind a citation, e.g. a3b4115f")
    ap.add_argument("--digest", nargs="?", const=24, type=float, metavar="HOURS",
                    help="what the brain did and what it now thinks")
    ap.add_argument("--grade", nargs="+", metavar=("LINE VERDICT", "CORRECTION"),
                    help="LINE right|wrong|meh [\"the correct thing\"]")
    ap.add_argument("question", nargs="*")
    args = ap.parse_args(argv)

    face = Face(args.socket)
    try:
        if args.digest is not None:
            r = face.send(op="digest", hours=args.digest)
            if not r.get("ok"):
                print("  ! %s" % r.get("error"))
                return 1
            print(r["text"])
            return 0

        if args.grade:
            if len(args.grade) < 2:
                print("  ! usage: grade <line-id> right|wrong|meh "
                      "[\"the correct thing, in one sentence\"]")
                return 1
            r = face.send(op="grade", line=args.grade[0], verdict=args.grade[1],
                          correction=" ".join(args.grade[2:]))
            if not r.get("ok"):
                print("  ! %s" % r.get("error"))
                return 1
            # Echoed back so the founder sees exactly what entered memory in
            # his name — a claim written on someone's behalf that they never
            # read is the beginning of a memory they cannot trust.
            print("  recorded: %s" % r["recorded"])
            return 0

        if args.questions:
            r = face.send(op="questions")
            if not r.get("ok"):
                print("  ! %s" % r.get("error"))
                return 1
            print(r.get("text") or
                  "  TRACE has nothing it needs to ask you.")
            return 0

        if args.answer:
            r = face.send(op="answer", question=args.answer[0],
                          said=args.answer[1])
            if not r.get("ok"):
                print("  ! %s" % r.get("error"))
                return 1
            print("  recorded, in your words:\n    %s" % r["recorded"])
            return 0

        if args.morning:
            r = face.send(op="morning")
            if not r.get("ok"):
                print("  ! %s" % r.get("error"))
                return 1
            # A QUIET NIGHT PRINTS ONE LINE AND NOT A HEADING WITH NOTHING
            # UNDER IT. Padding is how a memory starts telling you things it
            # does not believe.
            print(r.get("text") or
                  "  Nothing worth telling you this morning.")
            return 0

        if args.moment:
            r = face.send(op="moment", cite=args.moment)
            if not r.get("ok"):
                print("  ! %s" % r.get("error"))
                return 1
            print(r["text"])
            return 0

        if args.history is not None:
            r = face.send(op="history", query=args.history, limit=50)
            if not r.get("ok"):
                print("  ! %s" % r.get("error"))
                return 1
            print(r["text"])
            return 0

        if args.stats:
            r = face.send(op="stats")
            if not r.get("ok"):
                print("  ! %s" % r.get("error"))
                return 1
            if r.get("waking"):
                # N3: reachable in milliseconds, honest for the whole wake.
                # Reporting the settled numbers here would show a person a
                # node count at the one moment it is guaranteed provisional.
                total = r.get("total") or 0
                pct = (100.0 * (r.get("bound") or 0) / total) if total else 0.0
                print("WAKING UP — binding what is already on disk.")
                print("bound claims : %s of %s (%.0f%%)"
                      % (r.get("bound"), total, pct))
                print("episodes     : %s (so far)" % r.get("episodes"))
                print("\nQuestions are refused until this finishes — a "
                      "half-bound store\nwould answer 'not in memory' about "
                      "things that ARE in memory.")
                return 0
            # A BLIND EYE IS SAID FIRST, LOUDLY. It just happened for real:
            # `./trace install` moved the daemon from a terminal (which held
            # the Screen Recording grant) to launchd, which spawns the
            # interpreter directly. The grant does not follow, and the eye
            # went dark while every other number stayed healthy.
            organs = r.get("organs") or {}
            eye = organs.get("eye")
            # AN EYE SHUT ON PURPOSE IS NOT AN ALARM.
            #
            # WITNESSED 2026-08-24: the founder's screen was locked, the
            # eyelid had closed the eye exactly as F-EYELID says it should,
            # and this block printed "!! THE EYE IS NOT WATCHING" over a
            # RuntimeError left behind by one start attempt made while the
            # display slept. Both halves were misleading. The eye was fine;
            # the error was stale (errors: 1, hours old, already retried
            # past); and there was nothing for the founder to do.
            #
            # An alarm that fires every time its owner walks away teaches its
            # owner to ignore it, and the ONE thing this line exists to catch
            # — a genuinely blinded eye, which has happened twice and cost 16
            # days once — arrives looking exactly like the noise. That is a
            # worse failure than printing nothing at all, because it spends
            # the founder's attention and returns a false negative later.
            #
            # `tools/blind_alarm.py` already draws this distinction (it only
            # alarms when the machine is in use); this face did not, so the
            # two disagreed about the same eye. The eyelid's own reason is
            # the authority — it is the component that made the decision.
            # IS THIS DAEMON RUNNING THE CODE ON DISK?
            #
            # A daemon is as old as its last restart. Six capabilities landed
            # on 2026-08-25 and every one answered `unknown op` against the
            # daemon that was up — with nothing anywhere saying that a
            # restart was all it needed. A person reads the docs, tries the
            # command, gets an error, and concludes the feature is broken.
            KNOWN = {"history": "read back what you have asked",
                     "moment": "follow a citation to what it saw",
                     "morning": "what it would tell you unprompted",
                     "questions": "what it cannot work out alone",
                     "answer": "answer its questions"}
            missing = [v for k, v in sorted(KNOWN.items())
                       if k not in set(r.get("ops") or [])]
            if missing:
                print("!! THIS DAEMON IS OLDER THAN THE CODE ON DISK")
                print("   It cannot yet: %s." % "; ".join(missing))
                print("   Restart it to pick them up:  ./trace stop && "
                      "./trace start")
                print("")

            # A BROKEN CHAIN OUTRANKS EVERYTHING ELSE ON THIS SCREEN.
            audit = ((r.get("night") or {}).get("chain_audit")) or {}
            if audit and audit.get("ok") is False:
                print("!! THE STORE'S HASH CHAIN IS BROKEN at claim %s (%s)"
                      % (audit.get("broken_at"), audit.get("reason")))
                print("   A claim was altered or lost after it was written.")
                print("   Your memories are here; their proof is not.")
                print("")

            lid = r.get("eyelid") or {}
            shut_on_purpose = (eye == "parked" and lid.get("eyes_open") is False)
            if shut_on_purpose:
                why = {"locked": "the screen is locked",
                       "idle": "nobody has touched the machine"}.get(
                           lid.get("closed_because"),
                           str(lid.get("closed_because") or "closed"))
                print("eye          : closed on purpose — %s" % why)
                print("               (it reopens by itself when you come back)")
            elif eye and eye != "alive":
                detail = (organs.get("eye_detail") or {}).get("last_error") or ""
                print("!! THE EYE IS NOT WATCHING (state: %s)" % eye)
                if "TCC" in detail or "-3801" in detail or "Screen Recording" in detail:
                    # NAME THE BINARY. "the Python listed there" is not an
                    # instruction when a Mac has five Pythons and the list
                    # shows them all as "Python". macOS grants Screen
                    # Recording to the RESPONSIBLE process, which for a
                    # launchd-started daemon is the interpreter itself —
                    # measured 2026-08-20: launched from a terminal the eye
                    # sees, launched by launchd the SAME code reports "not
                    # granted", which is why `./trace install` has blinded
                    # the eye before.
                    import os as _os
                    import sys as _sys
                    real = _os.path.realpath(_sys.executable)
                    # The repo root, derived from this file rather than a
                    # module-level constant that does not exist here. A
                    # NameError in the branch that fires WHEN THE EYE IS
                    # BLIND would replace the one instruction that matters
                    # with a traceback.
                    root = _os.path.dirname(_os.path.dirname(
                        _os.path.abspath(__file__)))
                    print("   Screen Recording is not granted to the program")
                    print("   that is running TRACE.")
                    print("")
                    # THE DURABLE FIX IS THE APP, and it is why the app was
                    # built. macOS attaches this grant to the RESPONSIBLE
                    # PROCESS, so granting it to a Python binary ties it to
                    # whichever terminal or launchd happened to start that
                    # binary — which is exactly why it "kept coming loose"
                    # and why `./trace install` blinded the eye once already.
                    # TRACE.app is a stable identity (com.trace.app) and a
                    # daemon it starts inherits it as the responsible parent.
                    print("   THE FIX THAT STAYS FIXED — open the app:")
                    print("     open %s/dist/TRACE.app" % root)
                    print("   press 'Fix permission…', switch TRACE on in the")
                    print("   list (press + and pick it from Applications if")
                    print("   it is absent), then Turn TRACE off and on again.")
                    print("   From then the grant belongs to TRACE itself,")
                    print("   not to whatever started it.")
                    print("")
                    print("   RIGHT NOW, from this terminal only, you can")
                    print("   instead grant the interpreter:")
                    print("     %s" % real)
                    print("   then:  ./trace stop && ./trace start")
                    print("   That works until something else launches it.")
                elif detail:
                    print("   reason: %s" % detail[:150])
                print("")
            # THE BRAIN'S OWN VITALS, when the night has computed some.
            v = ((r.get("night") or {}).get("vitals")) or {}
            rate = v.get("sayable_belief_rate")
            if rate is not None and v.get("beliefs"):
                print("beliefs      : %s, of which %s read like something "
                      "about you (%.0f%%)"
                      % (v["beliefs"], v.get("sayable_beliefs", 0),
                         100.0 * rate))
            eye_d = (r.get("organs") or {}).get("eye_detail") or {}
            if eye_d.get("ax_trusted") is False:
                print("!! WINDOW TITLES AND PAGE ADDRESSES ARE NOT BEING READ.")
                print("   TRACE does not have the Accessibility permission, so")
                print("   answers see 'Brave Browser' instead of the page you")
                print("   were on. Fix: System Settings > Privacy & Security >")
                print("   Accessibility > switch TRACE on (add it with + from")
                print("   Applications if it is missing).")
            ms = (r.get("organs") or {}).get("model_server") or {}
            if ms:
                if ms.get("up") is False:
                    print("model server : DOWN — questions and scene "
                          "understanding wait for it%s"
                          % (" (restart failed: %s)" % ms["last_error"]
                             if ms.get("last_error") else
                             ", restarting it"))
                else:
                    extra = ""
                    if ms.get("revives"):
                        import datetime as _dt
                        extra = ("  (restarted %d time(s), last %s)"
                                 % (ms["revives"], _dt.datetime.fromtimestamp(
                                     ms["last_revive"]).strftime("%Y-%m-%d %H:%M")))
                    print("model server : up%s" % extra)
            print("bound claims : %s" % r.get("bound"))
            print("pending      : %s" % r.get("pending"))
            print("episodes     : %s" % r.get("episodes"))
            print("nodes        : %s" % r.get("nodes"))

            # THE LAST CAPTURE, SAID AS A DATE. Added 2026-08-10, because the
            # four counters above are cumulative and therefore can only ever
            # go up. On the founder's machine capture died on 5 August and for
            # five days this block printed six hundred thousand claims, which
            # reads as health. A memory's headline number must be WHEN it last
            # saw something, not how much it has ever seen.
            # Three states, three sentences. "The daemon is too old to know"
            # is NOT the same fact as "it has never seen anything", and
            # printing the second when the first is true is a status line
            # lying with confidence — which is the failure being fixed here,
            # so this face must not commit it while reporting it.
            has_key = "last_capture_ms" in r
            last_ms = r.get("last_capture_ms")
            if has_key and last_ms:
                # Rendered HERE, in the lived timezone, because this is a face
                # — the daemon shipped an integer (time law).
                import datetime as _dt
                last = _dt.datetime.fromtimestamp(
                    last_ms / 1000.0).strftime("%Y-%m-%d %H:%M:%S")
                age_h = r.get("last_capture_age_h") or 0.0
                if age_h >= 24.0:
                    print("last seen    : %s" % last)
                    print("               !! %.1f DAYS AGO. Nothing has been "
                          "captured since. The counters above are cumulative "
                          "and cannot fall, so they will keep looking healthy."
                          % (age_h / 24.0))
                elif age_h >= 1.0:
                    print("last seen    : %s  (%.1f hours ago)" % (last, age_h))
                else:
                    print("last seen    : %s" % last)
            elif has_key:
                print("last seen    : never — this store holds no perceptions")
            else:
                print("last seen    : unknown — this daemon predates the "
                      "reading; restart it to find out")

            # THE NIGHT, SAID OUT LOUD. It ran 16,828 slices deriving nothing
            # and `status` never mentioned it existed, so nobody could see it.
            n = r.get("night") or {}
            if n:
                if n.get("stuck"):
                    print("")
                    print("!! THE NIGHT IS STUCK — it cannot finish a pass "
                          "and is deriving nothing.")
                    print("   %s slices, %s completed, %s accepted out of %s "
                          "attempted."
                          % (n.get("slices"), n.get("completed"),
                             (n.get("totals") or {}).get("accepted"),
                             (n.get("totals") or {}).get("attempted")))
                    print("   It has stood down until something new arrives. "
                          "This is a defect, not rest.")
                elif n.get("settled"):
                    print("night        : settled — nothing left to think "
                          "about (%s passes completed)" % n.get("completed"))
                else:
                    print("night        : %s slices, %s completed, %s accepted"
                          % (n.get("slices"), n.get("completed"),
                             (n.get("totals") or {}).get("accepted")))
            return 0

        if args.question:
            _render(face.send(op="ask", question=" ".join(args.question)))
            return 0

        print("asking %s — ctrl-d to leave" % args.socket)
        while True:
            try:
                q = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not q:
                continue
            if q in ("stats", ":stats"):
                r = face.send(op="stats")
                print("  bound=%s pending=%s episodes=%s nodes=%s"
                      % (r.get("bound"), r.get("pending"), r.get("episodes"),
                         r.get("nodes")))
                continue
            _render(face.send(op="ask", question=q))
    finally:
        face.close()


if __name__ == "__main__":
    sys.exit(main())
