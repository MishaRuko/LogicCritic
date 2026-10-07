import re
from collections import Counter
from dataclasses import dataclass
from importlib.metadata import version
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from app.services.text_ingestion import ParsedExcerpt

PARSER = "pdf_pypdf_v1"
MAX_PAGES = 300
MAX_EXCERPT_CHARS = 1500
MIN_EXCERPT_CHARS = 25

# Headings recognised by name only, so ordinary short lines are never mistaken for sections.
SECTION_NAMES = (
    "abstract|summary|introduction|background|methods|materials and methods|methodology|"
    "experimental procedures|procedure|protocol|equipment|reagents|results|representative results|"
    "results and discussion|discussion|conclusions?|"
    "limitations|acknowledg(?:e)?ments|supplementary(?: materials?| information)?|appendix"
)
HEADING = re.compile(rf"^(?:\d+(?:\.\d+)*\.?\s+)?(?P<name>{SECTION_NAMES})\s*:?$", re.IGNORECASE)
# Any numbered heading ("3.1 Encoder and Decoder Stacks"): short, capitalised, no final period.
NUMBERED_HEADING = re.compile(r"^\d+(?:\.\d+){0,2}\.?\s+(?P<title>[A-Z][^.\n]{2,70})$")
REFERENCES = re.compile(r"^(?:\d+\.?\s+)?(?:references|bibliography|literature cited)\s*:?$", re.I)
RETRACTION_NOTICE = re.compile(r"^(?:retraction|expression of concern|editorial notice)\b", re.I)
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])")
HYPHEN_BREAK = re.compile(r"(?<=[a-z])-\n(?=[a-z])")
METHOD_NAMES = {
    "Methods",
    "Materials And Methods",
    "Methodology",
    "Experimental Procedures",
    "Procedure",
    "Protocol",
}
PROCEDURE_NAMES = {"Procedure", "Protocol", "Experimental Procedures"}


class PdfIngestionError(ValueError):
    """The PDF cannot be turned into excerpts. `code` says why; `message` is user-facing."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ParsedPdf:
    excerpts: list[ParsedExcerpt]
    page_count: int
    title: str | None
    metadata: dict


def parse_pdf(content: bytes) -> ParsedPdf:
    """Split a text-based PDF into paragraph excerpts located by page and character offsets.

    Offsets index a page's text after line-break hyphens are merged, as extracted by pypdf, so
    a locator is `{page, start, end}` with 1-based pages. Scanned (image-only) PDFs have no text
    layer and are rejected rather than ingested empty.
    """
    try:
        reader = PdfReader(BytesIO(content))
        if reader.is_encrypted and not reader.decrypt(""):
            raise PdfIngestionError("encrypted", "This PDF is password protected.")
        page_count = len(reader.pages)
    except PdfIngestionError:
        raise
    except (PyPdfError, ValueError, KeyError, OSError) as error:
        raise PdfIngestionError("corrupt", "This file is not a readable PDF.") from error
    if page_count > MAX_PAGES:
        raise PdfIngestionError(
            "too_many_pages", f"PDFs are limited to {MAX_PAGES} pages; this one has {page_count}."
        )

    pages = [_page_text(page) for page in reader.pages]
    noise = _running_lines(pages)

    excerpts: list[ParsedExcerpt] = []
    section: str | None = None
    method_section: str | None = None
    protocol_group: str | None = None
    in_references = False
    for number, text in enumerate(pages, start=1):
        for paragraph in _paragraphs(text, noise, in_procedure=method_section in PROCEDURE_NAMES):
            if paragraph.heading is not None:
                section = paragraph.heading
                if not paragraph.is_subsection:
                    method_section = section if section in METHOD_NAMES else None
                protocol_group = f"{number}:{paragraph.start}"
                in_references = paragraph.is_references
                continue
            if in_references:
                continue
            for start, end, chunk in _chunks(
                text,
                paragraph.start,
                paragraph.end,
                preserve_short=method_section in PROCEDURE_NAMES,
            ):
                locator: dict[str, int | str] = {
                    "page": number,
                    "start": start,
                    "end": end,
                    "sequence": len(excerpts),
                }
                if section is not None:
                    locator["section"] = section
                if method_section is not None:
                    locator["methodology_section"] = method_section
                    locator["protocol_group"] = protocol_group or method_section
                excerpts.append(ParsedExcerpt(text=chunk, sequence=len(excerpts), locator=locator))

    if not excerpts:
        raise PdfIngestionError(
            "no_text",
            "No extractable text found. Scanned PDFs need OCR, which is not supported yet.",
        )
    return ParsedPdf(
        excerpts=excerpts,
        page_count=page_count,
        title=_title(reader),
        metadata={"parser": PARSER, "pypdf": version("pypdf"), "page_count": page_count},
    )


def _page_text(page) -> str:
    try:
        text = page.extract_text() or ""
    except (PyPdfError, ValueError, KeyError, TypeError, RecursionError):
        return ""  # one unreadable page should not lose the document
    # Some font encodings extract as NUL characters, which Postgres text cannot store.
    text = text.replace("\x00", "")
    return HYPHEN_BREAK.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))


def _title(reader: PdfReader) -> str | None:
    try:
        title = (reader.metadata.title if reader.metadata else None) or ""
    except (PyPdfError, ValueError, KeyError):
        return None
    title = " ".join(str(title).replace("\x00", "").split())
    return title[:512] if len(title) >= 4 else None


def _running_lines(pages: list[str]) -> set[str]:
    """Lines that repeat on at least half the pages: running headers and footers."""
    if len(pages) < 4:
        return set()
    seen: Counter[str] = Counter()
    for text in pages:
        seen.update({_fingerprint(line) for line in text.split("\n") if line.strip()})
    return {line for line, count in seen.items() if count / len(pages) >= 0.5 and len(line) < 100}


def _fingerprint(line: str) -> str:
    # Page numbers change from page to page, so compare lines with digits removed.
    return re.sub(r"\d+", "", " ".join(line.split())).strip()


@dataclass(frozen=True)
class _Paragraph:
    start: int
    end: int
    heading: str | None = None
    is_references: bool = False
    is_subsection: bool = False


def _paragraphs(text: str, noise: set[str], *, in_procedure: bool = False) -> list[_Paragraph]:
    """Paragraph spans of a page. Headings come back as their own marker spans."""
    spans: list[_Paragraph] = []
    start: int | None = None
    end = 0
    offset = 0

    def close() -> None:
        nonlocal start
        if start is not None:
            spans.append(_Paragraph(start, end))
        start = None

    lines = text.split("\n")
    for index, line in enumerate(lines):
        line_start, line_end = offset, offset + len(line)
        offset = line_end + 1
        stripped = line.strip()
        if not stripped or _fingerprint(stripped) in noise:
            close()
            continue
        if REFERENCES.match(stripped):
            close()
            in_procedure = False
            spans.append(_Paragraph(line_start, line_end, heading="References", is_references=True))
            continue
        if RETRACTION_NOTICE.match(stripped):
            close()
            in_procedure = False
            spans.append(_Paragraph(line_start, line_end, heading="Retraction Notice"))
            continue
        heading = HEADING.match(stripped)
        if heading:
            close()
            in_procedure = heading.group("name").lower() in {
                "procedure",
                "protocol",
                "experimental procedures",
            }
            spans.append(_Paragraph(line_start, line_end, heading=heading.group("name").title()))
            continue
        numbered = NUMBERED_HEADING.match(stripped)
        # A protocol phase is followed by a new list starting at 1. Ordinary short
        # instructions without punctuation ("2. Get all reagents") remain steps.
        protocol_heading = False
        if numbered and in_procedure:
            next_line = next((line.strip() for line in lines[index + 1 :] if line.strip()), "")
            protocol_heading = bool(re.match(r"^1[.)]\s", next_line))
        if numbered and (protocol_heading or (not in_procedure and len(stripped.split()) <= 10)):
            close()
            spans.append(
                _Paragraph(
                    line_start,
                    line_end,
                    heading=numbered.group("title").strip(),
                    is_subsection=True,
                )
            )
            continue
        if in_procedure and re.match(r"^\d+[.)]\s", stripped):
            close()
        if start is None:
            start = line_start
        end = line_end
    close()
    return spans


def _chunks(
    text: str, start: int, end: int, preserve_short: bool = False
) -> list[tuple[int, int, str]]:
    """Cut one paragraph into excerpts of at most MAX_EXCERPT_CHARS, on sentence boundaries."""
    raw = text[start:end]
    pieces: list[tuple[int, int]] = []
    cursor = 0
    for match in SENTENCE_BREAK.finditer(raw):
        pieces.append((cursor, match.start()))
        cursor = match.end()
    pieces.append((cursor, len(raw)))

    chunks: list[tuple[int, int]] = []
    current: tuple[int, int] | None = None
    for piece in pieces:
        if current is not None and piece[1] - current[0] > MAX_EXCERPT_CHARS:
            chunks.append(current)
            current = None
        current = piece if current is None else (current[0], piece[1])
    if current is not None:
        chunks.append(current)

    results = []
    for chunk_start, chunk_end in chunks:
        # A single over-long "sentence" (a table, say) is still cut so no excerpt is huge.
        for offset in range(chunk_start, chunk_end, MAX_EXCERPT_CHARS):
            limit = min(offset + MAX_EXCERPT_CHARS, chunk_end)
            chunk = " ".join(raw[offset:limit].split())
            if (preserve_short or len(chunk) >= MIN_EXCERPT_CHARS) and re.search(
                r"[A-Za-z]{3}", chunk
            ):
                results.append((start + offset, start + limit, chunk))
    return results
