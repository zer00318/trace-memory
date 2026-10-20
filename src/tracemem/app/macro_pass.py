"""OWNS: running the macro VLM eye over banked frames, when it is safe to.

This is the piece that was missing. `extraction/vlm_macro.VlmMacroEye` has
existed and worked since July and has never produced a single claim in the
product, because nothing scheduled it. The measured cost of that: the
understanding tier — object / activity / inference / participant, 21% of the
frontier ceiling — is recalled at 0.17 by the OCR-only path, and object-tier
recall specifically at 4.3%.

WHEN IT IS SAFE. Never while the eye is open. Heavy GPU work beside live
capture makes capture go dark with no error, which the arbiter enforces
(`GPU_HEAVY_ROLES`, role="MACRO"). So this runs in the same window the night
does: eye closed, GPU uncontended. `extraction/macro_buffer` is what makes
that possible — the frames were seen while the founder was present, and are
understood after they leave.

WHAT IT REFUSES TO DO.

  · It never blocks a question. The arbiter's cooperative contract is poll-
    and-yield, and this polls between frames. A macro pass in flight when the
    founder asks something must stop, not finish — the founder waiting on a
    background chore is the product feeling broken.
  · It never runs partially and reports success. Each frame's claims are
    appended as that frame completes, so a pass cut short by a query keeps
    what it earned; `run()` reports how many frames it actually got through.
  · It never lets one bad frame end the pass. A local VLM returning
    unparseable JSON, or one enormous frame timing out, costs that frame.

WHAT IT CANNOT DO, STATED PLAINLY. Only frames still in memory get
understood: everything before the last daemon restart is gone, and a busy
session is sampled, not covered. The numbers for both are in the buffer's
stats and are carried into this pass's result, so a pass that understood
three scenes out of eighty never reads as a pass that understood the day.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from tracemem.app.scheduler import SpotlightBusy
from tracemem.extraction.macro_claims import macro_claims
from tracemem.extraction.vlm_macro import DEFAULT_MODEL

# The role string the arbiter checks. Must stay in GPU_HEAVY_ROLES; there is
# a test that fails if it drifts out.
MACRO_ROLE = "MACRO"


class MacroPass:
    """One drain of the frame buffer through the macro eye."""

    def __init__(self, buffer, eye, claim_sink: Callable, spotlight, clock,
                 device_id: str, model: str = DEFAULT_MODEL,
                 log: Optional[Callable] = None,
                 ready: Optional[Callable[[], bool]] = None) -> None:
        self._buf = buffer
        # Is the model server up? Asked BEFORE frames leave the buffer.
        # Without it a pass against a dead server took every banked frame,
        # failed each one, and threw them away: 62 frames drained, 0
        # understood (witnessed 2026-10-09 while Ollama.app had quit).
        self._ready = ready
        self._eye = eye
        self._sink = claim_sink
        self._spot = spotlight
        self._clock = clock
        self._device = device_id
        self._model = model
        self._log = log or (lambda m: None)
        self.passes = 0
        self.frames_seen = 0
        self.claims_written = 0
        self.failed = 0
        self.last_skip = ""

    # -- the decision ------------------------------------------------------

    def why_not(self) -> str:
        """Why this pass will not run right now, or "" if it will.

        A sentence rather than a bool, for the same reason the night's is:
        "it just didn't" costs an afternoon. This one has cost several.
        """
        if len(self._buf) == 0:
            return "no frames banked"
        if getattr(self._spot, "capture_active", False):
            return "the eye is open — the GPU belongs to capture"
        if not self._spot.is_uncontended():
            return "another turn holds the GPU"
        if self._ready is not None and not self._ready():
            return "the model server is down — frames kept for later"
        return ""

    # -- the pass ----------------------------------------------------------

    def run(self) -> Dict[str, int]:
        """Understand what is banked. Returns what actually happened."""
        skip = self.why_not()
        if skip:
            self.last_skip = skip
            return {"ran": 0, "frames": 0, "claims": 0, "yielded": 0,
                    "failed": 0, "skipped": skip}

        # TAKEN BEFORE THE GPU IS ACQUIRED, ON PURPOSE. If the acquire raises
        # the frames are already out of the buffer and are put back — but if
        # the process dies mid-pass, pixels are freed rather than held by a
        # buffer nobody is draining. The failure direction for pixels is
        # always "gone".
        frames = self._buf.take_all()
        done = failed = yielded = 0
        reached = 0          # frames consumed, understood or not
        written: List = []
        try:
            with self._spot.acquire(self._model, role=MACRO_ROLE, priority=0):
                for frame in frames:
                    # POLLED BETWEEN FRAMES, NOT WITHIN ONE. A VLM call is
                    # atomic to us; the granularity of yielding is a frame.
                    if self._spot.preempted():
                        yielded = len(frames) - reached
                        self._log("macro pass yielded to a query with %d "
                                  "frame(s) left" % yielded)
                        break
                    try:
                        written.extend(self._one(frame))
                        done += 1
                    except (ConnectionError, OSError) as exc:
                        # THE SERVER, NOT THE FRAME. A refused or reset
                        # connection (urllib's URLError is an OSError) says
                        # nothing about this frame, so neither it nor the
                        # rest are spent on it: they go back for the next
                        # pass. A timeout is the wedged-model class and is
                        # charged to the frame like any other failure.
                        if not (isinstance(exc, TimeoutError)
                                or "timed out" in str(exc)):
                            yielded = len(frames) - reached
                            self._log("macro pass stopped, model server "
                                      "unreachable: %s" % exc)
                            break
                        failed += 1
                        self._log("macro frame %s timed out: %s"
                                  % (frame.frame_id, exc))
                    except Exception as exc:                 # noqa: BLE001
                        # One frame's failure is one frame's failure.
                        failed += 1
                        self._log("macro frame %s failed: %s: %s"
                                  % (frame.frame_id, type(exc).__name__, exc))
                    reached += 1
        except SpotlightBusy as exc:
            # THE FRAMES GO BACK. A pass refused at the door must not consume
            # the session's only sample of the day.
            self._return(frames)
            self.last_skip = str(exc)
            return {"ran": 0, "frames": 0, "claims": 0, "yielded": 0,
                    "failed": 0, "skipped": str(exc)}

        # Whatever was not reached goes back for the next pass; whatever was
        # tried does not, or the next pass would say it all again (or fail
        # on the same bad frame for ever).
        if yielded:
            self._return(frames[reached:])
        self.failed += failed

        self.passes += 1
        self.frames_seen += done
        self.claims_written += len(written)
        self.last_skip = ""
        self._log("macro pass: %d frame(s) understood, %d claim(s), "
                  "%d yielded, %d failed" % (done, len(written), yielded,
                                             failed))
        return {"ran": 1, "frames": done, "claims": len(written),
                "yielded": yielded, "failed": failed, "skipped": ""}

    def _one(self, frame) -> List:
        t0 = self._clock.now_ms()
        seen = self._eye.describe_bytes(frame.png)
        cost_s = max(0.0, (self._clock.now_ms() - t0) / 1000.0)
        claims = macro_claims(
            seen, t_ms=frame.t_ms, frame_id=frame.frame_id,
            device_id=self._device, app=frame.app,
            place_key=frame.place_key, actual_cost=cost_s)
        for c in claims:
            self._sink(c)
        return claims

    def _return(self, frames) -> None:
        for f in frames:
            self._buf.offer(f.frame_id, f.png, f.t_ms, app=f.app,
                            place_key=f.place_key, scene_key=f.scene_key)

    def stats(self) -> Dict[str, object]:
        out = {"passes": self.passes, "frames_seen": self.frames_seen,
               "frames_failed": self.failed,
               "claims_written": self.claims_written,
               "not_running_because": self.last_skip or self.why_not()}
        out.update(self._buf.stats())
        return out
