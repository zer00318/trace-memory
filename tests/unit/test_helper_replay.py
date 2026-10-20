"""Deferred helper work is durable, retryable, and idempotent."""
from __future__ import annotations

from tests.helpers import make_claim
from tracemem.app.witness import WitnessKind, witness
from tracemem.extraction.helper_replay import replay_pending
from tracemem.extraction.helper_runtime import HelperReading, HelperRun
from tracemem.text.claim_log import ClaimLog


class Runtime:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def run(self, text, app, context, only=None):
        self.calls.append((text, app, context, tuple(only or ())))
        if self.fail:
            return HelperRun(failures=("sandbox timed out",))
        return HelperRun(readings=(HelperReading(
            "com.trace.reference.code-symbols", "1.0.0",
            "Code symbols: MemoryCell", .98,
            {"symbols": ["MemoryCell"]}, .01, 1.0),))


def deferred(parent):
    return witness(
        WitnessKind.HELPER_DEFERRED, parent.coord.t_ms,
        detail="heavy helper turn unavailable",
        structured_extra={
            "organ": "eye", "frame_id": parent.coord.capture.frame_id,
            "helpers": ["com.trace.reference.code-symbols@1.0.0"],
            "parent_claim_ids": [parent.id], "replay_state": "owed"})


def test_deferred_task_survives_restart_then_replays_from_immutable_text(tmp_path):
    path = str(tmp_path / "raw.sqlite3")
    first = ClaimLog(path)
    parent = make_claim("class MemoryCell", t_ms=10, app="Editor")
    task = deferred(parent)
    first.append(parent)
    first.append(task)

    reopened = ClaimLog(path)
    assert reopened.pending_helper_tasks()[0]["task_claim_id"] == task.id
    runtime = Runtime()
    report = replay_pending(reopened, runtime, reopened.append)

    assert report == {"completed": 1, "produced": 1, "failed": 0}
    assert runtime.calls[0][0] == "class MemoryCell"
    assert runtime.calls[0][3] == (
        "com.trace.reference.code-symbols@1.0.0",)
    helper = next(c for c in reopened.all_claims()
                  if c.bidder.startswith("helper:"))
    assert helper.coord == parent.coord
    assert helper.structured["parent_claim_ids"] == [parent.id]
    assert helper.structured["replayed_from_task"] == task.id
    assert reopened.pending_helper_tasks() == []

    before = reopened.count()
    assert replay_pending(ClaimLog(path), Runtime(),
                          ClaimLog(path).append)["produced"] == 0
    assert ClaimLog(path).count() == before


def test_failed_replay_remains_pending_and_names_its_attempt(tmp_path):
    store = ClaimLog(str(tmp_path / "raw.sqlite3"))
    parent = make_claim("class MemoryCell", t_ms=10, app="Editor")
    store.append(parent)
    store.append(deferred(parent))

    report = replay_pending(store, Runtime(fail=True), store.append)

    assert report == {"completed": 0, "produced": 0, "failed": 1}
    pending = store.pending_helper_tasks()
    assert pending[0]["attempts"] == 1
    assert "sandbox timed out" in pending[0]["last_error"]


def test_task_projection_contains_no_parent_text_in_plaintext(tmp_path):
    import sqlite3
    path = tmp_path / "raw.sqlite3"
    store = ClaimLog(str(path))
    parent = make_claim("PRIVATE CLASS NAME", t_ms=10)
    store.append(parent)
    store.append(deferred(parent))

    con = sqlite3.connect(path)
    projected = repr(con.execute(
        "select parent_claim_ids,helper_ids,status,last_error from helper_task"
    ).fetchall())
    con.close()
    assert "PRIVATE CLASS NAME" not in projected


def test_brains_production_night_replays_then_binds_before_reasoning(tmp_path):
    from tests.fakes import ContextAnswerReasoner, FakeClock, HashEmbedder
    from tracemem.app.brain import Brain
    from tracemem.app.wiring import build

    clock = FakeClock(100)
    store = ClaimLog(str(tmp_path / "raw.sqlite3"))
    parent = make_claim("class MemoryCell", t_ms=10, app="Editor")
    store.append(parent)
    store.append(deferred(parent))
    hub = build({"claim_log": store, "clock": clock,
                 "embedder": HashEmbedder(),
                 "reasoner": ContextAnswerReasoner()})
    reached = {}

    def night(**_kw):
        reached["helper_already_bound"] = any(
            claim.bidder.startswith("helper:")
            for claims in hub.claims_by_episode.values() for claim in claims)
        return {"status": "COMPLETE"}

    hub.night = night
    brain = Brain(hub, store.append, clock.now_ms,
                  helper_runtime=Runtime())

    assert brain.night() == {"status": "COMPLETE"}
    assert reached["helper_already_bound"] is True
    assert brain.stats()["helper_replay"] == {
        "completed": 1, "produced": 1, "failed": 0}
