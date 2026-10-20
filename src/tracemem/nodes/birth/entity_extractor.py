"""OWNS: episode → typed, UNRESOLVED mentions (spec 04 birth, rebuilt v1.5).

Deterministic multi-grammar extraction. Still model-free — the model-backed
NER (WO-032) slots in behind the same signature once gold labels exist to
measure it against (doctrine: adapters measured before trusted). But v1.5 is
a real extractor, not a placeholder: per-signal grammars, a name stoplist,
and every mention carries its claim of origin so provenance never breaks.

Mentions carry NO identity. "Sam" here is a surface string; WHICH Sam is the
resolver's problem, and the resolver is allowed to be uncertain.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from tracemem.domain import Modality

# --- person grammars ---
_PERSON_CTX_RE = re.compile(
    r"\b(?:with|to|from|told|asked|met|thanks|thank you|hey|hi|dear|cc)\s+"
    r"([A-Z][a-z]{2,15})\b")
_PERSON_SPEAKER_RE = re.compile(r"^([A-Z][a-z]{2,15}):\s")        # "Maya: sure!"
_PERSON_SIGNOFF_RE = re.compile(r"[-—]\s*([A-Z][a-z]{2,15})\s*$")  # "— Sam"
_ANON_PERSON_RE = re.compile(r"\b(?:a|another|the) person\b|\bsomeone\b", re.I)
_NAME_STOPLIST = {
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday", "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december", "today",
    "tomorrow", "yesterday", "the", "this", "that", "there", "here", "dear",
    "hello", "thanks", "regards", "best", "sent", "subject", "inbox",
}

# --- world grammars ---
_URL_RE = re.compile(r"(?:https?://)?([a-z0-9-]+(?:\.[a-z0-9-]+)+)(/[\w/.-]*)?", re.I)
# A dotted token is a WEBSITE only with an explicit scheme or a real web TLD —
# otherwise 'board.md' and 'crawl.mp4' (filenames, everywhere in a real corpus)
# get misread as sites. Learned from the first live run on ~/Documents.
_WEB_TLD = {"com", "org", "net", "io", "dev", "ai", "co", "edu", "gov", "app",
            "me", "xyz", "info", "de", "uk", "us", "so", "gg", "tv"}
_FILE_EXT = {"md", "py", "txt", "json", "csv", "tsv", "yaml", "yml", "toml",
             "ini", "cfg", "lock", "mp4", "mov", "png", "jpg", "jpeg", "gif",
             "svg", "db", "sqlite", "sqlite3", "pdf", "log", "sh", "js", "ts",
             "tsx", "html", "css", "pt", "mlpackage", "zip", "pyc", "ipynb",
             "pptx", "docx", "xlsx", "webp", "wav", "mp3"}
_READING_RE = re.compile(r"\breading (?:the |an? )?([a-z][\w -]{4,40}?)(?:[.,;]|$)", re.I)
_OBJECT_RE = re.compile(
    r"\b(?:a|an) ((?:red|blue|green|black|white|grey|gray|brown|yellow|"
    r"orange|purple|silver) [a-z]{3,15})\b", re.I)


@dataclass(frozen=True)
class Mention:
    kind: str                    # person|app|site|place|topic|object
    surface: str                 # verbatim surface ("Sam", "a person", "chess.com")
    episode_id: str
    claim_id: str
    t_ms: int = 0
    anonymous: bool = False
    detail: Optional[str] = None  # e.g. a site's path


def _person_names(text: str) -> List[str]:
    names = (_PERSON_CTX_RE.findall(text)
             + _PERSON_SPEAKER_RE.findall(text)
             + _PERSON_SIGNOFF_RE.findall(text))
    return [n for n in names if n.lower() not in _NAME_STOPLIST]


# GRAMMARS THAT NEED A BODY THIS SENSE DOES NOT HAVE.
#
# `_OBJECT_RE` reads "a red mug" and `_READING_RE` reads "reading the
# Economist" — both written for the PHYSICAL world, the napkin's camera path.
# DOCTRINE §3 said so on 2026-07-29: "the birth layer's grammars are
# PHYSICAL-world grammars ... and have only ever been fed browser OCR."
#
# Measured on the founder's graph 2026-08-24 — every object and topic node
# born this way, in full:
#
#   object  black box · black mar · blue and · green clay · green curtai ·
#           red and · white cropped · yellow mandala ...          (25 of 25)
#   topic   "gauge she has told you plainly s" · "their accessibility
#           contexts and m" · "window_stack api precisely"        (3 of 4)
#
# Not one is a thing in his life; all are fragments of whatever video or page
# was on screen. They then surfaced as COMPANY in dossiers — "Hana was seen
# alongside green clay" — which is the memory reporting a stranger's shirt as
# her associate.
#
# The grammar is not wrong, it is UNFED. So it waits for the pillar it was
# written for rather than being deleted: a CAMERA claim may mint an object, a
# screen OCR fragment may not. When the phone pillar lands (F-PILLARS) this
# turns itself back on with no code change.
_PHYSICAL_ONLY = (Modality.SCREEN,)


# SENSES THAT CAN MISREAD A STRING, as opposed to failing to produce one.
# OCR turns `chess.com` into `esa.com`; a VLM will describe a domain it half
# saw; ASR hears one spoken. Everything else on the list hands over the exact
# characters — the accessibility tree reports what the browser is displaying,
# an OWNED read copies from a file, the founder types it himself.
_GUESSING_SENSES = frozenset({"ocr", "vlm", "asr"})


# SENSES THAT DESCRIBE A SCENE RATHER THAN READING IT.
#
# A stricter category than _GUESSING_SENSES, and it exists for names. OCR at
# least ATTEMPTS exact character recovery and usually succeeds; the macro VLM
# is instructed in its own prompt not to transcribe at all ("Do NOT transcribe
# the text on screen — that is another sense's job"). So any name appearing in
# its output is by construction a guess, never a reading.
#
# MEASURED 2026-08-24 on gold/frames/auto_0025.png. The screen says
# `FM Sample Playername`. The macro eye reported:
#
#     participant  GM Example Player (chess player)
#
# Wrong on two of the three words. On that exact phrasing nothing was born —
# the person grammars want "with X" or "from X" — but that is luck, not a
# rule: the same eye phrasing it "a video call with Alex Example" mints the
# person `Example` on the spot, and a phantom person with a mangled name is
# what the founder then meets in a dossier.
#
# The person tier was cleaned from 110 surfaces to 26 real people this month.
# Letting a scene-describing sense mint identities would refill it, slowly,
# with names nobody can correct because nobody said them.
_SCENE_DESCRIBING_SENSES = frozenset({"vlm"})


def _perceiver(claim) -> str:
    return str(getattr(getattr(claim, "provenance", None),
                       "perceiver", "")).lower().split(":", 1)[0]


def _reads_exactly(claim) -> bool:
    """Whether this sense produced characters rather than recognised them."""
    perceiver = _perceiver(claim)
    return bool(perceiver) and perceiver not in _GUESSING_SENSES


def _may_name_a_person(claim) -> bool:
    """Whether a name in this claim is a reading or a guess.

    Deliberately NOT `_reads_exactly`: that would also bar OCR, which is
    where the founder's 26 real people came from. This bars only the senses
    that never claimed to be reading in the first place.
    """
    return _perceiver(claim) not in _SCENE_DESCRIBING_SENSES


def _has_a_body(claim) -> bool:
    """Whether this claim came from a sense that can see a physical thing."""
    modality = getattr(getattr(claim, "provenance", None), "modality", None)
    return modality is not None and modality not in _PHYSICAL_ONLY


def extract_mentions(episode, claims) -> List[Mention]:
    out: List[Mention] = []
    for c in claims:
        t = c.coord.t_ms
        if c.coord.capture.app:
            out.append(Mention("app", c.coord.capture.app, episode.id, c.id, t))
        if episode.place_key:
            out.append(Mention("place", episode.place_key, episode.id, c.id, t))

        # A DOMAIN MAY ONLY BE BORN FROM A SENSE THAT READ IT, not one that
        # guessed at it. OCR of a tab strip does not fail loudly on a domain,
        # it fails PLAUSIBLY: measured on the founder's graph, `chess.com`
        # arrived as `esa.com` (13 occurrences against 2 real ones),
        # `instagram.com` as `agram.com`, `gmail.com` as `ail.com`,
        # `accounts.google.com` as `accounts.g`. A truncated domain is not a
        # typo — it is a DIFFERENT, well-formed domain that could belong to
        # anyone, and once minted it is indistinguishable from a real one.
        #
        # PRIORITY_MASTER §2 reached this in July and fixed it half-way, by
        # letting an AX read outrank OCR WITHIN AN EPISODE. That leaves every
        # episode with no AX url still minting guesses. The accessibility
        # tree hands over the exact string the browser is displaying and has
        # no transcription error mode at all, so it is the only source
        # allowed to name a site.
        from_a_reader = _reads_exactly(c)
        m = _URL_RE.search(c.text) if from_a_reader else None
        if m and "." in m.group(1):
            dom = m.group(1).lower()
            tld = dom.rsplit(".", 1)[-1]
            has_scheme = m.group(0).lower().startswith("http")
            if tld not in _FILE_EXT and (has_scheme or tld in _WEB_TLD):
                out.append(Mention("site", dom, episode.id, c.id, t,
                                   detail=(m.group(2) or None)))

        # A NAME MUST COME FROM A SENSE THAT WAS READING. See
        # _SCENE_DESCRIBING_SENSES for the measured reason.
        if _may_name_a_person(c):
            for name in _person_names(c.text):
                out.append(Mention("person", name, episode.id, c.id, t))
        # "A PERSON" NEEDS A BODY TOO — the third instance of the same root
        # cause, after objects and topics. This grammar is right for a camera:
        # a VLM describing a frame says "a person in a blue shirt", and that
        # is a human who was really there. Applied to screen text it matches
        # the WORD: "someone commented on your post", "ask someone else",
        # "the person you follow" — notifications, not company.
        #
        # 43 anonymous "people" on the founder's graph were 43 occurrences of
        # those words on screen, each one then feeding the co-occurrence graph
        # as though a stranger had been in the room.
        if _has_a_body(c) and _ANON_PERSON_RE.search(c.text):
            out.append(Mention("person", "a person", episode.id, c.id, t,
                               anonymous=True))

        rm = _READING_RE.search(c.text) if _has_a_body(c) else None
        if rm:
            out.append(Mention("topic", rm.group(1).strip().lower(),
                               episode.id, c.id, t))
        if c.structured and c.structured.get("type"):
            out.append(Mention("topic", str(c.structured["type"]).split(".")[0],
                               episode.id, c.id, t))

        om = _OBJECT_RE.search(c.text) if _has_a_body(c) else None
        if om:
            out.append(Mention("object", om.group(1).lower(), episode.id, c.id, t))
    return _dedup(out)


def _dedup(mentions: List[Mention]) -> List[Mention]:
    seen, out = set(), []
    for m in mentions:
        k = (m.kind, m.surface.lower(), m.episode_id, m.anonymous)
        if k not in seen:
            seen.add(k)
            out.append(m)
    return out
