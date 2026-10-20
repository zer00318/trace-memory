"""OWNS: the only logger, and the law that store content never enters it
(map: Yours / Log scrubbing law).

THE HOLE THIS CLOSES. Everything else in this system guards the store: the
container is encrypted, the keys live in the Keychain, pixels die on the
device. And then one debug line —

    log.info(f"packed context: {claim.text}")

— writes a person's life to ~/Library/Logs in plaintext, world-readable,
swept up by crash reporters and support bundles and Time Machine, outside
every guarantee the product makes. The encryption is not wrong; it is
bypassed. That is why this is a sovereignty atom and not a tidiness one.

ENFORCED AT THE LOGGER, NOT BY DISCIPLINE (the brief's own words). Asking
every worker to remember not to log claim text is a rule that scales with
engineering hours and fails on the first tired afternoon. So instead:

  - the API takes STRUCTURED fields only; there is no message-format string
    to interpolate a life into;
  - a Claim (or anything claim-shaped) passed as a value is REDUCED to its
    id, kind and grade before it can reach a handler — passing the object
    is the safe path, which makes the lazy thing the correct thing;
  - free text is length-capped and scanned for the shapes of store content;
  - what survives is counts and ids, which is what an operator actually
    debugs with anyway.

The design bet: make the convenient call safe, and the unsafe call
awkward. A worker who wants to log a claim will type `log("packed",
claim=c)` because it is shorter than anything else, and get an id.

SPEC: 09 (yours)
ORACLE: tests/unit/test_logbook.py — logging a Claim emits id and kind and
        NEVER its text; a simulated day of logs greps clean of planted
        store content.
"""
from __future__ import annotations

import json
import re
import sys
import time
from typing import Any, Callable, Dict, Optional

# Free-text fields are capped hard. Long strings are where content hides —
# a truncated 80 chars of an error message still debugs; 4KB of it is a
# paste of someone's screen. UNCALIBRATED, chosen to fit an error line.
MAX_TEXT = 120

# Field names whose VALUE is never safe to log regardless of type. Belt and
# braces with the Claim reduction: a dict carrying {"text": ...} scraped out
# of a claim is not a Claim object and would otherwise sail through.
FORBIDDEN_KEYS = frozenset({
    "text", "ocr_text", "transcript", "content", "body", "window",
    "title", "prompt", "answer", "passage", "snippet", "rendered",
    # ADDED 2026-07-22 by the privacy red-team drill, which FAILED on its
    # first run and found this hole. A blocked scene's URL reached a log
    # line carrying a planted signature, because the scrubber knew "title"
    # and "window" were content and had never been told a URL is.
    #
    # It is, twice over: the address names what the user was looking at,
    # and query strings routinely carry tokens, session ids and search
    # terms. `bank.example/x?tok=...` is not metadata about a page, it is
    # the page. Log the claim id or a host if a caller needs one; the
    # logger will not decide that for you.
    "url", "uri", "href", "link", "address", "query", "search",
    "email", "phone", "filename", "filepath",
})

REDACTED = "<redacted:store-content>"
REDACTED_SECRET = "<redacted:secret>"

# The brief asks for free text to be length-capped AND CONTENT-DENYLISTED.
# The cap alone is not enough, and my own day-of-logs test proved it: a
# secret inside a SHORT error message ("failed on sk-live-abcdef123456")
# is under the cap and sails straight through. That is not a contrived
# case — an error string carrying the credential that caused it is the
# single most common way a key reaches a log file.
#
# Shape-based, because the logger cannot know what is secret by name. Each
# pattern is a shape that is never legitimately useful in a log line.
_SECRET_SHAPES = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),            # OpenAI/Anthropic-style
    re.compile(r"\bAKIA[0-9A-Z]{12,}"),                # AWS access key id
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),             # GitHub token
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{12,}", re.I),
    re.compile(r"\b[A-Fa-f0-9]{32,}\b"),               # long hex: hashes, keys
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"),       # long base64 runs
    # "password is X" / "passphrase: X" and friends — the human shapes.
    re.compile(r"\b(pass(?:word|phrase)|secret|token|api[_\- ]?key)\b"
               r"\s*(?:is|=|:)\s*\S+", re.I),
]


def _redact_secrets(text: str) -> str:
    for pat in _SECRET_SHAPES:
        text = pat.sub(REDACTED_SECRET, text)
    return text


def _looks_like_claim(v: Any) -> bool:
    return hasattr(v, "id") and hasattr(v, "text") and hasattr(v, "coord")


def _claim_summary(c: Any) -> Dict[str, Any]:
    """What a Claim is allowed to become in a log line: identity, not content."""
    structured = getattr(c, "structured", None) or {}
    out = {
        "claim_id": getattr(c, "id", None),
        "kind": structured.get("witness") or getattr(
            getattr(c, "provenance", None), "perceiver", None),
        "grade": int(c.grade) if getattr(c, "grade", None) is not None else None,
        "t_ms": getattr(getattr(c, "coord", None), "t_ms", None),
        "chars": len(getattr(c, "text", "") or ""),   # the SIZE, never the text
    }
    return {k: v for k, v in out.items() if v is not None}


def scrub(key: str, value: Any, _depth: int = 0) -> Any:
    """Reduce one field to something safe. Total and recursive.

    Recursion is capped: a deeply nested structure is itself suspicious in a
    log line, and an unbounded walk over a claim graph would be its own bug.
    """
    if _depth > 3:
        return REDACTED
    if _looks_like_claim(value):
        return _claim_summary(value)
    if key.lower() in FORBIDDEN_KEYS:
        return REDACTED
    if isinstance(value, str):
        safe = _redact_secrets(value)
        return safe if len(safe) <= MAX_TEXT else safe[:MAX_TEXT] + "…"
    if isinstance(value, dict):
        return {k: scrub(str(k), v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        # A list of claims logs as a COUNT — the single most common way a
        # whole day's content would otherwise land in a file.
        if value and all(_looks_like_claim(v) for v in value):
            return {"claims": len(value)}
        return [scrub(key, v, _depth + 1) for v in value[:20]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return scrub(key, str(value), _depth + 1)


class Logbook:
    """The shared logger. Structured events only — no format strings."""

    def __init__(self, sink: Optional[Callable[[str], None]] = None,
                 clock: Callable[[], float] = time.time) -> None:
        self._sink = sink if sink is not None else _default_sink
        self._clock = clock

    def event(self, name: str, **fields: Any) -> Dict[str, Any]:
        """Log one structured event. Returns the record actually emitted, so
        a test can assert on exactly what would hit disk."""
        record = {"t": int(self._clock() * 1000), "event": str(name)[:MAX_TEXT]}
        for k, v in fields.items():
            record[k] = scrub(k, v)
        self._sink(json.dumps(record, sort_keys=True, default=str))
        return record


def _default_sink(line: str) -> None:
    sys.stderr.write(line + "\n")


# One shared instance; organs import this rather than constructing loggers,
# so there is exactly one door and the grep oracle has one place to check.
log = Logbook()


# -- the audit side of the law --------------------------------------------
def scan_for_content(text: str, signatures) -> list:
    """Return any planted store signature found in a body of log text.

    The other half of the atom's oracle: the law is only real if somebody
    checks, so the checker ships with it. Used by the privacy red-team drill
    against real log files.
    """
    return [s for s in signatures if s and s in text]


def looks_scrubbed(record: dict) -> bool:
    """Would this record be safe on disk? Used by tests and the red-team."""
    def walk(v) -> bool:
        if isinstance(v, str):
            return len(v) <= MAX_TEXT + 1
        if isinstance(v, dict):
            return all(str(k).lower() not in FORBIDDEN_KEYS or val == REDACTED
                       for k, val in v.items()) and all(walk(x) for x in v.values())
        if isinstance(v, (list, tuple)):
            return all(walk(x) for x in v)
        return True
    return walk(record)
