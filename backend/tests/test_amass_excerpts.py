from app.services.amass import BiomedRecord
from app.services.amass_import import MAX_EXCERPT_CHARS, record_to_excerpts, split_block


def words(n: int, seed: str = "word") -> str:
    return " ".join(f"{seed}{i}" for i in range(n))


def assert_offsets_are_exact(text: str, pieces: list[tuple[str, int, str | None]]) -> None:
    for body, start, _ in pieces:
        assert text[start : start + len(body)] == body


def test_a_short_block_is_left_alone() -> None:
    assert split_block("A short abstract.", 40, "Intro") == [("A short abstract.", 40, "Intro")]


def test_a_structured_abstract_is_split_by_heading_and_labelled() -> None:
    text = (
        f"BACKGROUND: \n{words(110)}\nPATIENTS AND METHODS:\n{words(110)}\n"
        f"RESULTS:\n{words(110)}\nCONCLUSION:\nIt worked."
    ).replace("BACKGROUND: \n", "BACKGROUND:\n")
    assert len(text) > MAX_EXCERPT_CHARS

    pieces = split_block(text, 0, None)

    assert [section for _, _, section in pieces] == [
        "Background",
        "Patients And Methods",
        "Results",
        "Conclusion",
    ]
    assert pieces[0][0].startswith("BACKGROUND:") and pieces[-1][0].endswith("It worked.")
    assert_offsets_are_exact(text, pieces)


def test_a_long_notice_is_grouped_by_line_within_the_limit() -> None:
    lines = [words(60, f"l{i}x") for i in range(20)]  # ~400 chars each
    text = "\n".join(lines)
    pieces = split_block(text, 100, None)

    assert len(pieces) > 1
    assert all(len(body) <= MAX_EXCERPT_CHARS for body, _, _ in pieces)
    assert "".join(body.replace("\n", "") for body, _, _ in pieces) == "".join(lines)
    for body, start, _ in pieces:
        assert text[start - 100 : start - 100 + len(body)] == body  # offsets are shifted by `start`


def test_one_enormous_line_is_split_on_sentence_boundaries() -> None:
    text = " ".join(f"This is sentence number {i} of a very long paragraph." for i in range(120))
    pieces = split_block(text, 0, "Results")

    assert len(pieces) > 1 and all(len(body) <= MAX_EXCERPT_CHARS for body, _, _ in pieces)
    assert all(body.endswith(".") for body, _, _ in pieces)
    assert {section for _, _, section in pieces} == {"Results"}
    assert_offsets_are_exact(text, pieces)


def test_a_heading_with_nothing_under_it_produces_no_excerpt() -> None:
    text = f"{words(300)}\nCONCLUSION:"
    pieces = split_block(text, 0, None)
    assert all(body.strip() != "CONCLUSION:" for body, _, _ in pieces)


def test_retraction_notice_does_not_inherit_conclusion_section() -> None:
    text = (
        "CONCLUSION:\nThe treatment was associated with viral clearance.\n"
        "This article has been retracted: please see the publisher policy.\n"
        "Concerns were raised about the study methodology and conclusions."
    )

    pieces = split_block(text, 0, None)

    assert [section for _, _, section in pieces] == ["Conclusion", "Retraction Notice"]
    assert pieces[0][0].endswith("viral clearance.")
    assert pieces[1][0].startswith("This article has been retracted")
    assert_offsets_are_exact(text, pieces)


def test_record_excerpts_use_the_split_and_keep_jsonpath_offsets() -> None:
    abstract = f"BACKGROUND:\n{words(150)}\nRESULTS:\n{words(150)}\nCONCLUSION:\nShort."
    record = BiomedRecord.model_validate(
        {"amassId": "AMBC_1", "title": "T title", "abstract": abstract}
    )

    excerpts = record_to_excerpts(record)

    abstracts = [e for e in excerpts if e.locator["jsonPath"] == "abstract"]
    assert [e.locator.get("section") for e in abstracts] == ["Background", "Results", "Conclusion"]
    for e in abstracts:
        assert abstract[e.locator["start"] : e.locator["end"]] == e.text
    assert [e.sequence for e in excerpts] == list(range(len(excerpts)))
