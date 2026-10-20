"""Read the user's durable documents without changing them.

The broad and deep OWNED passes share this one format boundary.  Returning a
content digest with the decoded text gives the caller an exact restart receipt:
mtime alone is not proof that bytes are unchanged, and a filename is not an
honest substitute for reading the document.

Only local, deterministic parsers live here.  No document is uploaded, no
office application is automated, and embedded objects/attachments are never
executed.  Zip based formats are bounded by both archive size and expanded
text so a small zip bomb cannot turn an install-day skim into an unbounded job.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import zipfile
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Iterable, Optional
from xml.etree import ElementTree as ET


TEXT_EXTS = {
    ".md", ".markdown", ".txt", ".text", ".rst", ".org", ".log",
    ".json", ".csv", ".tsv", ".yaml", ".yml", ".ini", ".cfg",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java",
    ".c", ".h", ".html", ".css", ".sh",
}
MAIL_EXTS = {".eml", ".emlx"}
DOCUMENT_EXTS = {".pdf", ".docx", ".pptx", ".xlsx"}
SUPPORTED_EXTS = TEXT_EXTS | MAIL_EXTS | DOCUMENT_EXTS

MAX_TEXT_BYTES = 1_000_000
MAX_DOCUMENT_BYTES = 32_000_000
MAX_EXPANDED_TEXT = 4_000_000
MAX_ZIP_EXPANDED = 96_000_000


class SourceUnreadable(ValueError):
    """The source could not be decoded honestly.  The caller reports it."""


@dataclass(frozen=True)
class DecodedSource:
    text: str
    fingerprint: str
    title: Optional[str] = None


def _bounded(raw: bytes, limit: int = MAX_EXPANDED_TEXT) -> str:
    return raw.decode("utf-8", "replace")[:limit]


def _xml_text(raw: bytes) -> str:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise SourceUnreadable("invalid document XML") from exc
    chunks = []
    for element in root.iter():
        if element.text and element.text.strip():
            chunks.append(element.text.strip())
    return "\n".join(chunks)


def _safe_zip(raw: bytes) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except (OSError, zipfile.BadZipFile) as exc:
        raise SourceUnreadable("invalid office document") from exc
    if sum(i.file_size for i in archive.infolist()) > MAX_ZIP_EXPANDED:
        archive.close()
        raise SourceUnreadable("expanded document is too large")
    return archive


def _office_text(raw: bytes, ext: str, broad: bool) -> str:
    with _safe_zip(raw) as archive:
        names = set(archive.namelist())
        if ext == ".docx":
            wanted = ["word/document.xml"]
        elif ext == ".pptx":
            wanted = sorted(
                n for n in names
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", n))
        else:  # xlsx: shared strings plus cell values from each sheet
            wanted = (["xl/sharedStrings.xml"]
                      if "xl/sharedStrings.xml" in names else [])
            wanted += sorted(
                n for n in names
                if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
        if broad:
            wanted = wanted[:1]
        chunks = []
        for name in wanted:
            if name not in names:
                continue
            chunks.append(_xml_text(archive.read(name)))
            if sum(len(x) for x in chunks) >= MAX_EXPANDED_TEXT:
                break
        return "\n".join(chunks)[:MAX_EXPANDED_TEXT]


def _mail(raw: bytes, emlx: bool) -> tuple[str, Optional[str]]:
    if emlx:
        # Apple .emlx starts with the RFC822 message byte count.  Envelope
        # plist data may follow it; the count prevents parsing that as mail.
        first, sep, rest = raw.partition(b"\n")
        if sep and first.strip().isdigit():
            rest = rest[:int(first.strip())]
            raw = rest
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
    except Exception as exc:
        raise SourceUnreadable("invalid mail message") from exc
    title = str(message.get("subject") or "").strip() or None
    parts = []
    candidates: Iterable = message.walk() if message.is_multipart() else (message,)
    for part in candidates:
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        if part.get_content_type() not in ("text/plain", "text/html"):
            continue
        try:
            content = part.get_content()
        except Exception:
            continue
        if part.get_content_type() == "text/html":
            content = re.sub(r"<[^>]+>", " ", str(content))
        parts.append(str(content))
        if sum(len(x) for x in parts) >= MAX_EXPANDED_TEXT:
            break
    return "\n".join(parts)[:MAX_EXPANDED_TEXT], title


def _pdf(raw: bytes, broad: bool) -> str:
    try:
        from pypdf import PdfReader
        pdf = PdfReader(io.BytesIO(raw), strict=False)
        pages = pdf.pages[:1] if broad else pdf.pages
        return "\n".join((p.extract_text() or "") for p in pages)[
            :MAX_EXPANDED_TEXT]
    except Exception as exc:
        raise SourceUnreadable("unreadable PDF") from exc


def read_source(path: str, broad: bool = False) -> DecodedSource:
    """Decode one supported source and fingerprint its exact input bytes."""
    ext = Path(path).suffix.lower()
    if ext not in SUPPORTED_EXTS:
        raise SourceUnreadable("unsupported format")
    limit = MAX_DOCUMENT_BYTES if ext in DOCUMENT_EXTS else MAX_TEXT_BYTES
    try:
        size = os.path.getsize(path)
        if size > limit:
            raise SourceUnreadable("too big")
        with open(path, "rb") as fh:
            raw = fh.read(limit + 1)
    except OSError as exc:
        raise SourceUnreadable("unreadable") from exc
    if len(raw) > limit:
        raise SourceUnreadable("too big")

    fingerprint = hashlib.sha256(raw).hexdigest()
    title = None
    if ext in TEXT_EXTS:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SourceUnreadable("not UTF-8") from exc
    elif ext in MAIL_EXTS:
        text, title = _mail(raw, ext == ".emlx")
    elif ext == ".pdf":
        text = _pdf(raw, broad)
    else:
        text = _office_text(raw, ext, broad)
    return DecodedSource(text=text, fingerprint=fingerprint, title=title)
