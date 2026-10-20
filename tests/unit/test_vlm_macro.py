"""ORACLE for the macro-picture eye (extraction/vlm_macro.py).

The live call needs the GPU and a model, so it is not in the suite. What is
here is everything that must hold REGARDLESS of what the model says: the eye
never emits verbatim (reading is another sense's job), a malformed reply is
a blind beat and not a dead one, and the request actually carries the image.
"""
import base64
import json

import pytest

from tracemem.extraction.vlm_macro import KINDS, VlmMacroEye


def eye_returning(raw, capture=None):
    def transport(url, payload, timeout_s):
        if capture is not None:
            capture["payload"] = payload
            capture["url"] = url
        return raw
    return VlmMacroEye(transport=transport)


def test_it_parses_understanding_claims():
    raw = json.dumps({"claims": [
        {"kind": "object", "text": "a terminal window"},
        {"kind": "activity", "text": "the user is editing code"},
        {"kind": "inference", "text": "likely a developer"},
    ]})
    claims = eye_returning(raw).describe_bytes(b"png")
    assert len(claims) == 3
    assert {c["kind"] for c in claims} == set(KINDS)


def test_a_named_person_is_dropped_even_if_the_model_offers_one():
    """`participant` was removed from KINDS on 2026-08-24 after four gold
    frames produced five participant claims: one correct name, one garbled
    (`GM Example Player` for `FM Sample Playername`), one vacuous, one
    unverified, and one FABRICATED — "Sunny (channel owner)" invented from
    the brand "Sunny Videos".

    The prompt no longer asks for it, but a prompt is a request and this
    parser is the enforcement. A model that volunteers a person anyway must
    have it dropped here rather than trusted to obey.
    """
    raw = json.dumps({"claims": [
        {"kind": "object", "text": "a terminal window"},
        {"kind": "participant", "text": "Sunny (channel owner)"},
    ]})
    claims = eye_returning(raw).describe_bytes(b"png")
    assert [c["text"] for c in claims] == ["a terminal window"]


def test_it_refuses_to_emit_verbatim():
    """Reading is the OCR eye's job. A VLM asked to transcribe hallucinates
    plausible text, which is worse than an honest gap — so a verbatim claim
    from the model is dropped, not stored."""
    raw = json.dumps({"claims": [
        {"kind": "verbatim", "text": "THE EXACT WORDS ON SCREEN"},
        {"kind": "object", "text": "a chart"},
    ]})
    claims = eye_returning(raw).describe_bytes(b"png")
    assert [c["kind"] for c in claims] == ["object"]


def test_a_malformed_reply_is_a_blind_beat_not_a_dead_one():
    """A local model's JSON is not always clean. A crash here would take the
    night down; no claims is recoverable."""
    for raw in ("not json at all", "", "{", json.dumps({"wrong": "shape"}),
                json.dumps(["a", "list"]), json.dumps({"claims": "notalist"})):
        assert eye_returning(raw).describe_bytes(b"png") == []


def test_junk_items_are_skipped_individually():
    raw = json.dumps({"claims": [
        {"kind": "object", "text": "a window"},
        {"kind": "object"},                       # no text
        {"text": "no kind"},
        {"kind": "object", "text": "   "},        # blank
        "a bare string",
        {"kind": "object", "text": "another window"},
    ]})
    claims = eye_returning(raw).describe_bytes(b"png")
    assert [c["text"] for c in claims] == ["a window", "another window"]


def test_the_request_actually_carries_the_image():
    """A scene eye that forgot to attach the image would describe nothing and
    could still look like it worked."""
    cap = {}
    eye_returning(json.dumps({"claims": []}), capture=cap).describe_bytes(b"the pixels")
    assert cap["payload"]["images"] == [base64.standard_b64encode(b"the pixels").decode()]
    assert cap["payload"]["stream"] is False
    assert cap["payload"]["options"]["temperature"] == 0.0


def test_temperature_is_zero_so_the_same_frame_reads_the_same():
    """Perception, not creativity. A scene sense that answers differently
    each time it looks at an unchanged frame cannot be trusted or tested."""
    cap = {}
    eye_returning(json.dumps({"claims": []}), capture=cap).describe_bytes(b"x")
    assert cap["payload"]["options"]["temperature"] == 0.0
