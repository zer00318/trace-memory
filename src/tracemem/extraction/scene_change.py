"""OWNS: activity-boundary detection (spec 01 §6).

Mac: an app change is a free, perfect boundary signal — no model runs.
Phone: embedding distance between consecutive frame gists. A place_key change
is a boundary on either device. ctx is a plain dict: {app, place_key}.
"""
from __future__ import annotations

SCENE_THRESHOLD = 0.35  # tunable (01 R1): cosine DISTANCE above this = new scene


def _cos(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


def is_new_activity(ctx, last_ctx, frame=None, last_frame=None,
                    embedder=None) -> bool:
    if last_ctx is None:
        return True                                  # first sight is a boundary
    if ctx.get("app") and ctx.get("app") != last_ctx.get("app"):
        return True                                  # mac path: free and exact
    if ctx.get("place_key") and last_ctx.get("place_key") and \
            ctx["place_key"] != last_ctx["place_key"]:
        return True                                  # room -> street
    if embedder is not None and frame is not None and last_frame is not None:
        dist = 1.0 - _cos(embedder.embed(frame), embedder.embed(last_frame))
        return dist > SCENE_THRESHOLD                # phone path: visual drift
    return False                                     # scroll within an app is NOT a boundary
