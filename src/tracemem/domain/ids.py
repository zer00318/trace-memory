"""OWNS: deterministic content ids (spec 00 §8).

CL2: same perception twice → same id. Sync dedup and append idempotence
(spec 02 T2, spec 06 K4) both rest on this determinism.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Mapping, Optional

from tracemem.domain.coordinate import CoordinateStack
from tracemem.domain.provenance import Provenance

_NS = uuid.uuid5(uuid.NAMESPACE_URL, "tracemem")


CLAIM_ID_VERSION = 2


def legacy_claim_id(text: str, coord: CoordinateStack,
                    provenance: Provenance) -> str:
    """The v1 identity, retained only to audit pre-v2 stores."""
    canonical = "|".join([
        text,
        str(coord.t_ms),
        # The capture locator: a frame (LIVED) or a source (OWNED). Exactly
        # one is set (CaptureCoord enforces it), so this is always a str and
        # every pre-existing LIVED id is unchanged (frame_id still wins).
        coord.capture.frame_id or coord.capture.source,
        coord.capture.device_id,
        str(coord.span.bbox),
        provenance.perceiver,
        provenance.modality.value,
    ])
    return str(uuid.uuid5(_NS, "claim:" + canonical))


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in sorted(
            value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("claim identity contains non-JSON value %r" % (value,))


def claim_id(text: str, coord: CoordinateStack, provenance: Provenance, *,
             structured: Optional[Mapping[str, Any]] = None,
             grade: Any = None, confidence: Any = None) -> str:
    """Identity of one observation, including its complete locating facts."""
    payload = {
        "v": CLAIM_ID_VERSION,
        "text": text,
        "world": {"gps": coord.world.gps, "pose": coord.world.pose,
                  "place_key": coord.world.place_key},
        "capture": {"frame_id": coord.capture.frame_id,
                    "source": coord.capture.source,
                    "device_id": coord.capture.device_id,
                    "app": coord.capture.app,
                    "window": coord.capture.window},
        "span": {"bbox": coord.span.bbox},
        "t_ms": coord.t_ms,
        "provenance": {"perceiver": provenance.perceiver,
                       "modality": provenance.modality.value,
                       "source_ids": provenance.source_ids,
                       "klass": provenance.klass.value},
        "grade": int(grade) if grade is not None else None,
        "confidence": getattr(confidence, "value", confidence),
        "structured": structured,
    }
    canonical = json.dumps(_jsonable(payload), sort_keys=True,
                           separators=(",", ":"), ensure_ascii=False,
                           allow_nan=False)
    return str(uuid.uuid5(_NS, "claim:v2:" + canonical))


def claim_id_version(value: str, text: str, coord: CoordinateStack,
                     provenance: Provenance, *, structured=None,
                     grade=None, confidence=None) -> int:
    """Return 2/current, 1/legacy, or 0/explicit unknown identity."""
    current = claim_id(text, coord, provenance, structured=structured,
                       grade=grade, confidence=confidence)
    if value == current:
        return CLAIM_ID_VERSION
    if value == legacy_claim_id(text, coord, provenance):
        return 1
    return 0


def node_id(kind: str, natural_key: str) -> str:
    return str(uuid.uuid5(_NS, "node:%s:%s" % (kind, natural_key)))
