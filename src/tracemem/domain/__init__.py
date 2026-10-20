"""M0 domain — pure types. Illegal states unrepresentable (spec 00).

RAW types (Claim, Episode, CoordinateStack, ...) are frozen: no mutating
methods exist. META (Node/Edge) is the only mutable tier.
"""

from tracemem.domain.activation import activation_now, boost, spread_deltas
from tracemem.domain.claim import Claim
from tracemem.domain.confidence import Confidence
from tracemem.domain.coordinate import (
    CaptureCoord,
    CoordinateStack,
    SpanCoord,
    WorldCoord,
)
from tracemem.domain.episode import Episode
from tracemem.domain.grade import Grade, degrade
from tracemem.domain.ids import claim_id, node_id
from tracemem.domain.node import Attribute, Edge, EdgeType, Node, NodeKind
from tracemem.domain.provenance import Modality, Provenance, ProvenanceClass
from tracemem.domain.question import Question
from tracemem.domain.time import TimeRange

__all__ = [
    "activation_now", "boost", "spread_deltas",
    "Claim", "Confidence",
    "CaptureCoord", "CoordinateStack", "SpanCoord", "WorldCoord",
    "Episode", "Grade", "degrade", "claim_id", "node_id",
    "Attribute", "Edge", "EdgeType", "Node", "NodeKind",
    "Modality", "Provenance", "ProvenanceClass", "Question", "TimeRange",
]
