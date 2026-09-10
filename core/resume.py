"""Resume ingestion: uploaded bytes in, searchable text and an ATS score out.

This replaces ``modules/parser.py``, ``modules/cleaner.py`` and ``modules/ats.py``.
Three things changed in the port:

* ``import fitz`` became ``import pymupdf`` — the ``fitz`` alias is deprecated and
  emits a warning on every import in PyMuPDF 1.28.
* DOCX and plain text are accepted, not only PDF. Candidates send what they have.
* The ATS score reports *which* requirements matched. A bare number that a
  candidate cannot act on is not worth showing, and a recruiter needs to see why
  a resume was filtered before an LLM ever saw it.

No LLM and no database access here, so this module is cheap to unit test.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from core.config import settings

log = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = (".pdf", ".docx", ".txt", ".md")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# Below this, there is nothing worth scoring: a scanned image yields a handful of
# stray words, and a candidate is better told the floor than given a 0% match.
MIN_RESUME_WORDS = 40


class ResumeError(ValueError):
    """The upload cannot be turned into text worth scoring."""


@dataclass(frozen=True)
class Resume:
    text: str
    sha256: str
    source: str
    words: int
    pages: int = 0
    path: Path | None = None

    @property
    def stored_at(self) -> str:
        return str(self.path) if self.path else ""


@dataclass(frozen=True)
class AtsResult:
    score: int
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        total = len(self.matched) + len(self.missing)
        return f"{len(self.matched)}/{total} keywords" if total else "no requirements set"


# --------------------------------------------------------------------------- #
# Text extraction
# --------------------------------------------------------------------------- #


def _from_pdf(path: Path) -> tuple[str, int]:
    import pymupdf  # imported lazily: a text resume should not need it

    with pymupdf.open(path) as doc:
        pages = [page.get_text() for page in doc]
    return "\n".join(pages), len(pages)


def _from_docx(path: Path) -> tuple[str, int]:
    import docx

    document = docx.Document(str(path))
    parts = [para.text for para in document.paragraphs]
    # Skills and dates are very often laid out in tables, which paragraphs skip.
    for table in document.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(part for part in parts if part.strip()), 0


def _from_text(path: Path) -> tuple[str, int]:
    return path.read_text(encoding="utf-8", errors="replace"), 0


def extract_text(path: str | Path) -> tuple[str, int, str]:
    """Return ``(raw_text, page_count, source_kind)`` for a resume file."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text, pages = _from_pdf(path)
        return text, pages, "pdf"
    if suffix == ".docx":
        text, pages = _from_docx(path)
        return text, pages, "docx"
    if suffix in (".txt", ".md"):
        text, pages = _from_text(path)
        return text, pages, "text"
    raise ResumeError(
        f"Unsupported resume format {suffix or '(none)'}. "
        f"Upload one of: {', '.join(SUPPORTED_SUFFIXES)}."
    )


# --------------------------------------------------------------------------- #
# Cleaning
# --------------------------------------------------------------------------- #

# PDF extraction habitually returns letter-spaced words ("P h o n e") and
# ligature glyphs. Both wreck keyword matching, so they are normalised once,
# here, before anything else looks at the text.
_LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"}
_SPACED_LETTERS_RE = re.compile(r"(?<=\b[a-zA-Z]) (?=[a-zA-Z]\b)")
_BULLETS_RE = re.compile(r"[•·▪◦‣]")
_MULTI_NEWLINE_RE = re.compile(r"\n{2,}")
_HORIZONTAL_WS_RE = re.compile(r"[ \t\u00a0]+")  # \u00a0 = NBSP; PDF text is full of them


def clean_text(text: str) -> str:
    if not text:
        return ""
    for glyph, replacement in _LIGATURES.items():
        text = text.replace(glyph, replacement)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _BULLETS_RE.sub("-", text)
    text = _SPACED_LETTERS_RE.sub("", text)
    text = _HORIZONTAL_WS_RE.sub(" ", text)
    text = _MULTI_NEWLINE_RE.sub("\n", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def word_count(text: str) -> int:
    return len(text.split())


# --------------------------------------------------------------------------- #
# ATS keyword score
# --------------------------------------------------------------------------- #

# Keep the characters that carry meaning in tech keywords: "c++", "c#", "node.js".
_NORMALIZE_RE = re.compile(r"[^a-z0-9+#.]+")


def _normalize(text: str) -> str:
    return _NORMALIZE_RE.sub(" ", (text or "").lower()).strip()


def _requirement_pattern(requirement: str) -> re.Pattern[str] | None:
    normalized = _normalize(requirement)
    if not normalized:
        return None
    # Whitespace inside a requirement matches any run of whitespace, so
    # "system design" still matches a line break between the two words.
    body = r"\s+".join(re.escape(part) for part in normalized.split())
    # \b does not fire after "+" or "#", so those endings get a lookahead instead.
    tail = r"\b" if normalized[-1].isalnum() else r"(?![a-z0-9])"
    return re.compile(rf"(?<![a-z0-9]){body}{tail}")


def ats_score(text: str, requirements: list[str]) -> AtsResult:
    """Percentage of role requirements literally present in the resume.

    Deliberately dumb: it is the cheap pre-filter that runs before any model
    call, and its output has to be defensible to a candidate who asks why they
    were rejected. Semantic judgement is the LLM's job.
    """
    requirements = [req for req in (requirements or []) if str(req).strip()]
    if not text or not requirements:
        return AtsResult(score=0, matched=[], missing=list(requirements))

    haystack = _normalize(text)
    matched: list[str] = []
    missing: list[str] = []
    for requirement in requirements:
        pattern = _requirement_pattern(str(requirement))
        if pattern is not None and pattern.search(haystack):
            matched.append(str(requirement))
        else:
            missing.append(str(requirement))

    score = round(len(matched) / len(requirements) * 100)
    return AtsResult(score=int(score), matched=matched, missing=missing)


# --------------------------------------------------------------------------- #
# Name guess
# --------------------------------------------------------------------------- #

_CONTACT_HINT_RE = re.compile(r"[@|]|\d{4,}|https?://|www\.")
_NAME_LINE_RE = re.compile(r"^[A-Za-z][A-Za-z.'\-]*(?: [A-Za-z.'\-]+){0,3}$")
_NOT_A_NAME = {
    "curriculum vitae",
    "resume",
    "cv",
    "profile",
    "summary",
    "objective",
    "contact",
}


def guess_name(text: str) -> str:
    """Best-effort name from the top of the resume.

    Only a fallback: the account's own name wins, and the LLM extracts a name
    too. Useful when a candidate uploads on behalf of a blank profile.
    """
    for line in (text or "").split("\n")[:8]:
        candidate = line.strip().strip("-").strip()
        if not candidate or len(candidate) > 60:
            continue
        if candidate.lower() in _NOT_A_NAME or _CONTACT_HINT_RE.search(candidate):
            continue
        if _NAME_LINE_RE.match(candidate) and len(candidate.split()) >= 2:
            return " ".join(part.capitalize() if part.isupper() else part
                            for part in candidate.split())
    return ""


# --------------------------------------------------------------------------- #
# Ingestion
# --------------------------------------------------------------------------- #


def _safe_stem(filename: str) -> str:
    stem = Path(filename or "resume").stem
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_")
    return (cleaned or "resume")[:48]


def store_upload(data: bytes, filename: str, *, prefix: str = "") -> Path:
    """Write an upload under ``UPLOAD_DIR`` with a collision-proof name.

    The digest goes in the filename so re-uploading the same file twice does not
    accumulate copies, and so a stored path can be traced back to a row.
    """
    if not data:
        raise ResumeError("The uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ResumeError(
            f"Resume is {len(data) / 1_048_576:.1f} MB; the limit is "
            f"{MAX_UPLOAD_BYTES // 1_048_576} MB."
        )
    suffix = Path(filename or "").suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ResumeError(
            f"Unsupported resume format {suffix or '(none)'}. "
            f"Upload one of: {', '.join(SUPPORTED_SUFFIXES)}."
        )

    settings.ensure_dirs()
    digest = hashlib.sha256(data).hexdigest()
    parts = [part for part in (prefix, _safe_stem(filename), digest[:12]) if part]
    path = settings.upload_dir / f"{'_'.join(parts)}{suffix}"
    path.write_bytes(data)
    return path


def ingest(
    data: bytes, filename: str, *, prefix: str = "", store: bool = True
) -> Resume:
    """Store the upload (optionally) and return its cleaned text.

    Raises :class:`ResumeError` with a message written for the candidate, since
    every failure here is something they can fix by uploading a better file.
    """
    path = store_upload(data, filename, prefix=prefix)
    try:
        raw, pages, source = extract_text(path)
    except ResumeError:
        raise
    except Exception as exc:  # corrupt PDF, password-protected DOCX, ...
        log.warning("Could not read resume %s: %s", path.name, exc)
        raise ResumeError(
            "That file could not be read. If it is a scanned image, export a "
            "text-based PDF and try again."
        ) from exc

    text = clean_text(raw)
    words = word_count(text)
    if words < MIN_RESUME_WORDS:
        raise ResumeError(
            "Only %d words of text came out of that file, and %d are needed — it "
            "looks like a scan. Upload a text-based PDF, a DOCX, or paste the text "
            "instead." % (words, MIN_RESUME_WORDS)
        )

    if not store:
        path.unlink(missing_ok=True)
        path = None  # type: ignore[assignment]

    return Resume(
        text=text,
        sha256=hashlib.sha256(data).hexdigest(),
        source=source,
        words=words,
        pages=pages,
        path=path,
    )


def from_text(text: str) -> Resume:
    """Ingest pasted text — the escape hatch when a file will not parse."""
    cleaned = clean_text(text)
    words = word_count(cleaned)
    if words < MIN_RESUME_WORDS:
        raise ResumeError(
            "That is %d words. Paste at least %d words of your resume."
            % (words, MIN_RESUME_WORDS)
        )
    return Resume(
        text=cleaned,
        sha256=hashlib.sha256(cleaned.encode("utf-8")).hexdigest(),
        source="pasted",
        words=words,
    )
