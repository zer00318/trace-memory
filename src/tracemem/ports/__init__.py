"""M0 ports — the swap and trust seams (spec 00 §9).

PORT1: ClaimStore deliberately has NO update/delete — raw immutability is an
interface-level fact; no implementation can offer mutation.
"""
from tracemem.ports.protocols import (
    ASR,
    OCR,
    ClaimStore,
    Clock,
    Crypto,
    Embedder,
    GpuLease,
    GraphStore,
    Perceiver,
    Reasoner,
    Specialist,
    VLMRouter,
)

__all__ = [
    "ASR", "OCR", "ClaimStore", "Clock", "Crypto", "Embedder", "GpuLease",
    "GraphStore", "Perceiver", "Reasoner", "Specialist", "VLMRouter",
]
