"""All port protocols in one module (spec 00 §9).

One file rather than twelve: a protocol is 3 lines; the seam is the TYPE, not
the file. (Deviation from the tree doc, recorded: spec 09 A8 scaffold ruling.)
"""
from __future__ import annotations

from typing import Any, ContextManager, Iterable, List, Optional, Protocol, Sequence, Tuple

from tracemem.domain.claim import Claim
from tracemem.domain.node import Edge, Node


class Perceiver(Protocol):
    def perceive(self, frame: Any, ctx: Any) -> List[Claim]: ...


class OCR(Protocol):
    def read(self, image: Any) -> List[dict]: ...          # {text, bbox, confidence}


class ASR(Protocol):
    def transcribe(self, audio: Any) -> List[dict]: ...    # {text, t0_ms, t1_ms, channel}


class VLMRouter(Protocol):
    def gist(self, frame: Any) -> str: ...                 # activity label


class Specialist(Protocol):
    def load(self) -> None: ...
    def evict(self) -> None: ...
    def read(self, frame: Any) -> List[dict]: ...          # structured reads


class Embedder(Protocol):
    def embed(self, text: str) -> Sequence[float]: ...


class ClaimStore(Protocol):
    """Append-only. NO update. NO delete. (PORT1 — raw immutability at the seam.)"""
    def append(self, claim: Claim) -> None: ...
    def get(self, claim_id: str) -> Optional[Claim]: ...
    def read_range(self, t0_ms: int, t1_ms: int,
                   place_key: Optional[str] = None) -> Iterable[Claim]: ...


class GraphStore(Protocol):
    def get_node(self, node_id: str) -> Optional[Node]: ...
    def upsert_node(self, node: Node) -> None: ...
    def delete_meta(self, node_id: str) -> None: ...       # META only; raw has no delete anywhere
    def retire_meta(self, node_id: str, t_ms: int) -> None: ...  # META only; tombstones then removes (the moat)
    def retired(self) -> Iterable: ...                     # (node, retired_t_ms) — the queryable graveyard
    def merge_into(self, keep_id: str, absorb_id: str, t_ms: int) -> None: ...  # deliberate identity merge (may remove depth-0)
    def edges_of(self, node_id: str) -> Iterable[Edge]: ...
    def nodes_with_activation_ge(self, floor: float, t_ms: int) -> Iterable[Node]: ...
    def ledger_fresh(self, pair_key: str) -> bool: ...
    def record_ledger(self, pair_key: str, outcome: str, fingerprint: str) -> None: ...


class Reasoner(Protocol):
    def complete(self, prompt: str) -> str: ...


class Crypto(Protocol):
    def encrypt(self, header: bytes, plaintext: bytes) -> Tuple[bytes, bytes]: ...  # nonce, ct
    def decrypt(self, header: bytes, nonce: bytes, ciphertext: bytes) -> bytes: ...


class Clock(Protocol):
    def now_ms(self) -> int: ...


class GpuLease(Protocol):
    def acquire(self, model: str, role: str, priority: int) -> ContextManager[None]: ...
    def preempted(self) -> bool: ...
    def is_uncontended(self) -> bool: ...
