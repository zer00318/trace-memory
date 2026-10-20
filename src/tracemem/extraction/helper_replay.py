"""Night replay for durable text-helper work.

Only TEXT helpers are admitted by helper API v1, so replay reads the immutable
parent claims named by the task witness.  No discarded pixels are recreated
or guessed.  Output uses the original observation coordinate and an idempotent
task-derived bid id; a crash after append but before the completion receipt is
therefore safe to retry.
"""
from __future__ import annotations

from tracemem.domain import CaptureCoord, CoordinateStack, SpanCoord, WorldCoord
from tracemem.extraction.claim_builder import build_claim


def replay_pending(store, runtime, absorb, limit: int = 8) -> dict:
    completed = produced = failed = 0
    for task in store.pending_helper_tasks(limit=limit):
        task_id = task["task_claim_id"]
        parents = store.read_ids(task["parent_claim_ids"])
        if len(parents) != len(task["parent_claim_ids"]):
            store.fail_helper_task(task_id, "one or more parent claims are unreadable")
            failed += 1
            continue
        parents_by_id = {c.id: c for c in parents}
        parents = [parents_by_id[cid] for cid in task["parent_claim_ids"]]
        first = parents[0] if parents else None
        if first is None:
            store.fail_helper_task(task_id, "deferred task has no parent claims")
            failed += 1
            continue
        app = first.coord.capture.app or "unknown"
        text = " ".join(c.text for c in parents)
        report = runtime.run(
            text, app,
            {"frame_id": first.coord.capture.frame_id,
             "window": first.coord.capture.window,
             "t_ms": first.coord.t_ms, "replay_task": task_id},
            only=task["helper_ids"])
        if report.failures or report.deferred:
            why = "; ".join(report.failures + report.deferred)
            store.fail_helper_task(task_id, why or "helper remained deferred")
            failed += 1
            continue
        boxes = [c.coord.span.bbox for c in parents if c.coord.span.bbox]
        bbox = ((min(b[0] for b in boxes), min(b[1] for b in boxes),
                 max(b[2] for b in boxes), max(b[3] for b in boxes))
                if boxes else None)
        for index, reading in enumerate(report.readings):
            coord = CoordinateStack(
                world=WorldCoord(place_key=first.coord.world.place_key,
                                 gps=first.coord.world.gps,
                                 pose=first.coord.world.pose),
                capture=CaptureCoord.lived(
                    first.coord.capture.frame_id,
                    first.coord.capture.device_id, app=app,
                    window=first.coord.capture.window),
                span=SpanCoord(bbox=bbox), t_ms=first.coord.t_ms)
            structured = dict(reading.structured)
            structured.update({
                "helper_id": reading.helper_id,
                "helper_version": reading.version,
                "parent_claim_ids": [c.id for c in parents],
                "replayed_from_task": task_id,
            })
            absorb(build_claim(
                {"text": reading.text, "confidence": reading.confidence,
                 "modality": first.provenance.modality.value,
                 "source_ids": [coord.capture.frame_id],
                 "structured": structured},
                coord, "specialist:%s@%s" %
                (reading.helper_id, reading.version),
                bidder="helper:%s@%s" %
                (reading.helper_id, reading.version),
                bid_id="helper-replay:%s:%d" % (task_id, index),
                bid_predicted_value=reading.priority,
                actual_cost=reading.duration_s, scope="frame"))
            produced += 1
        store.finish_helper_task(task_id)
        completed += 1
    return {"completed": completed, "produced": produced, "failed": failed}
