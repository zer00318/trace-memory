"""OWNS: specialist structure → canonical sentence, deterministic (spec 02 §3).

Same structure always renders the same sentence; the payload rides alongside
in Claim.structured (P3.2). No LLM.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Mapping

_RENDERERS: Dict[str, Callable[[Mapping[str, Any]], str]] = {}


def renderer(kind: str):
    def deco(fn):
        _RENDERERS[kind] = fn
        return fn
    return deco


@renderer("chess.fen")
def _chess(s: Mapping[str, Any]) -> str:
    return "chess position (FEN %s), %s to move" % (s["fen"], s.get("to_move", "white"))


@renderer("recipe")
def _recipe(s: Mapping[str, Any]) -> str:
    return "recipe: %s — %d steps; ingredients: %s" % (
        s["title"], len(s.get("steps", [])), ", ".join(s.get("ingredients", [])))


@renderer("code")
def _code(s: Mapping[str, Any]) -> str:
    return "code (%s): symbols %s" % (s.get("lang", "?"), ", ".join(s.get("symbols", [])))


def render(structured: Mapping[str, Any]) -> str:
    kind = structured.get("type")
    if kind not in _RENDERERS:
        raise KeyError("no renderer for structured type %r" % kind)
    return _RENDERERS[kind](structured)
