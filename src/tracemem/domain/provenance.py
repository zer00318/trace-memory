"""OWNS: provenance — perceiver + modality tag (spec 00 §4).

P1: modality is a TAG on a uniform shape. A heard utterance and a seen caption
are the same class; cross-modal binding is expressible because of this.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Tuple


class Modality(Enum):
    SCREEN = "screen"
    CAMERA = "camera"
    MIC = "mic"
    SYSTEM_AUDIO = "system_audio"
    SENSOR = "sensor"  # heartbeats, gap witnesses (spec 07)


class ProvenanceClass(Enum):
    """HOW a fact is known — the epistemic axis (SCHEMA §2.1 part 7).

    Distinct from Modality (which SENSE) and Grade (how well-witnessed). This
    is the axis that keeps a plan from posing as an observation.

    - LIVED  — observed happening: screen, camera, mic. A raw Claim.
    - OWNED  — read from the user's OWN corpus: files, mail, history. A raw
               Claim, but of a different world — a record/intention, not a
               live observation. "Your calendar said..." vs "I saw...".
    - BELIEF — DERIVED by the system. Belongs to the META tier (Node), never
               to a raw Claim; included here so the one axis spans both tiers
               and an answer can say "I think..." with the same vocabulary.
    """
    LIVED = "lived"
    OWNED = "owned"
    BELIEF = "belief"


@dataclass(frozen=True)
class Provenance:
    perceiver: str
    modality: Modality
    source_ids: Tuple[str, ...] = ()
    # Default LIVED: every claim written in this project's history so far is a
    # live screen observation, so LIVED is the honest default, not a guess.
    klass: "ProvenanceClass" = ProvenanceClass.LIVED
