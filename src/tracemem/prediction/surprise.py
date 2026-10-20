"""OWNS: prediction-error -> salience in [0,1] (spec 04 §2, P5.4).

Surprise is 1 - P(observed | slot), rescaled against the model's own best
guess so a uniform-ignorance model yields mid surprise, not max — an empty
model must not flood the graph with fake salience.
"""
from __future__ import annotations

from tracemem.prediction.model import RoutineModel, slot_of


def score(observed, context, model: RoutineModel) -> float:
    """observed: a place_key (or object with .place_key). context: t_ms int
    or object with .t_ms."""
    place = observed if (observed is None or isinstance(observed, str)) \
        else observed.place_key
    t_ms = context if isinstance(context, int) else context.t_ms
    s = slot_of(t_ms)
    dist = model.predict(t_ms)
    if not dist:
        return 0.5                       # ignorance is mid, never max (no fake salience)
    p_obs = model.prob(place, s)
    p_best = max(dist.values())
    return max(0.0, min(1.0, 1.0 - p_obs / p_best))
