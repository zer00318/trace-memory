"""OWNS: the frames the macro eye will look at, held in memory and never
written down.

WHY THIS EXISTS AT ALL. The OCR eye reads the WORDS on screen and recovers
~96% of the frontier's verbatim claims. It does not produce UNDERSTANDING —
what object is on screen, what the person is doing, what follows from it.
That tier is 21% of the frontier ceiling and the OCR-only path recalls 0.17
of it (tools/context_gap.py). `extraction/vlm_macro.VlmMacroEye` was built to
close that gap and has never run outside the measuring tool, because of a
scheduling knot:

  · the VLM is a 12B model on the Metal GPU;
  · heavy GPU work beside live capture makes capture GO DARK SILENTLY —
    the scheduler encodes this as a hard refusal (`role == "DREAM"` while
    `capture_active`), and it is the single most expensive failure this
    product has had;
  · so the macro eye can only run while the eye is CLOSED;
  · but a closed eye has no frames to look at.

Hence a buffer. The frames worth understanding are seen while the founder is
present; the GPU is free once they leave.

WHY IT IS MEMORY AND NOT DISK. F-PILLARS: "pixels and waveforms die on the
device that captured them, exactly as they already do on the Mac." Today,
at any instant, there are ZERO screenshots on this machine's disk. That is a
simple promise anyone can check with `find`. A disk-backed bank would trade
it for "up to N screenshots for up to M minutes", which is a different and
much weaker promise, and not one an agent should quietly make on the
founder's behalf. In memory the frames die with the process — a crash, a
restart or a `kill -9` all take them, which is the correct failure direction
for pixels.

The cost is honest and worth stating: frames from before the last daemon
restart are gone, and only a bounded sample of any session gets understood.
Both are visible in `stats()`.

THE CALLER OWNS ENCODING. This holds whatever bytes it is handed. A 5K
retina PNG is ~8MB and twelve of them would be 96MB of resident memory in a
daemon meant to run for years, so the capture side should hand it a
downscaled JPEG — a VLM does not need retina pixels to say "a chess board".
The byte ceiling here is a backstop, not the plan.
"""
from __future__ import annotations

from typing import Dict, List, Optional

# Twelve frames is a session's worth of DISTINCT scenes, not a session's
# worth of seconds — see `offer`, which refuses a scene it already holds.
MAX_FRAMES = 12
# ~48MB. A backstop against a caller that hands over full retina PNGs; at
# the intended downscaled-JPEG size (~200KB) it never binds.
MAX_BYTES = 48 * 1024 * 1024


class Frame:
    __slots__ = ("frame_id", "png", "t_ms", "app", "place_key", "scene_key")

    def __init__(self, frame_id, png, t_ms, app, place_key, scene_key):
        self.frame_id = frame_id
        self.png = png
        self.t_ms = t_ms
        self.app = app
        self.place_key = place_key
        self.scene_key = scene_key

    def __repr__(self):                                      # pragma: no cover
        return "Frame(%s, t=%s, scene=%s)" % (self.frame_id, self.t_ms,
                                              self.scene_key)


class MacroBuffer:
    """A bounded, temporally-spread sample of the scenes seen this session."""

    def __init__(self, max_frames: int = MAX_FRAMES,
                 max_bytes: int = MAX_BYTES) -> None:
        if max_frames < 2:
            # Thinning needs an interior to thin. One-frame buffers would
            # also make the spread policy meaningless.
            raise ValueError("a macro buffer below 2 frames cannot spread")
        self._max_frames = max_frames
        self._max_bytes = max_bytes
        self._frames: List[Frame] = []
        self.offered = 0
        self.deduped = 0
        self.thinned = 0
        self.drained = 0

    # -- writing ----------------------------------------------------------

    def offer(self, frame_id: str, png: bytes, t_ms: int, *,
              app: str = "", place_key: str = "",
              scene_key: Optional[str] = None) -> bool:
        """Offer one frame. Returns whether it was kept.

        Called from the capture beat, so it must stay cheap: a dict lookup
        and maybe a list operation. No encoding, no I/O, no model.
        """
        self.offered += 1
        if not png:
            return False
        key = scene_key if scene_key is not None else frame_id
        # THE SAME SCENE TWICE TEACHES NOTHING. A founder reading one page
        # for twenty minutes is one scene; without this the buffer fills with
        # twelve copies of it and the rest of the day is never understood.
        for f in self._frames:
            if f.scene_key == key:
                self.deduped += 1
                return False
        self._frames.append(Frame(frame_id, png, t_ms, app, place_key, key))
        self._enforce()
        return True

    def _enforce(self) -> None:
        while (len(self._frames) > self._max_frames
               or self.nbytes > self._max_bytes):
            if len(self._frames) <= 2:
                # Never thin away the endpoints; a two-frame buffer over the
                # byte ceiling means one enormous frame, and dropping the
                # newest is better than emptying the buffer entirely.
                self._frames.pop()
                self.thinned += 1
                return
            self._frames.pop(self._closest_pair_index())
            self.thinned += 1

    def _closest_pair_index(self) -> int:
        """Which interior frame to drop: the one in the tightest cluster.

        DROPPING THE OLDEST WOULD BE THE OBVIOUS CHOICE AND IT IS WRONG. A
        FIFO ends the day holding the last twelve scenes — the founder's
        evening, over and over, and never their morning. What is wanted is a
        sample SPREAD ACROSS the session, so understanding covers the whole
        day at low resolution rather than the last hour at high resolution.

        So the frame discarded is the one whose neighbours are nearest in
        time: removing it costs the least coverage. Endpoints are never
        candidates — they are the session's bounds.
        """
        best_i, best_gap = 1, None
        for i in range(1, len(self._frames) - 1):
            gap = self._frames[i + 1].t_ms - self._frames[i - 1].t_ms
            if best_gap is None or gap < best_gap:
                best_i, best_gap = i, gap
        return best_i

    # -- reading ----------------------------------------------------------

    def take_all(self) -> List[Frame]:
        """Hand over every frame and forget them. The pixels leave with the
        caller and this buffer holds nothing afterwards — a drain that
        half-fails must not leave frames resident for the next hour."""
        out, self._frames = self._frames, []
        self.drained += len(out)
        return out

    def __len__(self) -> int:
        return len(self._frames)

    @property
    def nbytes(self) -> int:
        return sum(len(f.png) for f in self._frames)

    def stats(self) -> Dict[str, int]:
        """The honest cost, visible. `deduped` and `thinned` are how much of
        the session this eye never saw."""
        return {"held": len(self._frames), "bytes": self.nbytes,
                "offered": self.offered, "deduped": self.deduped,
                "thinned": self.thinned, "drained": self.drained}
