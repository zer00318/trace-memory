#!/usr/bin/env python3
"""THE FIRST LIVING BEAT — live screen → change → region → OCR → Claims.

The spine's organs, breathing together on the real screen for the first
time: SCStream (reading eye, low fps) → gray-grid downscale →
Peripheral change detection → region extraction → pixel crop →
Vision OCR → stamped Claims → cross-frame merge + semantic newness.

HONEST SCOPE: this is the PRE-AUCTION straight-line beat — every changed
region gets looked at (no bidding, no meal-prep conditioning, no store
writes). The auction joins in the daemon wiring; this run proves the
organs compose and counts what a living minute actually yields.

Usage: .venv/bin/python tools/first_beat.py [seconds]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import os                                                              # noqa: E402

from tracemem.capture.retina_screen import SCStreamSession           # noqa: E402
from tracemem.extraction.attention_mac import attention_span          # noqa: E402
from tracemem.text.claim_log import ClaimLog                          # noqa: E402
from tracemem.capture.capture_config import GRID_X, GRID_Y            # noqa: E402
from tracemem.extraction.l1_claims import runs_to_claims              # noqa: E402
from tracemem.extraction.l1_read import ScreenRun, recognize_pixels   # noqa: E402
from tracemem.extraction.auction import Auction                       # noqa: E402
from tracemem.extraction.diet import DietRegistry                     # noqa: E402
from tracemem.extraction.media_suppress import MediaSuppressor        # noqa: E402
from tracemem.extraction.newness import SaidThings                    # noqa: E402
from tracemem.extraction.peripheral import Peripheral                 # noqa: E402
from tracemem.extraction.region_extract import extract                # noqa: E402
from tracemem.extraction.text_merge import TextMerger                 # noqa: E402


class _BagEmbedder:
    """Beat-one stand-in (token-bag vector) — MiniLM joins via the
    supplier's embedder when the daemon wires organs; labeled honestly."""
    def embed(self, text):
        vec = [0.0] * 128
        for tok in text.lower().split():
            vec[hash(tok) % 128] += 1.0
        return vec


def gray_grid(payload):
    """BGRA payload → GRID_Y x GRID_X gray grid by sparse sampling."""
    w, h, bpr, data = (payload.width, payload.height,
                       payload.bytes_per_row, payload.data)
    grid = []
    for gy in range(GRID_Y):
        row = []
        yc = (gy * h) // GRID_Y + h // (2 * GRID_Y)
        for gx in range(GRID_X):
            xc = (gx * w) // GRID_X + w // (2 * GRID_X)
            acc = 0                        # 4x4 block average: one flickering
            for dy in (-2, -1, 0, 1):      # pixel can no longer fake a change
                base = (yc + dy) * bpr
                for dx in (-2, -1, 0, 1):
                    o = base + (xc + dx) * 4
                    acc += (data[o + 2] * 3 + data[o + 1] * 6 + data[o]) // 10
            row.append(acc // 16)
        grid.append(row)
    return grid


def main(seconds: int = 60) -> None:
    stats = {"frames": 0, "events": 0, "muted_tiles": 0, "regions": 0,
             "ocr_calls": 0, "claims": 0, "novel": 0, "mute_events": 0}
    merger, said = TextMerger(), SaidThings(_BagEmbedder())
    peripheral = Peripheral()
    media = MediaSuppressor()
    auction = Auction(DietRegistry(), prepare=lambda region: region)
    samples = []
    STORE = "/tmp/trace_beat_store.sqlite3"     # SHADOW store — never the live one
    if os.path.exists(STORE):
        os.remove(STORE)
    holder = {}     # ClaimLog born INSIDE the capture thread (sqlite3 refuses
                    # cross-thread use; the stream's serial queue is one thread)

    def on_frame(rec):
        try:
            _beat(rec)
        except Exception as e:                  # the ObjC delegate swallows
            stats["errors"] = stats.get("errors", 0) + 1   # exceptions SILENTLY —
            stats["last_error"] = repr(e)[:120]            # never let that hide

    def _beat(rec):
        stats["frames"] += 1
        p = rec.frame
        events = peripheral.observe(gray_grid(p), rec.t_ms)
        changes = [e for e in events if e.kind == "change"]
        stats["events"] += len(changes)
        if not changes:
            return
        tiles = [(e.region[0], e.region[1]) if isinstance(e.region, tuple)
                 else (e.region.x, e.region.y) for e in changes]
        allowed = set(media.filter_changes([(t, rec.t_ms) for t in tiles]))
        stats["mute_events"] = sum(1 for v in media._muted.values() if v)
        stats["muted_tiles"] += len(tiles) - len(allowed)
        if not allowed:
            return
        # THE REAL ECONOMY: suppression gates the world's hand; the auction
        # prices what remains; winner-take-all picks ONE gaze per beat.
        auction.on_events([e for e in changes
                           if (e.region if isinstance(e.region, tuple)
                               else (e.region.x, e.region.y)) in allowed])
        gaze = auction.beat(rec.t_ms)
        if gaze is None:
            return
        stats["regions"] += 1
        gx, gy = gaze.region                               # tile-grained fovea:
        col0, col1 = max(0, gx - 1), min(GRID_X - 1, gx + 1)   # 3x3 crop around
        row0, row1 = max(0, gy - 1), min(GRID_Y - 1, gy + 1)   # the winner
        x0 = (col0 * p.width) // GRID_X
        x1 = ((col1 + 1) * p.width) // GRID_X
        y0 = (row0 * p.height) // GRID_Y
        y1 = ((row1 + 1) * p.height) // GRID_Y
        rows = bytearray()
        for y in range(y0, y1):
            base = y * p.bytes_per_row + x0 * 4
            rows += p.data[base: base + (x1 - x0) * 4]
        stats["ocr_calls"] += 1
        runs = recognize_pixels(x1 - x0, y1 - y0, (x1 - x0) * 4, bytes(rows))
        screen_runs = [ScreenRun(r.text, x0 + r.x * (x1 - x0), y0 + r.y * (y1 - y0),
                                 r.w * (x1 - x0), r.h * (y1 - y0), r.confidence)
                       for r in runs]
        # The focus sense stamps the gaze: WHICH app the words lived in.
        # Perception failure never kills the beat — it degrades to "unknown".
        try:
            span = attention_span(rec.t_ms)
            app = span.app or "unknown"
            stats["focused"] = stats.get("focused", 0) + (1 if span.app else 0)
        except Exception:
            app = "unknown"
        claims = runs_to_claims(
            screen_runs, frame_w=p.width, frame_h=p.height, t_ms=rec.t_ms,
            frame_id=f"beat-{stats['frames']}", device_id="mac-1", app=app,
            place_key="wifi:unknown", bidder="peripheral:change",
            bid_id=f"beat:{rec.t_ms}", bid_predicted_value=1.0, actual_cost=0.0)
        stats["claims"] += len(claims)
        if "log" not in holder:
            holder["log"] = ClaimLog(STORE)
        for c in claims:
            holder["log"].append(c)             # durable, stamp-gated, immutable
            m = merger.observe(c.text, c.coord.span.bbox, c.confidence.value
                               if hasattr(c.confidence, "value") else 0.9, rec.t_ms)
            if m.sightings == 1 and said.check_and_record("live", c.text,
                                                          bbox=c.coord.span.bbox):
                stats["novel"] += 1
                if len(samples) < 8:
                    samples.append(c.text[:70])

    session = SCStreamSession(on_frame=on_frame, fps=2.0, capture_geometry=True)
    session.start()
    t0 = time.time()
    try:
        while time.time() - t0 < seconds:
            time.sleep(0.25)
    finally:
        session.stop()
    dur = time.time() - t0
    print(f"FIRST BEAT — {dur:.0f}s live: frames={stats['frames']} "
          f"changes={stats['events']} regions={stats['regions']} "
          f"ocr={stats['ocr_calls']} muted_now={stats['mute_events']} "
          f"muted_tiles={stats['muted_tiles']} claims={stats['claims']} "
          f"novel={stats['novel']} errors={stats.get('errors', 0)}")
    if stats.get("last_error"):
        print("  last_error:", stats["last_error"])
    for s in samples:
        print("  novel:", s)
    # durability: a FRESH connection must find what the beat lived
    reopened = ClaimLog(STORE)
    print(f"DURABLE: reopened store holds {reopened.count()} claims")
    apps = sorted({c.coord.capture.app for c in reopened.all_claims()
                   if c.coord.capture.app})
    print(f"FOCUSED: claims carry real apps -> {apps or ['(none)']} "
          f"({stats.get('focused', 0)} gazes stamped by the focus sense)")
    if samples:
        needle = max(samples[0].split(), key=len).lower()
        hit = any(needle in c.text.lower() for c in reopened.all_claims())
        print(f"FINDABLE: '{needle}' {'found' if hit else 'MISSING'} after reopen")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 60)
