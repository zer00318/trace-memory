"""OWNS: deterministic synthetic day/week fixtures (spec X §1, 05 §6).

Same seed -> byte-identical claim set. The routine week has a compressible
life (repeated slots) plus planted deviations and planted facts that the
question set targets; chaotic=True produces a non-plateauing life so the
novelty probe has a negative control.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List

from tracemem.domain import (
    CaptureCoord, Claim, Confidence, CoordinateStack, Grade, Modality,
    Provenance, SpanCoord, WorldCoord,
)

MS_H = 3_600_000
MS_DAY = 24 * MS_H

HOME, OFFICE, CAFE = "wifi:home-ap", "wifi:office-ap", "wifi:cafe-shared"


def _claim(text: str, t_ms: int, place: str, app: str,
           modality=Modality.SCREEN, grade=Grade.VERBATIM,
           perceiver="ocr") -> Claim:
    coord = CoordinateStack(
        world=WorldCoord(place_key=place),
        capture=CaptureCoord(frame_id="f-%d" % t_ms, device_id="mac-1", app=app),
        span=SpanCoord(bbox=(0.1, 0.1, 0.8, 0.2)),
        t_ms=t_ms)
    return Claim(text=text, coord=coord,
                 provenance=Provenance(perceiver=perceiver, modality=modality),
                 grade=grade, confidence=Confidence(0.92),
                 # birth stamps (ruling dd / claim_match.md A4): synthetic
                 # fixture claims flow through the real ClaimLog in eval
                 # harnesses, so they must be stamped like any real write —
                 # synthetic bidder/cost, frame-scoped (these describe one
                 # synthetic moment's capture, not accumulated memory).
                 bidder="fixture:synthetic_week", bid_id="fixture:synthetic_week:%d" % t_ms,
                 bid_predicted_value=0.0, actual_cost=0.0, scope="frame")


@dataclass(frozen=True)
class SyntheticWeek:
    claims: List[Claim]
    scene_changes: List[int]
    questions: List[dict]


def synthetic_week(seed: int = 7, chaotic: bool = False) -> SyntheticWeek:
    rng = random.Random(seed)
    claims: List[Claim] = []
    scene_changes: List[int] = []

    lunches = ["ramen", "falafel wrap", "leftover dal", "ramen", "burrito",
               "pancakes", "ramen"]
    docs = ["memory consolidation review", "sqlite WAL internals",
            "metal shader profiling notes", "memory consolidation review",
            "hkdf key derivation rfc", "pottery kiln manual",
            "memory consolidation review"]

    for d in range(7):
        base = d * MS_DAY
        if chaotic:
            for h in (8, 10, 13, 16, 20):
                noise = " ".join("tok%d" % rng.randrange(10 ** 6) for _ in range(8))
                claims.append(_claim(noise, base + h * MS_H,
                                     "wifi:place-%d" % rng.randrange(10 ** 4), "Chaos"))
            scene_changes.extend(base + h * MS_H for h in (9, 12, 18))
            continue

        # morning at home — stable routine
        claims.append(_claim("morning news: weather sunny in oslo",
                             base + 8 * MS_H, HOME, "Safari"))
        claims.append(_claim("email from Sam: quarterly report draft attached",
                             base + int(8.5 * MS_H), HOME, "Mail"))
        scene_changes.append(base + 9 * MS_H)
        if d < 5:  # weekday: office block
            claims.append(_claim("standup note: shipped extraction fixes",
                                 base + int(9.5 * MS_H), OFFICE, "Notes"))
            claims.append(_claim("reading %s" % docs[d],
                                 base + 11 * MS_H, OFFICE, "Preview"))
            claims.append(_claim("editing parity_loop.py in tracemem repo",
                                 base + 14 * MS_H, OFFICE, "VS Code"))
        scene_changes.append(base + 12 * MS_H)
        claims.append(_claim("lunch order: %s" % lunches[d],
                             base + int(12.5 * MS_H), OFFICE if d < 5 else HOME,
                             "DoorDash"))
        scene_changes.append(base + 18 * MS_H)
        claims.append(_claim("watching documentary about coral reefs",
                             base + 20 * MS_H, HOME, "TV",
                             modality=Modality.MIC, perceiver="asr",
                             grade=Grade.GIST))

    if not chaotic:
        # planted deviations — the emergent layer's raw material
        claims.append(_claim("coffee with Maya about the kiln project timeline",
                             2 * MS_DAY + 16 * MS_H, CAFE, "Calendar"))
        claims.append(_claim("Maya said the kiln firing is moved to thursday",
                             2 * MS_DAY + int(16.5 * MS_H), CAFE, "Messages",
                             modality=Modality.MIC, perceiver="asr",
                             grade=Grade.GIST))
        # A SECOND, LATER SIGHTING OF MAYA — added 2026-07-29 (N2). Not a
        # concession to the corroboration gate but a correction to the
        # fixture: a person who matters to you does not appear once in a week
        # and never again, and a synthetic week in which everyone is a
        # one-off is a week no real life resembles. It also keeps the crown
        # test honest — under the corroboration rule, a name seen exactly
        # once is deliberately NOT durable, so a fixture that mentioned Maya
        # once would have been asserting the very behaviour the founder's
        # real store proved harmful (people named "Thinking", "Real",
        # "Launminrkyyodi", each from a single sighting).
        claims.append(_claim("thanks Maya — kiln slot confirmed for thursday",
                             4 * MS_DAY + int(9.5 * MS_H), HOME, "Mail"))
        claims.append(_claim("museum ticket: ceramics retrospective admission",
                             5 * MS_DAY + 15 * MS_H, "wifi:museum-guest", "Wallet"))

    questions = [
        {"id": "q-doc", "text": "what document did I read most at the office?",
         "type": "factual", "authored_from": "life",
         "gold": ["memory consolidation"], "gap": False},
        {"id": "q-standup", "text": "what exactly did my standup note say?",
         "type": "verbatim", "authored_from": "life",
         "gold": ["shipped extraction fixes"], "gap": False},
        {"id": "q-maya", "text": "when was the kiln firing moved to?",
         "type": "factual", "authored_from": "life",
         "gold": ["thursday"], "gap": False},
        {"id": "q-routine", "text": "what do I usually eat for lunch?",
         "type": "emergent", "authored_from": "life",
         "gold": ["ramen"], "gap": False},
        {"id": "q-morning", "text": "is there a pattern to my mornings?",
         "type": "emergent", "authored_from": "life",
         "gold": ["news", "home"], "gap": False},
        {"id": "q-gap", "text": "who phoned me on sunday night?",
         "type": "factual", "authored_from": "life",
         "gold": [], "gap": True},                      # never captured — F3
        {"id": "q-store", "text": "what app produced frame f-28800000?",
         "type": "factual", "authored_from": "store",   # store-authored: excluded from gate (F2)
         "gold": ["Safari"], "gap": False},
    ]
    return SyntheticWeek(claims=claims, scene_changes=sorted(scene_changes),
                         questions=questions)
