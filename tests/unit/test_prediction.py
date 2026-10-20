"""Prediction: routine model, surprise scoring, routine+deviation compression."""
from __future__ import annotations

from tracemem.domain.episode import Episode
from tracemem.domain.time import TimeRange
from tracemem.prediction.compression import compress
from tracemem.prediction.model import MS_PER_DAY, RoutineModel
from tracemem.prediction.surprise import score

H = 3_600_000


def ep(day, hour, place, eid=None):
    t = day * MS_PER_DAY + hour * H
    return Episode(id=eid or "e-%d-%d" % (day, hour),
                   t_range=TimeRange(t, t + H), place_key=place)


def routine_week():
    eps = []
    for d in range(6):
        eps.append(ep(d, 9, "wifi:office"))
        eps.append(ep(d, 20, "wifi:home"))
    return eps


def test_routine_predicts_repeat_day_above_chance():
    m = RoutineModel().fit(routine_week())
    dist = m.predict(6 * MS_PER_DAY + 9 * H)            # day 7, 9am
    assert max(dist, key=dist.get) == "wifi:office"


def test_surprise_high_on_deviation_low_on_routine():
    m = RoutineModel().fit(routine_week())
    t = 6 * MS_PER_DAY + 9 * H
    assert score("wifi:office", t, m) < 0.2
    assert score("wifi:museum", t, m) > 0.6
    assert score("wifi:anywhere", 3 * H, m) == 0.5      # empty slot: mid, never max


def test_compression_folds_routine_keeps_deviation():
    m = RoutineModel().fit(routine_week())
    day7 = [ep(6, 9, "wifi:office"), ep(6, 20, "wifi:home"),
            ep(6, 9, "wifi:museum", eid="e-dev")]
    out = compress(m, day7)
    assert "e-dev" in out.deviations
    assert len(out.routine) == 2
    assert out.ratio < 0.5
