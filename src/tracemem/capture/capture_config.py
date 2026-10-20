"""OWNS: the two-stream capture constants (map: World / Capture config).

Ruling (z), STATE_BOARD 2026-07-19: capture is TWO eyes, not one fixed-
cadence stream —

  - motion sense: ~480p, 5-10 fps, feeds the diff kernel (peripheral.py)
    ONLY. Cheap, always-on, never touches the ring's reading tier.
  - reading eye: native resolution, fires ONLY on a motion-sense change
    verdict (MotionGatedCapture._capture_one_full_res_frame). Fixed-
    cadence full-res capture is banned by this ruling.

Every number here that governs capture geometry, rate, or the diff
kernel's downscale grid lives ONLY in this file — retina_screen.py and
peripheral.py import it rather than redeclaring it. Oracle (Capture
config atom, campaign/tree/01_world.json): `grep` for hardcoded fps or
resolution literals outside this file returns nothing.

Constants get provenance (DISPATCH.md): each one below names the ruling
or probe that produced it, not a bare guess.
"""
from __future__ import annotations

# -- motion-sense stream (the cheap, always-on eye) -------------------------
# ~480p, 5-10 fps: ruling (z), STATE_BOARD 2026-07-19. ScreenCaptureKit does
# the downscale natively via SCStreamConfiguration.setWidth_/setHeight_ —
# no software resize step needed downstream (see peripheral.py's BGRA
# adapter docstring).
MOTION_WIDTH = 480
MOTION_HEIGHT = 270
MOTION_FPS = 8.0        # within the ruling's 5-10 fps band; witnessed live
                        # at 7.95 fps actual (eval_runs/motion_oracle_20260719.json)

# One edit's multi-region churn on the motion-sense diff must collapse to
# ONE triggered full-res grab, not one per changed region. Witnessed live:
# deliberate-change trigger latency 0.49s (well under this cooldown) —
# eval_runs/motion_oracle_20260719.json.
CHANGE_TRIGGER_COOLDOWN_MS = 1_000

# -- reading eye (native-res, change-gated; fires on a motion verdict) ------
# The one-shot full-res grab triggered by a change verdict requests a fast
# capture rate so exactly one frame lands promptly (SCStreamSession.start()
# still floors at whatever ScreenCaptureKit actually delivers for a single
# grab) — not a sustained cadence, and never used for continuous capture.
# Value carried over unchanged from the motion-sense wiring atom
# (MotionGatedCapture._capture_one_full_res_frame, 2026-07-19).
READING_EYE_GRAB_FPS = 30.0

# SCStreamSession's own generic constructor default, used only if a caller
# builds one directly without passing fps (every production wiring path —
# MotionGatedCapture's motion eye and its triggered full-res grab — always
# passes an explicit fps and never hits this default). Kept low on purpose
# so an un-parameterized SCStreamSession cannot accidentally become the
# fixed-cadence full-res capture this ruling bans.
SCSTREAM_SESSION_DEFAULT_FPS = 1.0

# Colorspace: not yet set anywhere (SCStreamConfiguration never calls a
# colorspace/pixel-format setter in retina_screen.py today) — an unclaimed
# open item, not a guessed constant. Add it here, with the probe that
# picked it, the day capture actually sets one.

# -- MEASURED consequence of the native-resolution fix (re-audit 2026-07-21)
# Fixing the points/pixels unit bug (the reading eye was asking
# SCStreamConfiguration for a POINTS-sized frame, capturing at half linear
# resolution on Retina) made full-res frames 10.4x heavier:
#
#     before   3024x1964 requested as 1512x982   ->   2.29 MB/frame
#     after    3024x1964 native BGRA             ->  23.89 MB/frame  (witnessed)
#
# At retina.DEFAULT_BUDGET_BYTES (256 MiB) the ring therefore holds ~11
# full-res frames instead of ~117. BOOKED HERE rather than discovered later,
# because two things now read differently:
#   - the SCStream atom's oracle text ("ring held 117") is stale and false;
#   - Retina.pin() refuses over budget, so deep looks now hit that ceiling at
#     roughly a tenth of the previous scene depth.
# NOT a regression to undo: the frames are correct now and were wrong before.
# Whether 11 frames is enough is a real open question, and it is NOT the
# alarming number it looks like — the reading eye is CHANGE-GATED (ruling z),
# so it grabs on a motion verdict, not at a fixed cadence; the old "117
# frames = 58 seconds" figure came from a 2 fps continuous run that this
# ruling banned. Seconds-of-history depends on how often the screen changes,
# which no probe has measured. That probe belongs to the Frame delivery /
# Capture bench atoms; until it runs, this number is a WITNESSED measurement
# with an UNKNOWN consequence, and is labelled as exactly that.
FULL_RES_FRAME_BYTES_MEASURED = 23_887_872   # 3024x1964 BGRA, this Mac, 2026-07-21

# -- the coordinate space law (Points-vs-pixels law atom, 2026-07-21) -------
# DECIDED: every coordinate crossing the capture boundary is in POINTS, the
# display-independent space. Backing pixels never leave a frame buffer. The
# conversion happens in exactly one module — capture/coordinate_space.py —
# and an invariant grep proves there is no second converter. Rationale and
# the silent-failure class it closes: see that module's docstring.
COORDINATE_SPACE = "points"

# -- presence / idle (Idle seconds atom) ------------------------------------
# Seconds of zero human input after which the focus sense calls you AWAY.
# Provenance: NOT probed — chosen to match the macOS default display-sleep
# floor (5 min) so "away" and "the screen dimmed" tell the same story. This
# is an ASSUMED constant; the Away witness atom's live walk-away run is what
# will either confirm it or move it, and must record the result here.
IDLE_AWAY_THRESHOLD_S = 300.0

# How often the presence sampler reads the OS idle clock. Cheap call
# (CGEventSourceSecondsSinceLastEventType), so the rate is set by how
# precisely an away-boundary needs to land, not by cost: 10s bounds the
# error on a gap claim's edges at 10s.
IDLE_POLL_INTERVAL_S = 10.0

# -- diff kernel downscale grid (peripheral.py's region grid) ---------------
# 16x10 regions over a 480x270 motion-sense frame (~30x27px per region).
# Carried over unchanged from the diff kernel's original tuning (Extraction
# / Diff kernel atom, pre-2026-07-19); no re-probe done as part of this
# atom, so treat GRID_X/GRID_Y as ASSUMED-good rather than re-witnessed.
GRID_X = 16
GRID_Y = 10
