"""OWNS: every threshold the binder uses to decide where one moment ends
(map: Binder / Boundary constants).

EVERY NUMBER IN THIS FILE IS UNCALIBRATED. Not one has been measured
against a real lived week — they are the shapes of the rules, with
placeholder magnitudes. That is stated here, once, loudly, because the
opposite failure is the expensive one: a tuned-looking constant sitting
inline in a function acquires false authority, and six weeks later nobody
remembers it was a guess (DISPATCH: a number without provenance IS a
guess, and must be labeled one).

The Boundary constants atom's whole purpose is preparation for
calibration — NO behaviour changes when these move out of the functions
that used to hold them. Calibration is a separate, later, gold-gated atom;
when it runs, it edits this file and nothing else, and each constant's
comment gains the probe and date that set it.
"""
from __future__ import annotations

# Longest a single moment may run before it is force-cut, regardless of
# whether anything changed. Carried over unchanged from event_segmenter.py
# (was MAX_EPISODE_MS, spec P4.2 "learn the grain later; tunable").
# UNCALIBRATED — 30 min is a plausible attention span, not a measured one.
MAX_EPISODE_MS = 30 * 60 * 1000

# A context switch shorter than this does not close the current moment
# (Flicker window rule atom). Glancing at Slack for three seconds mid-task
# is a detour inside the moment, not the end of it.
# UNCALIBRATED — 5s is the brief's stated default. The real value is
# whatever separates "checked a notification" from "changed activity", and
# only a lived week can say where that lies.
FLICKER_WINDOW_MS = 5_000

# An inactivity gap at least this long is a candidate for ending the DAY
# (Day boundaries & sleep atom). Deliberately far above a lunch break and
# far below a full night, so an unusual sleep still registers.
# UNCALIBRATED — 4h is a shape, not a measurement.
MIN_SLEEP_GAP_MS = 4 * 60 * 60 * 1000

# Two day boundaries may not sit closer together than this. Without it a
# long flight or a sick day would shatter into several "days".
# UNCALIBRATED — 12h assumes a roughly circadian life, which is exactly
# the assumption a real week is supposed to test.
MIN_DAY_MS = 12 * 60 * 60 * 1000

__all__ = ["MAX_EPISODE_MS", "FLICKER_WINDOW_MS", "MIN_SLEEP_GAP_MS",
           "MIN_DAY_MS"]
