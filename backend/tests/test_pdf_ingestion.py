import io

import pytest
from pypdf import PdfWriter

from app.services import pdf_ingestion
from app.services.pdf_ingestion import PdfIngestionError, parse_pdf
from tests.pdf_factory import make_pdf

SENTENCE = "The treated group showed a measurable reduction in the primary outcome measure."


def texts(parsed) -> list[str]:
    return [item.text for item in parsed.excerpts]


def test_pages_are_recorded_in_order() -> None:
    parsed = parse_pdf(
        make_pdf(
            [
                ["Introduction", "First paragraph about the assay design."],
                ["Results", "Second paragraph on the second page."],
            ]
        )
    )
    assert texts(parsed) == [
        "First paragraph about the assay design.",
        "Second paragraph on the second page.",
    ]
    assert [item.locator["page"] for item in parsed.excerpts] == [1, 2]
    assert [item.sequence for item in parsed.excerpts] == [0, 1]
    assert parsed.page_count == 2 and parsed.metadata["parser"] == "pdf_pypdf_v1"


def with_page_text(monkeypatch: pytest.MonkeyPatch, text: str):
    """pypdf rarely emits blank lines, so exercise paragraph splitting on text that has them."""
    monkeypatch.setattr(pdf_ingestion, "_page_text", lambda page: text)
    return parse_pdf(make_pdf([[SENTENCE]]))


def test_blank_lines_split_paragraphs(monkeypatch: pytest.MonkeyPatch) -> None:
    parsed = with_page_text(
        monkeypatch, "First paragraph about the assay design.\n\nSecond paragraph on controls.\n"
    )
    assert texts(parsed) == [
        "First paragraph about the assay design.",
        "Second paragraph on controls.",
    ]


def test_offsets_index_the_page_text(monkeypatch: pytest.MonkeyPatch) -> None:
    page = "Alpha paragraph with enough words.\n\nBeta paragraph here with words.\n"
    parsed = with_page_text(monkeypatch, page)
    for item in parsed.excerpts:
        start, end = item.locator["start"], item.locator["end"]
        assert " ".join(page[start:end].split()) == item.text
    assert parsed.excerpts[1].locator["start"] > parsed.excerpts[0].locator["end"]


def test_wrapped_lines_are_joined_and_hyphenated_breaks_are_repaired() -> None:
    parsed = parse_pdf(make_pdf([["Gene regu-", "lation was measured in all", "tissue samples."]]))
    assert texts(parsed) == ["Gene regulation was measured in all tissue samples."]


def test_headings_label_sections_and_are_not_excerpts() -> None:
    parsed = parse_pdf(
        make_pdf(
            [
                [
                    "Abstract",
                    SENTENCE,
                    "",
                    "2 Methods",
                    "Participants were enrolled across three sites.",
                    "",
                    "3.1 Statistical Analysis",
                    "Outcomes were compared with a mixed model.",
                ]
            ]
        )
    )
    assert [(item.locator.get("section"), item.text.split()[0]) for item in parsed.excerpts] == [
        ("Abstract", "The"),
        ("Methods", "Participants"),
        ("Statistical Analysis", "Outcomes"),
    ]


def test_retraction_notice_does_not_inherit_a_conclusion_section() -> None:
    parsed = parse_pdf(
        make_pdf(
            [
                [
                    "Conclusion",
                    "The intervention improved the primary outcome.",
                    "",
                    "Retraction Notice",
                    "The journal withdrew the article after an investigation.",
                ]
            ]
        )
    )
    assert [(item.locator.get("section"), item.text) for item in parsed.excerpts] == [
        ("Conclusion", "The intervention improved the primary outcome."),
        ("Retraction Notice", "The journal withdrew the article after an investigation."),
    ]


def test_reference_list_is_skipped_until_the_next_real_section() -> None:
    parsed = parse_pdf(
        make_pdf(
            [
                ["Conclusions", "We conclude that the effect is real in this cohort."],
                ["References", "[1] Smith J. A study of things. Journal 2020.", "[2] Lee K. More."],
                ["Appendix", "Supplementary protocol details are listed here."],
            ]
        )
    )
    assert texts(parsed) == [
        "We conclude that the effect is real in this cohort.",
        "Supplementary protocol details are listed here.",
    ]


def test_running_headers_and_page_numbers_are_dropped() -> None:
    pages = [
        ["Journal of Examples, vol 9", f"Finding {word} appears only on this page.", f"{n}"]
        for n, word in enumerate(["alpha", "bravo", "charlie", "delta", "echo"], start=1)
    ]
    parsed = parse_pdf(make_pdf(pages))
    assert all("Journal of Examples" not in text for text in texts(parsed))
    assert len(parsed.excerpts) == 5


def test_a_long_paragraph_is_cut_on_sentence_boundaries() -> None:
    long_paragraph = [" ".join([SENTENCE] * 60)]
    parsed = parse_pdf(make_pdf([long_paragraph]))
    assert len(parsed.excerpts) > 1
    assert all(len(text) <= pdf_ingestion.MAX_EXCERPT_CHARS for text in texts(parsed))
    assert all(text.endswith(".") for text in texts(parsed))


def test_a_pdf_without_a_text_layer_is_rejected() -> None:
    with pytest.raises(PdfIngestionError) as error:
        parse_pdf(make_pdf([[]]))
    assert error.value.code == "no_text" and "OCR" in error.value.message


def test_garbage_is_reported_as_corrupt() -> None:
    with pytest.raises(PdfIngestionError) as error:
        parse_pdf(b"definitely not a pdf")
    assert error.value.code == "corrupt"


def test_password_protected_pdfs_are_rejected() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("secret")
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(PdfIngestionError) as error:
        parse_pdf(buffer.getvalue())
    assert error.value.code == "encrypted"


def test_page_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pdf_ingestion, "MAX_PAGES", 2)
    with pytest.raises(PdfIngestionError) as error:
        parse_pdf(make_pdf([[SENTENCE]] * 3))
    assert error.value.code == "too_many_pages"


def test_numbered_procedure_steps_are_not_discarded_as_headings():
    parsed = parse_pdf(
        make_pdf(
            [
                [
                    "Procedure",
                    "1. Spray hands with ethanol",
                    "2. Get all reagents",
                    "3. Place dish in cabinet",
                ]
            ]
        )
    )
    assert texts(parsed) == [
        "1. Spray hands with ethanol",
        "2. Get all reagents",
        "3. Place dish in cabinet",
    ]
    assert all(e.locator["section"] == "Procedure" for e in parsed.excerpts)


def test_protocol_phases_and_steps_survive_page_breaks():
    parsed = parse_pdf(
        make_pdf(
            [
                [
                    "Protocol",
                    "1. Preparation of the Gel",
                    "1. Weigh the agarose.",
                    "2. Add buffer.",
                ],
                [
                    "3. Pour the gel.",
                    "2. Separation of DNA Fragments",
                    "1. Load samples.",
                    "2. Run the gel.",
                    "3. Observing DNA fragments",
                    "1. Photograph the gel.",
                    "4. Representative Results",
                    "DNA fragments appear as distinct fluorescent bands.",
                ],
            ]
        )
    )
    methods = [e for e in parsed.excerpts if e.locator.get("methodology_section")]
    assert [e.text for e in methods] == [
        "1. Weigh the agarose.",
        "2. Add buffer.",
        "3. Pour the gel.",
        "1. Load samples.",
        "2. Run the gel.",
        "1. Photograph the gel.",
    ]
    groups = [e.locator["protocol_group"] for e in methods]
    assert groups[0] == groups[1] == groups[2]
    assert groups[3] == groups[4] and groups[3] != groups[0]
    assert groups[5] not in {groups[0], groups[3]}
    assert not parsed.excerpts[-1].locator.get("methodology_section")
