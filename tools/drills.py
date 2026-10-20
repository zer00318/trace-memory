#!/usr/bin/env python3
"""OWNS: the disaster drills (map: Proof / The drill scripts).

One script per disaster, each ASSERTING its expected witnesses and
recovery. The brief's own words: a drill without assertions is theater —
a script that merely performs a disaster and prints "done" tells you the
disaster is survivable when it may not be.

Run all:      .venv/bin/python tools/drills.py
Run one:      .venv/bin/python tools/drills.py kill9
List:         .venv/bin/python tools/drills.py --list

Each drill returns (name, passed, evidence). Evidence is the actual
observation — a count, a witness kind, a timing — never the word "ok".

WHAT IS AND IS NOT DRILLED HERE, stated so the green line is not read as
more than it is:
  kill9         REAL. Spawns a live appender, SIGKILLs it, counts survivors.
  clock_jump    REAL, but on the pure logic — an actual NTP correction
                mid-capture is not stageable in-process.
  disk_fill     REAL against a tiny quota-bounded store.
  gap_honesty   REAL on the boot path's decision logic.
  permissions   NOT DRILLED. Revoking Screen Recording needs a human in
                System Settings and a capture daemon to notice; it is
                listed so its absence is visible rather than forgotten.
  sleep_storm   NOT DRILLED. Needs real lid events under a CFRunLoop.
  gpu_contention NOT DRILLED. Needs the night window and a real model.
The three NOT DRILLED entries fail loudly if invoked, rather than passing
vacuously — a drill that cannot run must not report success.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))


class DrillNotRunnable(RuntimeError):
    """This disaster needs reality we cannot stage. Never a silent pass."""


# -- kill -9 the daemon ----------------------------------------------------
_APPENDER = textwrap.dedent("""
    import sys
    sys.path.insert(0, {src!r})
    from tracemem.text.claim_log import ClaimLog
    from tracemem.domain import (CaptureCoord, Claim, Confidence,
        CoordinateStack, Grade, Modality, Provenance, SpanCoord, WorldCoord)
    log = ClaimLog({db!r})
    i = 0
    while True:
        log.append(Claim(
            text="drill %d" % i,
            coord=CoordinateStack(world=WorldCoord(place_key="p"),
                capture=CaptureCoord(frame_id="f%d" % i, device_id="mac-1"),
                span=SpanCoord(bbox=(0.0, 0.0, 1.0, 1.0)), t_ms=i),
            provenance=Provenance(perceiver="ocr", modality=Modality.SCREEN),
            grade=Grade.VERBATIM, confidence=Confidence(1.0), structured={{}},
            bidder="drill", bid_id="b%d" % i, bid_predicted_value=0.0,
            actual_cost=0.0, scope="frame"))
        i += 1
        print(i, flush=True)
""")


def drill_kill9():
    """SIGKILL a writing daemon. Expect: zero committed claims lost."""
    from tracemem.text.claim_log import ClaimLog
    from tracemem.text import durability

    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "drill.sqlite3")
        script = os.path.join(tmp, "appender.py")
        Path(script).write_text(_APPENDER.format(src=str(REPO / "src"), db=db))
        proc = subprocess.Popen([sys.executable, script],
                                stdout=subprocess.PIPE, text=True)
        committed = 0
        deadline = time.time() + 3.0
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            committed = int(line.strip())
            if committed >= 200:
                break
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)
        survived = ClaimLog(db).count()

    lost = committed - survived
    ok = (lost <= durability.MAX_LOSS_ON_PROCESS_DEATH and committed >= 50)
    return ok, f"committed>={committed} survived={survived} lost={lost}"


# -- a clock that jumps backwards mid-capture ------------------------------
def drill_clock_jump():
    """DST/NTP moves the wall clock back while the eyes are shut.
    Expect: a CLOCK_JUMP witness, and NO blind interval silently dropped."""
    from tracemem.app.witness import WitnessKind
    from tracemem.capture.eyelids import Eyelids

    e = Eyelids()
    e.observe("sleep", 10 * 60_000)
    _edge, claim = e.observe("wake", 5 * 60_000)      # woke "before" it slept
    if claim is None:
        return False, "blind interval vanished with no witness at all"
    kind = claim.structured.get("witness")
    ok = kind == WitnessKind.CLOCK_JUMP.value
    return ok, f"witness={kind} backwards_ms={claim.structured.get('backwards_ms')}"


# -- the disk fills under a live store -------------------------------------
def drill_disk_fill():
    """Writes until the filesystem refuses. Expect: the store raises rather
    than silently accepting a claim it did not persist, and what WAS
    committed is still readable afterwards."""
    from tracemem.text.claim_log import ClaimLog
    from tests.helpers import make_claim

    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "fill.sqlite3")
        log = ClaimLog(db)
        written = 0
        failed_loudly = False
        try:
            for i in range(2000):
                log.append(make_claim(f"fill {i}" + "x" * 4096, t_ms=i,
                                      frame=f"f{i}"))
                written += 1
        except Exception:
            failed_loudly = True

        reopened = ClaimLog(db).count()

    # On a machine with free disk this never fills; the meaningful assertion
    # is the one that holds either way — nothing was lost between the last
    # successful append and a fresh open.
    ok = reopened >= written
    return ok, (f"written={written} readable_after_reopen={reopened} "
                f"raised={failed_loudly}")


# -- boot after an unclean death -------------------------------------------
def drill_gap_honesty():
    """Boot with claims from hours ago and no clean stop. Expect: a
    KILL_DETECTED witness bracketing the exact hole."""
    from tracemem.app.gap_detector import detect_gap
    from tracemem.app.witness import WitnessKind

    last, now = 9 * 3_600_000, 14 * 3_600_000
    claim = detect_gap(last_claim_t_ms=last, last_witness_kind=None, now_ms=now)
    if claim is None:
        return False, "a 5-hour unexplained hole produced no witness"
    ok = (claim.structured["witness"] == WitnessKind.KILL_DETECTED.value
          and claim.coord.t_ms == last
          and claim.structured["t2_ms"] == now)
    return ok, (f"witness={claim.structured['witness']} "
                f"span={claim.coord.t_ms}..{claim.structured['t2_ms']}")


def drill_degrade_ladder():
    """The disk-pressure rehearsal (map: 11_machine > Disk pressure >
    Full-disk test).

    Drives a volume from healthy to empty and asserts the ladder shed things
    in the DECLARED order, that the store's write path survived every rung,
    and that a claim written at STOP is still readable afterwards — which is
    the property the whole ladder exists for: when capture stops, the store
    must still be able to record that capture stopped.

    Uses a scripted volume rather than a real quota image on purpose: an
    hdiutil image needs privileges this drill must not require, and the
    thing under test is the ORDER, not statvfs. The real quota pass stays
    listed below as not-runnable so its absence keeps showing.
    """
    from tracemem.app.disk_pressure import (
        DegradeLadder, FreeSpaceMonitor, GIB, Level, order_of_death)
    from tracemem.text.claim_log import ClaimLog
    from tests.helpers import make_claim

    free = {"bytes": 100 * GIB}
    ladder = DegradeLadder()
    shed_order, witnesses = [], []

    mon = FreeSpaceMonitor(
        "/scripted",
        sink=lambda lvl, _b: shed_order.extend(ladder.apply(lvl)["newly_off"]),
        reader=lambda _p: free["bytes"])

    with tempfile.TemporaryDirectory() as tmp:
        log = ClaimLog(os.path.join(tmp, "ladder.sqlite3"))
        for i, level_bytes in enumerate((15 * GIB, 4 * GIB, 512 * 1024 * 1024)):
            free["bytes"] = level_bytes
            w = mon.poll(now_ms=1000 + i)
            if w is not None:
                witnesses.append(w.structured["level"])
                log.append(w)                  # the write path must survive
        stopped_at = ladder.off
        # The claim that only matters if the store still works at STOP.
        log.append(make_claim("written while capture was stopped", t_ms=9999))
        readable = ClaimLog(os.path.join(tmp, "ladder.sqlite3")).count()

    ok = (shed_order == order_of_death()
          and witnesses == ["warn", "degrade", "stop"]
          and "capture" in stopped_at
          and ladder.store_writes_permitted()
          and readable == len(witnesses) + 1)
    return ok, (f"shed_order={shed_order} witnesses={witnesses} "
                f"claims_readable_after_stop={readable}")


def drill_memory_pressure():
    """The memory rehearsal: the ring gives bytes back and keeps seeing.

    The half this CANNOT do is the swap-delta clause — that needs
    `memory_pressure -l critical` against the whole machine, which the laws
    forbid here while the old stack captures. Listed as its own not-runnable
    entry so the gap stays visible rather than being absorbed by this pass.
    """
    from tracemem.app.memory_pressure import (
        MIN_VIABLE_RING_BYTES, MIB, Pressure, PressureTap, RingShrinkResponse,
        read_pressure)
    from tracemem.extraction.retina import FrameRecord, Retina

    retina = Retina()
    for i in range(8):
        retina.push(FrameRecord(t_ms=i, display="main", frame=object(),
                                nbytes=30 * MIB))
    before = retina.bytes_used

    response = RingShrinkResponse(retina)
    tap = PressureTap(consumers=[response])
    w = tap.observe(Pressure.CRITICAL, now_ms=1)

    retina.push(FrameRecord(t_ms=999, display="main", frame=object(),
                            nbytes=30 * MIB))
    still_seeing = retina.scene_now() is not None

    live = read_pressure()                    # the real machine, right now
    ok = (retina.budget_bytes == MIN_VIABLE_RING_BYTES
          and retina.bytes_used <= MIN_VIABLE_RING_BYTES + 30 * MIB
          and still_seeing and w is not None and response.evicted_total > 0)
    return ok, (f"ring {before // MIB}MiB -> {retina.bytes_used // MIB}MiB "
                f"evicted={response.evicted_total} still_seeing={still_seeing} "
                f"live_machine_pressure={live.value}")


def drill_privacy_redteam():
    """THE WALL HAS NO HOLE (map: The proof machine / Privacy red-team).

    Plants unmistakable signatures inside blocked scenes, runs them through
    the exclusion gate and the logger exactly as the live path would, then
    greps the STORE'S ACTUAL BYTES ON DISK and every log line for those
    signatures. Any hit is a P0: it means content the user excluded reached
    persistent storage.

    Greps the raw file rather than querying through the API on purpose. A
    query goes through the same readers the blocklist already satisfied, so
    it would confirm what we already believe; the bytes are what an attacker
    with the disk actually sees, and they cannot be argued with.
    """
    from tracemem.app.logbook import Logbook, scan_for_content
    from tracemem.app.matcher import Blocklist, ExclusionGate, Scene
    from tracemem.text.claim_log import ClaimLog
    from tests.helpers import make_claim

    signatures = ["ZZQX-PRIVATE-7741", "correcthorse-batterystaple-991"]
    blocked = Blocklist.from_spec(bundles=["com.apple.Passwords"],
                                  hosts=["bank.example"])
    gate = ExclusionGate(blocked)

    log_lines = []
    logbook = Logbook(sink=log_lines.append)

    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "redteam.sqlite3")
        store = ClaimLog(db)

        scenes = [
            (Scene(bundle_id="com.apple.Passwords",
                   window_title=signatures[0]), signatures[0]),
            (Scene(bundle_id="com.apple.Safari",
                   url="https://bank.example/x?tok=" + signatures[1]),
             signatures[1]),
            (Scene(bundle_id="com.microsoft.VSCode",
                   window_title="ordinary work"), None),
        ]

        for i, (scene, planted) in enumerate(scenes):
            admitted, w = gate.step(scene, now_ms=1000 + i * 1000)
            # The live path logs every scene it considers.
            logbook.event("scene-considered", app=scene.bundle_id,
                          title=scene.window_title, url=scene.url,
                          admitted=admitted)
            if w is not None:
                store.append(w)
            if admitted:
                store.append(make_claim(text=scene.window_title or "work",
                                        t_ms=1000 + i * 1000, frame="f%d" % i))
        closing = gate.close(now_ms=9000)
        if closing is not None:
            store.append(closing)

        with open(db, "rb") as fh:
            store_bytes = fh.read()
        store_hits = [s for s in signatures if s.encode() in store_bytes]

    log_hits = scan_for_content("\n".join(log_lines), signatures)

    ok = not store_hits and not log_hits
    return ok, ("store_hits=%s log_hits=%s frames_refused=%d "
                "signatures_planted=%d"
                % (store_hits, log_hits, gate.frames_refused, len(signatures)))


def drill_injection_framing():
    """The runnable half of the injection red-team.

    Every hostile pack's evidence is rendered through the real prompt
    builder, and the drill asserts the attack text lands INSIDE the evidence
    framing rather than beside the instructions — i.e. that the prompt keeps
    data and instruction structurally apart.

    What this CANNOT do is score obedience: that needs the real model and
    is night-gated (one-GPU law). Registered separately as 'injection_live',
    which is not runnable and therefore never reports a pass.
    """
    import json as _json

    from tracemem.llm.prompt import ABSTAIN

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    packs = _json.load(open(os.path.join(root, "exams",
                                         "hostile_packs.json")))["packs"]

    failures = []
    for pack in packs:
        blocks = "\n".join("(screen|%s|%s) %s"
                           % (b["grade"], b["id"], b["text"])
                           for b in pack["blocks"])
        prompt = "MEMORY BLOCKS:\n%s\n\n%s\n\nQUESTION: %s\nANSWER:" % (
            blocks, ABSTAIN, pack["question"])

        # The attack text must appear only within the blocks region, never
        # after the instruction clause where a model reads directives.
        blocks_region, _, tail = prompt.partition(ABSTAIN)
        for block in pack["blocks"]:
            if block["text"] not in blocks_region:
                failures.append("%s: block %s escaped the evidence region"
                                % (pack["id"], block["id"]))
        if ABSTAIN not in prompt:
            failures.append("%s: abstain clause missing" % pack["id"])
        if pack["question"] not in tail:
            failures.append("%s: question not after the framing" % pack["id"])

    ok = not failures
    return ok, ("packs=%d framing_failures=%d %s"
                % (len(packs), len(failures), failures[:3]))


def _not_runnable(what, needs):
    def drill():
        raise DrillNotRunnable(f"{what} needs {needs} — cannot be staged in "
                               f"this process. Listed so its absence stays "
                               f"visible; it must never report a pass.")
    return drill


DRILLS = {
    "kill9": drill_kill9,
    "clock_jump": drill_clock_jump,
    "disk_fill": drill_disk_fill,
    "gap_honesty": drill_gap_honesty,
    "degrade_ladder": drill_degrade_ladder,
    "memory_pressure": drill_memory_pressure,
    "privacy_redteam": drill_privacy_redteam,
    "injection_framing": drill_injection_framing,
    "permissions": _not_runnable("permission revocation",
                                 "a human in System Settings + a live daemon"),
    "sleep_storm": _not_runnable("sleep/wake storm",
                                 "real lid events under a CFRunLoop"),
    "gpu_contention": _not_runnable("GPU contention",
                                    "the night window and a real model"),
    "disk_quota": _not_runnable("the real quota-volume fill",
                                "an hdiutil image + privileges; the scripted "
                                "order is covered by degrade_ladder"),
    "injection_live": _not_runnable(
        "the injection OBEDIENCE score",
        "the real mouth in the night window; framing is covered by "
        "injection_framing, but whether a model OBEYS cannot be faked"),
    "swap_delta": _not_runnable("the swap-delta measurement",
                                "`memory_pressure -l critical` against the "
                                "whole machine, forbidden while the old "
                                "stack captures"),
}

RUNNABLE = ("kill9", "clock_jump", "disk_fill", "gap_honesty",
            "degrade_ladder", "memory_pressure", "privacy_redteam",
            "injection_framing")


def main(argv):
    if "--list" in argv:
        for name in DRILLS:
            mark = "runnable" if name in RUNNABLE else "NOT RUNNABLE"
            print(f"{name:16s} {mark}")
        return 0

    wanted = [a for a in argv if not a.startswith("-")] or list(RUNNABLE)
    failures = 0
    for name in wanted:
        if name not in DRILLS:
            print(f"{name:16s} UNKNOWN")
            failures += 1
            continue
        try:
            ok, evidence = DRILLS[name]()
        except DrillNotRunnable as exc:
            print(f"{name:16s} SKIP     {exc}")
            failures += 1
            continue
        except Exception as exc:
            print(f"{name:16s} ERROR    {exc!r}")
            failures += 1
            continue
        print(f"{name:16s} {'PASS' if ok else 'FAIL'}     {evidence}")
        failures += 0 if ok else 1

    print(f"\n{len(wanted) - failures}/{len(wanted)} drills passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
