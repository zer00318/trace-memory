"""OWNS: the macro-picture eye (napkin: "A macro picture of what's
happening => VLM"). The understanding tier of extraction.

The OCR eye reads the WORDS on the screen — it recovers ~96% of the
frontier's verbatim claims (tools/context_gap.py, 2026-07-23). What it does
not produce is the frontier's UNDERSTANDING: what object is on screen, what
the person is doing, what can be inferred. That tier — object / inference /
activity / participant — is 21% of a frontier ceiling and our OCR-only path
recalls ~0.17 of it. This eye closes THREE of those four tiers; it
deliberately does not attempt `participant`, and the ceiling therefore stays
above it by that much on purpose. See KINDS for what was measured.

It is a LOCAL VLM (gemma3 via ollama), not a frontier call, on purpose: the
napkin puts a VLM in the capture path as an on-device organ, and the whole
product promise is that pixels never leave the machine. The frontier is the
measuring stick (the ceiling), never the product's own eye.

ONE MODEL ON THE GPU AT A TIME (hard law). This shares the GPU with the
reasoner. It must run in the night window or on banked frames during
development — never beside live capture. The caller owns that scheduling;
this module just makes the call.

GRADE: its claims are GIST (domain/grading.py: "vlm" -> Grade.GIST). A
scene description is lossy interpretation, not faithful transcription, and
must never outrank the OCR verbatim it sits beside — a confident wrong
inference that could overrule read text is exactly the failure the grade
scale exists to prevent.
"""
from __future__ import annotations

import base64
import json
import urllib.request

from tracemem.llm.egress import require_loopback
from typing import Callable, List, Optional

DEFAULT_MODEL = "gemma3:12b-it-qat"
DEFAULT_URL = "http://127.0.0.1:11434/api/generate"

# The kinds this eye is allowed to emit — the understanding tiers, matching
# the ceiling vocabulary so the two are comparable. Deliberately NOT
# "verbatim": reading is the OCR eye's job, and a VLM asked to transcribe
# hallucinates plausible text, which is worse than an honest gap.
# "participant" WAS HERE AND WAS REMOVED, MEASURED 2026-08-24.
#
# Four gold frames, five participant claims, every one of them the model
# transcribing or inventing a name — the one thing this prompt explicitly
# forbids, because a name can only be produced by reading:
#
#   auto_0025  "A. Streamer (Chess YouTuber)"      correct
#   auto_0025  "GM Example Player (chess player)"     screen says
#                                                    `FM Sample Playername`
#   auto_0019  "User"                                vacuous
#   auto_0031  "Marco (as indicated in the bottom right corner)"
#   auto_0145  "Sunny (channel owner)"              FABRICATED — the channel
#                                                    is "Sunny Videos", a
#                                                    brand. No person named
#                                                    Sunny is anywhere in
#                                                    the frame. The two
#                                                    people the page DOES
#                                                    name, Ravi Example and
#                                                    Mona Example, went
#                                                    unmentioned.
#
# One clean name in five, and that one is a string the OCR eye reads exactly.
# The kind cannot pay for itself: asking a sense not to transcribe and then
# asking it who is present is asking it to guess, and it does.
#
# The birth layer separately refuses to mint people from this sense
# (`nodes/birth/entity_extractor._SCENE_DESCRIBING_SENSES`). That stays — the
# other three kinds can mention a name in passing. This is the other half:
# stop asking the question that can only be answered by guessing.
KINDS = ("object", "inference", "activity")

_PROMPT = (
    "You are the scene-understanding sense of a personal memory system, "
    "looking at one screenshot. Do NOT transcribe the text on screen — that "
    "is another sense's job. Report only what a person GLANCING at this "
    "screen would understand:\n"
    "  - object: a distinct UI element or thing present (app window, button, "
    "image, chart, video)\n"
    "  - activity: what the user appears to be doing right now\n"
    "  - inference: something reasonably concluded from the scene that is not "
    "written verbatim\n"
    "Do NOT report who is present, and never name a person: reading names is "
    "another sense's job, and a model asked who is there invents someone.\n"
    "Be specific and grounded; never guess a fact you cannot see. Return JSON "
    '{"claims":[{"kind":"object|activity|inference","text":"..."}]}'
)


def _default_transport(url: str, payload: dict, timeout_s: float) -> str:
    req = urllib.request.Request(
        require_loopback(url), data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode("utf-8"))["response"]


class VlmMacroEye:
    """Frame in, understanding-tier claims out. No store, no pixels kept."""

    def __init__(self, model: str = DEFAULT_MODEL, url: str = DEFAULT_URL,
                 transport: Optional[Callable] = None,
                 timeout_s: float = 120.0) -> None:
        self.model = model
        self.url = url
        self._transport = transport or _default_transport
        self._timeout_s = timeout_s

    def describe_bytes(self, png_bytes: bytes) -> List[dict]:
        img = base64.standard_b64encode(png_bytes).decode()
        payload = {
            "model": self.model,
            "prompt": _PROMPT,
            "images": [img],
            "stream": False,
            "format": "json",
            # Low temperature: this is perception, not creativity. A scene
            # sense that answers differently each time it looks at the same
            # unchanged frame is a sense you cannot trust or test.
            "options": {"temperature": 0.0},
        }
        raw = self._transport(self.url, payload, self._timeout_s)
        return self._parse(raw)

    def describe(self, png_path) -> List[dict]:
        with open(str(png_path), "rb") as fh:
            return self.describe_bytes(fh.read())

    @staticmethod
    def _parse(raw: str) -> List[dict]:
        """Tolerant parse: a local model's JSON is not always clean. A
        malformed reply yields NO claims rather than a crash — a blind beat
        is recoverable, a dead one takes the night down."""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
        claims = data.get("claims") if isinstance(data, dict) else None
        if not isinstance(claims, list):
            return []
        out = []
        for c in claims:
            if not isinstance(c, dict):
                continue
            kind = c.get("kind")
            text = c.get("text")
            if kind in KINDS and isinstance(text, str) and text.strip():
                out.append({"kind": kind, "text": text.strip()})
        return out
