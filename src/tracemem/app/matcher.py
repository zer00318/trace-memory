"""OWNS: the exclusion matcher (map: Running / The matcher).

The user's blocklist, applied BEFORE a frame enters the retina. Bundle ids,
window-title patterns, and URL hosts.

WHERE THIS RUNS IS THE WHOLE POINT. The right place is the SCStream content
filter, so the OS never hands us the pixels at all. Where the filter cannot
express a rule — a title pattern, a URL host, both of which are properties
of window contents rather than of a window list — this gate runs at the
capture callback, before the frame reaches `Retina.push`. What must never
happen is exclusion applied downstream, after storage: a blocked scene that
was stored and then filtered out of answers is a blocked scene that is
still on disk, and the user asked for it not to exist.

WHAT THE WITNESS MAY SAY. A blocked interval IS witnessed, because an
unwitnessed hole is indistinguishable from "nothing happened" and the
coverage audit would silently over-report. But the witness is COARSE by
construction: it carries the interval and the RULE THE USER WROTE, and
never the window title, never the URL, never anything read from the screen.
Naming the user's own rule back to them reveals nothing they did not
already type. Naming the title would defeat the exclusion in the act of
recording it.

The interval closes when the blocked scene stops being frontmost, so the
witness reads "excluded 14:00-14:20" rather than one witness per frame.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import List, Optional, Sequence

from tracemem.app.witness import WitnessKind, witness
from tracemem.domain import Claim


@dataclass(frozen=True)
class Scene:
    """What the capture path knows about the frontmost thing, before any
    pixels are kept."""
    bundle_id: str = ""
    window_title: str = ""
    url: str = ""


@dataclass(frozen=True)
class Rule:
    """One line of the user's blocklist."""
    kind: str          # "bundle" | "title" | "host"
    pattern: str

    def describe(self) -> str:
        return "%s:%s" % (self.kind, self.pattern)


def _host_of(url: str) -> str:
    if not url:
        return ""
    rest = url.split("://", 1)[-1]
    host = rest.split("/", 1)[0].split("?", 1)[0]
    return host.split("@")[-1].split(":")[0].lower()


def _host_matches(host: str, pattern: str) -> bool:
    """Suffix match on LABEL boundaries.

    'bank.com' must block 'www.bank.com' and must NOT block 'notbank.com'.
    A plain endswith gets the second case wrong, and gets it wrong in the
    direction of blocking things the user did not ask to block — which
    silently costs them memory they wanted.
    """
    host, pattern = host.lower(), pattern.lower().lstrip(".")
    return host == pattern or host.endswith("." + pattern)


class Blocklist:
    """The rules, and the question 'is this scene excluded'."""

    def __init__(self, rules: Optional[Sequence[Rule]] = None) -> None:
        self.rules: List[Rule] = list(rules or [])

    @classmethod
    def from_spec(cls, bundles=(), titles=(), hosts=()) -> "Blocklist":
        return cls([Rule("bundle", b) for b in bundles]
                   + [Rule("title", t) for t in titles]
                   + [Rule("host", h) for h in hosts])

    def match(self, scene: Scene) -> Optional[Rule]:
        """The FIRST rule that excludes this scene, or None."""
        for rule in self.rules:
            if rule.kind == "bundle" and scene.bundle_id and \
                    fnmatch.fnmatch(scene.bundle_id.lower(), rule.pattern.lower()):
                return rule
            if rule.kind == "title" and scene.window_title and \
                    fnmatch.fnmatch(scene.window_title.lower(), rule.pattern.lower()):
                return rule
            if rule.kind == "host" and scene.url and \
                    _host_matches(_host_of(scene.url), rule.pattern):
                return rule
        return None

    def excludes(self, scene: Scene) -> bool:
        return self.match(scene) is not None


class ExclusionGate:
    """Applies the blocklist frame by frame and witnesses the interval.

    `admit(scene, now_ms)` is the door: False means the frame must not reach
    the retina. The witness arrives when the blocked interval CLOSES, so one
    excluded stretch is one claim rather than one per frame.
    """

    def __init__(self, blocklist: Blocklist) -> None:
        self.blocklist = blocklist
        self.frames_refused = 0
        self._open_rule: Optional[Rule] = None
        self._open_since_ms: Optional[int] = None
        self._last_blocked_ms: Optional[int] = None

    def admit(self, scene: Scene, now_ms: int) -> bool:
        rule = self.blocklist.match(scene)
        if rule is None:
            return True
        if self._open_rule is None:
            self._open_rule = rule
            self._open_since_ms = now_ms
        self._last_blocked_ms = now_ms
        self.frames_refused += 1
        return False

    def close(self, now_ms: int) -> Optional[Claim]:
        """Call when a non-blocked scene arrives, or at shutdown. Returns the
        coarse interval witness if one was open."""
        if self._open_rule is None:
            return None
        rule = self._open_rule
        # `x or now_ms` would be wrong here: t_ms == 0 is a real timestamp
        # and falsy, so the first interval of any run (or of any fixture
        # starting at zero) would silently report itself as starting now.
        t1 = now_ms if self._open_since_ms is None else self._open_since_ms
        last = now_ms if self._last_blocked_ms is None else self._last_blocked_ms
        t2 = max(now_ms, last)
        self._open_rule = None
        self._open_since_ms = None
        self._last_blocked_ms = None
        return witness(
            WitnessKind.EXCLUDED, t_ms=t1, span_t2_ms=t2,
            detail="excluded by your rule %s" % rule.describe(),
            structured_extra={"rule_kind": rule.kind,
                              "rule_pattern": rule.pattern,
                              "span_ms": t2 - t1})

    def step(self, scene: Scene, now_ms: int):
        """Convenience for a capture loop: returns (admitted, witness_or_None).

        Closing on the first non-blocked scene is what makes the interval an
        interval; a caller that only ever calls admit() would hold one open
        forever.
        """
        if self.blocklist.excludes(scene):
            return self.admit(scene, now_ms), None
        return True, self.close(now_ms)
