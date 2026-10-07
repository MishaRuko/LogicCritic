import re
from dataclasses import dataclass

MAX_EXCERPT_CHARS = 1_500
PARAGRAPH = re.compile(r"\S(?:.*?\S)?(?=(?:\r\n|\r|\n)[ \t]*(?:\r\n|\r|\n)|\s*\Z)", re.DOTALL)
SENTENCE_END = re.compile(r"[.!?](?:[\"')\]]*)\s+")


@dataclass(frozen=True)
class ParsedExcerpt:
    text: str
    sequence: int
    locator: dict[str, int | str]


def parse_structured_text(text: str, *, bounded: bool = True) -> list[ParsedExcerpt]:
    """Split plain text/Markdown into paragraph-level excerpts with stable character locators.

    `bounded=False` keeps whole paragraphs for callers that apply their own splitting.
    """
    current_section: str | None = None
    excerpts: list[ParsedExcerpt] = []

    for match in PARAGRAPH.finditer(text):
        block = match.group(0)
        stripped = block.strip()
        if not stripped:
            continue

        heading = re.fullmatch(r"#{1,6}\s+(.+?)\s*", stripped)
        if heading:
            current_section = heading.group(1)

        spans = (
            _bounded_spans(text, match.start(), match.end())
            if bounded
            else [(match.start(), match.end())]
        )
        for start, end in spans:
            locator: dict[str, int | str] = {
                "start": start,
                "end": end,
                "sequence": len(excerpts),
            }
            if current_section is not None:
                locator["section"] = current_section

            excerpts.append(
                ParsedExcerpt(text=text[start:end], sequence=len(excerpts), locator=locator)
            )

    return excerpts


def _bounded_spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Split one paragraph without losing exact offsets or exceeding the excerpt limit."""
    spans = []
    while end - start > MAX_EXCERPT_CHARS:
        window = text[start : start + MAX_EXCERPT_CHARS + 1]
        sentence_ends = [match.end() for match in SENTENCE_END.finditer(window)]
        cut = sentence_ends[-1] if sentence_ends else window.rfind(" ", 0, MAX_EXCERPT_CHARS + 1)
        if cut <= 0:
            cut = MAX_EXCERPT_CHARS
        piece_end = start + cut
        while piece_end > start and text[piece_end - 1].isspace():
            piece_end -= 1
        if piece_end > start:
            spans.append((start, piece_end))
        start += cut
        while start < end and text[start].isspace():
            start += 1
    if start < end:
        spans.append((start, end))
    return spans
