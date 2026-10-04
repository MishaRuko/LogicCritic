from app.services.text_ingestion import MAX_EXCERPT_CHARS, parse_structured_text


def test_parses_markdown_sections_with_stable_character_locators() -> None:
    text = (
        "# Results\n\nThe intervention reduced the marker.\n\n"
        "## Caveat\n\nThe sample was not randomized.\n"
    )

    excerpts = parse_structured_text(text)

    assert [item.text for item in excerpts] == [
        "# Results",
        "The intervention reduced the marker.",
        "## Caveat",
        "The sample was not randomized.",
    ]
    assert excerpts[1].locator["section"] == "Results"
    assert excerpts[3].locator["section"] == "Caveat"
    assert text[excerpts[1].locator["start"] : excerpts[1].locator["end"]] == excerpts[1].text


def test_parses_single_paragraph_with_a_trailing_newline() -> None:
    excerpts = parse_structured_text("A single source paragraph.\n")

    assert len(excerpts) == 1
    assert excerpts[0].text == "A single source paragraph."
    assert excerpts[0].locator == {"start": 0, "end": 26, "sequence": 0}


def test_splits_long_paragraphs_into_bounded_exact_excerpts() -> None:
    text = " ".join(f"Sentence {index}." for index in range(400))

    excerpts = parse_structured_text(text)

    assert len(excerpts) > 1
    assert all(len(item.text) <= MAX_EXCERPT_CHARS for item in excerpts)
    assert [item.sequence for item in excerpts] == list(range(len(excerpts)))
    assert all(
        text[item.locator["start"] : item.locator["end"]] == item.text for item in excerpts
    )


def test_keeps_exact_offsets_for_crlf_text_and_hard_splits_long_tokens() -> None:
    text = "# Results\r\n\r\n" + "x" * (MAX_EXCERPT_CHARS * 2 + 5)

    excerpts = parse_structured_text(text)

    assert len(excerpts) == 4
    assert all(len(item.text) <= MAX_EXCERPT_CHARS for item in excerpts)
    assert all(
        text[item.locator["start"] : item.locator["end"]] == item.text for item in excerpts
    )
