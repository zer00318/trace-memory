"""ORACLE for The drill scripts (12_proof): "Every script runs green once."

Also guards the property that makes the drill suite trustworthy — a drill
that cannot run must FAIL, never pass vacuously. A green line that silently
covers an undrilled disaster is worse than no drill at all.
"""
import pytest

from tools.drills import DRILLS, RUNNABLE, DrillNotRunnable, main


@pytest.mark.parametrize("name", RUNNABLE)
def test_each_runnable_drill_passes_with_evidence(name):
    ok, evidence = DRILLS[name]()
    assert ok, f"{name} failed: {evidence}"
    assert evidence and evidence != "ok", "a drill must report an observation"


@pytest.mark.parametrize("name", ["permissions", "sleep_storm", "gpu_contention"])
def test_an_unstageable_drill_refuses_rather_than_passing(name):
    with pytest.raises(DrillNotRunnable):
        DRILLS[name]()


def test_the_runner_exits_nonzero_when_a_drill_cannot_run():
    assert main(["permissions"]) == 1


def test_the_runner_exits_zero_when_the_runnable_set_passes():
    assert main(list(RUNNABLE)) == 0


def test_every_drill_is_classified():
    unclassified = [n for n in DRILLS if n not in RUNNABLE]
    for n in unclassified:
        with pytest.raises(DrillNotRunnable):
            DRILLS[n]()
