"""Tournament contenders — Designs A/B/C behind the common arena interface.

specs/node_tournament.md defines the contract; specs/node_designs/ define
each engine. Design 0 (the existing brain) competes via its own adapter,
wired at arena time — it is NOT imported here (bias law: droppable).
"""
from tracemem.contenders.base import Contender, EvidenceBlock, EvidencePackage, StorageReport
from tracemem.contenders.design_a import UnderstandingMemory
from tracemem.contenders.design_b import FutureAnswerMemory
from tracemem.contenders.design_c import WiringMemory

__all__ = [
    "Contender", "EvidenceBlock", "EvidencePackage", "StorageReport",
    "UnderstandingMemory", "FutureAnswerMemory", "WiringMemory",
]
