"""OWNS: boot-time honesty — the hole is written down before anything else.

Pure decision logic (store-agnostic): given when the last claim landed,
what the last witness said, and now, produce the gap witness the boot
must write — or None when the silence is explainable. The Negative
question class and the coverage audit consume what this writes.
"""
from __future__ import annotations

from typing import Optional

from tracemem.app.witness import WitnessKind, witness
from tracemem.domain import Claim

SLEEP_EXPLAINABLE_MS = 12 * 60 * 60 * 1000   # ≤12h after a clean stop = a night
SHORT_BLIP_MS = 2 * 60 * 1000                 # <2min needs no ceremony


def detect_gap(last_claim_t_ms: Optional[int], last_witness_kind: Optional[WitnessKind],
               now_ms: int, device_id: str = "mac-1") -> Optional[Claim]:
    """Returns the witness to append at boot, or None.

    Rules, in order:
    - empty store: START only (day one is not a gap).
    - blip (<2min): no gap ceremony, the START witness suffices.
    - clean stop / founder pause ≤12h: expected silence — witnessed as a
      gap but attributed to the stop, cause carried in structured.
    - anything else: KILL_DETECTED gap — we died without saying goodbye,
      and the hole says so.
    """
    if last_claim_t_ms is None:
        return None
    span = now_ms - last_claim_t_ms
    if span < SHORT_BLIP_MS:
        return None
    expected = last_witness_kind in (WitnessKind.CLEAN_STOP, WitnessKind.FOUNDER_PAUSE)
    if expected and span <= SLEEP_EXPLAINABLE_MS:
        cause = last_witness_kind.value
        kind = WitnessKind.GAP
    else:
        cause = ("overlong-" + last_witness_kind.value) if expected else "no-clean-stop"
        kind = WitnessKind.KILL_DETECTED if not expected else WitnessKind.GAP
    hours = span / 3_600_000
    return witness(kind, t_ms=last_claim_t_ms,
                   detail=f"not watching for {hours:.1f}h (cause: {cause})",
                   span_t2_ms=now_ms, device_id=device_id,
                   structured_extra={"cause": cause, "span_ms": span})
