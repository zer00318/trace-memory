"""OWNS: the eval-only frame bank: ground truth that does not come from TRACE.

FOUNDER RULING (2026-10-20): frames are saved from time to time so the
memory can be examined against what was really on screen. The
falsification experiment (evalx) may only be scored on questions written
from lived truth, never from the store, because a question written by
reading the store can only flatter it. A banked frame is that truth: the
questions and their answers are written from pixels, and the store is then
asked blind.

WHAT THIS IS NOT. It is not memory. Nothing here is a claim, nothing is
bound, nothing is ever answered from. The frames stay on the Mac that
captured them (F-PILLARS keeps pixels on their device), under `gold/`, which
git ignores and the publish gate never reads; files are owner-only, capped
at GOLD_MAX (oldest removed first), and an app on the blocklist is never
banked because the eye's own exclusion check runs before this.
Opt out with "gold_frames": false in ~/.trace/settings.json.

Every number here is UNCALIBRATED: the cadence matches tools/gold_capture's
historical --auto default, the cap keeps the bank to a few hundred MB.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

GOLD_EVERY_MS = 4 * 60 * 1000
GOLD_MAX = 1000
GOLD_MAX_SIDE = 1600          # text must stay readable to whoever writes questions
GOLD_QUALITY = 80


class GoldBank:
    def __init__(self, root: Path, every_ms: int = GOLD_EVERY_MS,
                 max_frames: int = GOLD_MAX) -> None:
        self._root = Path(root)
        self._every = every_ms
        self._max = max_frames
        self._last_ms: Optional[int] = None
        self.banked = 0
        self.errors = 0

    def due(self, t_ms: int) -> bool:
        return self._last_ms is None or t_ms - self._last_ms >= self._every

    def maybe_bank(self, payload, t_ms: int, app: Optional[str],
                   window: Optional[str] = None) -> Optional[Path]:
        if self._last_ms is not None and t_ms - self._last_ms < self._every:
            return None
        self._last_ms = t_ms
        try:
            from tracemem.extraction.frame_encode import encode_bgra_jpeg
            jpeg = encode_bgra_jpeg(payload, max_side=GOLD_MAX_SIDE,
                                    quality=GOLD_QUALITY)
            self._root.mkdir(parents=True, exist_ok=True)
            os.chmod(self._root, 0o700)
            stem = "live_%d" % int(t_ms)
            image = self._root / (stem + ".jpg")
            meta = self._root / (stem + ".json")
            for path, data in ((image, jpeg), (meta, json.dumps({
                    "id": stem, "class": "live", "t_ms": int(t_ms),
                    "app": app or "", "window": window or "",
                    "coord_space": "pixels_topleft"}).encode())):
                fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
            self.banked += 1
            self._prune()
            return image
        except Exception:                                   # noqa: BLE001
            self.errors += 1             # an eval aid must never cost the eye a beat
            return None

    def _prune(self) -> None:
        frames = sorted(self._root.glob("live_*.jpg"))
        for old in frames[: max(0, len(frames) - self._max)]:
            old.unlink(missing_ok=True)
            old.with_suffix(".json").unlink(missing_ok=True)
