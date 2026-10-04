import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedExcerpt:
    text: str
    sequence: int
    locator: dict[str, int | str]


def parse_structured_text(text: str) -> list[ParsedExcerpt]:
    """Split plain text/Markdown into paragraph-level excerpts with stable character locators."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").rstrip()
    current_section: str | None = None
    excerpts: list[ParsedExcerpt] = []

    for match in re.finditer(r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", normalized, flags=re.DOTALL):
        block = match.group(0)
        stripped = block.strip()
        if not stripped:
            continue

        heading = re.fullmatch(r"#{1,6}\s+(.+?)\s*", stripped)
        if heading:
            current_section = heading.group(1)

        locator: dict[str, int | str] = {
            "start": match.start(),
            "end": match.end(),
            "sequence": len(excerpts),
        }
        if current_section is not None:
            locator["section"] = current_section

        excerpts.append(ParsedExcerpt(text=stripped, sequence=len(excerpts), locator=locator))

    return excerpts
