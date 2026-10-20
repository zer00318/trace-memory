"""OWNS: gist -> activity routing (spec 01 §7).

The mac app-map short-circuits the VLM entirely: when the frontmost app names
the activity, classification costs ZERO GPU. The VLM runs only for ambiguous
frames (phone camera, unknown apps). Returns (activity, vlm_calls) so tests
can pin the zero.
"""
from __future__ import annotations

from typing import Optional, Tuple

APP_SPECIALIST_MAP = {
    "vs code": "code", "xcode": "code", "terminal": "code", "iterm": "code",
    "preview": "document", "pages": "document", "acrobat": "document",
    "chess": "board", "lichess": "board",
}


def route(frame, ctx: dict, vlm, app_map: Optional[dict] = None) -> Tuple[str, int]:
    table = dict(APP_SPECIALIST_MAP)
    table.update(app_map or {})
    app = (ctx.get("app") or "").lower()
    for needle, activity in sorted(table.items()):
        if needle in app:
            return activity, 0                      # the free path — zero VLM
    if vlm is None:
        return "base", 0                            # no classifier available: honest fallback
    return str(vlm.classify(frame)), 1
