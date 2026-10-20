"""OWNS: bounded admission between capture and extraction (spec 01 §10, X5).

Capture must NEVER block on extraction. Beyond HARD the queue sheds the
LOWEST-signal frame (which may be the incoming one) and logs it — a shed is
a witnessed gap, not a silent loss. next() serves highest-signal first: under
pressure the most informative frames are read soonest.
"""
from __future__ import annotations

import heapq
from typing import List, Optional, Tuple

SOFT, HARD = 64, 256


class AdmissionQueue:
    def __init__(self, soft: int = SOFT, hard: int = HARD) -> None:
        self.soft, self.hard = soft, hard
        self._heap: List[Tuple[float, int, object]] = []   # (signal, seq, frame) min-heap
        self._seq = 0
        self.shed_count = 0
        self.shed_log: List[Tuple[float, object]] = []

    def depth(self) -> int:
        return len(self._heap)

    def admit(self, frame, verdict: float) -> bool:
        """verdict: the frame's signal score (change-gate magnitude). Returns
        False iff THIS frame was the one shed."""
        signal = float(verdict)
        heapq.heappush(self._heap, (signal, self._seq, frame))
        self._seq += 1
        if len(self._heap) > self.hard:
            shed_signal, _, shed_frame = heapq.heappop(self._heap)  # lowest signal goes
            self.shed_count += 1
            self.shed_log.append((shed_signal, shed_frame))
            return shed_frame is not frame
        return True

    def next(self) -> Optional[object]:
        """Highest-signal frame first."""
        if not self._heap:
            return None
        best = max(range(len(self._heap)), key=lambda i: self._heap[i][0])
        item = self._heap[best]
        self._heap[best] = self._heap[-1]
        self._heap.pop()
        heapq.heapify(self._heap)
        return item[2]
