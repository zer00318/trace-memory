#!/usr/bin/env python3
"""F1 golden-frame capture (WO-001).

ZERO-EFFORT MODE (recommended):
    python3 tools/gold_capture.py --auto [seconds]
Leave it running in a terminal and just work normally. It snaps the screen
every 4 minutes (default) with app/window/timestamp recorded automatically.
Stop with Ctrl-C whenever — a normal workday yields 100+ varied frames.
Labels are then written by the frontier model (ceiling scoring — founder
rule 2026-07-19), not by hand. tools/labeler.py stays for spot-checks only.

GUIDED MODE (optional, ~15 min): run with no flags and follow the prompts
to stage specific screen types.

gold/ is gitignored: frames never leave this Mac (eval-only).
Re-running skips frames that already exist, so you can stop anytime.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

CLASSES = [
    ("dense_text_ui", "a text-DENSE UI (Slack / Mail / a big spreadsheet)"),
    ("sparse_ui", "a SPARSE UI (Finder window, System Settings)"),
    ("browser_article", "a browser on a long text ARTICLE"),
    ("code_editor", "your EDITOR with real code on screen"),
    ("video_paused", "a video, PAUSED"),
    ("video_playing", "the same video PLAYING (shoot mid-motion)"),
    ("terminal", "a TERMINAL with real scrolled output"),
    ("mixed_windows", "several OVERLAPPING windows of different apps"),
]
PER_CLASS = 5
OUT = Path(__file__).resolve().parent.parent / "gold" / "frames"


def osascript(expr):
    try:
        r = subprocess.run(["osascript", "-e", expr],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception:
        return ""


def auto(interval):
    OUT.mkdir(parents=True, exist_ok=True)
    n = len(list(OUT.glob("auto_*.png")))
    print(f"Auto-capture every {interval}s → {OUT}")
    print("Just work normally. Ctrl-C to stop (anytime is fine).")
    while True:
        time.sleep(interval)
        n += 1
        png = OUT / f"auto_{n:04d}.png"
        app = osascript('tell application "System Events" to get name of '
                        'first process whose frontmost is true')
        win = osascript('tell application "System Events" to get name of front '
                        'window of (first process whose frontmost is true)')
        r = subprocess.run(["screencapture", "-x", "-m", str(png)])
        if r.returncode != 0 or not png.exists():
            print("!! screencapture failed — grant Screen Recording permission "
                  "to this terminal and re-run.")
            return
        (OUT / f"{png.stem}.json").write_text(json.dumps({
            "id": png.stem, "class": "auto", "t_ms": int(time.time() * 1000),
            "app": app, "window": win, "coord_space": "pixels_topleft",
            "text_regions": [],
        }, indent=2))
        print(f"  {png.name}  {app}")


def main():
    if "--auto" in sys.argv:
        i = sys.argv.index("--auto")
        secs = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 and sys.argv[i + 1].isdigit() else 240
        try:
            auto(secs)
        except KeyboardInterrupt:
            print(f"\nStopped. {len(list(OUT.glob('auto_*.png')))} frames banked in {OUT}")
        return
    OUT.mkdir(parents=True, exist_ok=True)
    goal = len(CLASSES) * PER_CLASS
    done = 0
    for cls, desc in CLASSES:
        for i in range(1, PER_CLASS + 1):
            png = OUT / f"{cls}_{i:02d}.png"
            if png.exists():
                done += 1
                continue
            print(f"\n[{cls} {i}/{PER_CLASS}] Set up: {desc}")
            try:
                input("  Press Enter, then CLICK THE TARGET WINDOW (3s countdown)... ")
            except EOFError:
                print("stdin closed — run this from a real terminal.")
                sys.exit(1)
            time.sleep(3)
            app = osascript('tell application "System Events" to get name of '
                            'first process whose frontmost is true')
            win = osascript('tell application "System Events" to get name of front '
                            'window of (first process whose frontmost is true)')
            r = subprocess.run(["screencapture", "-x", "-m", str(png)])
            if r.returncode != 0 or not png.exists():
                print("  !! screencapture failed — grant Screen Recording "
                      "permission to this terminal and re-run (progress is kept).")
                sys.exit(1)
            sidecar = {
                "id": png.stem,
                "class": cls,
                "t_ms": int(time.time() * 1000),
                "app": app,
                "window": win,
                "coord_space": "pixels_topleft",
                "text_regions": [],
            }
            (OUT / f"{png.stem}.json").write_text(json.dumps(sidecar, indent=2))
            done += 1
            print(f"  ok {png.name}  app={app!r}  ({done}/{goal})")
    print(f"\nDone: {done}/{goal} frames in {OUT}")
    print("Next:  python3 tools/labeler.py  — browser opens pre-loaded, "
          "boxes autosave.")


if __name__ == "__main__":
    main()
