import pytest
from pydantic import BaseModel, Field

from app.schemas import ArgumentCheckOutput, ExtractionOutput
from app.services.claude_tools import strict_input_schema, strict_tool
from app.services.synthesis import LinkAudits, LinkProposals


def objects(node):
    """Every object schema inside a (possibly nested) JSON schema."""
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for value in node.values():
            yield from objects(value)
    elif isinstance(node, list):
        for value in node:
            yield from objects(value)


@pytest.mark.parametrize(
    "model", [ExtractionOutput, ArgumentCheckOutput, LinkProposals, LinkAudits]
)
def test_every_object_is_closed_and_every_property_required(model) -> None:
    schema = strict_input_schema(model)
    found = list(objects(schema))
    assert found
    for obj in found:
        assert obj["additionalProperties"] is False
        assert obj["required"] == list(obj["properties"])


def test_fields_with_defaults_are_still_required() -> None:
    class Result(BaseModel):
        items: list[str] = Field(default_factory=list)

    assert strict_input_schema(Result)["required"] == ["items"]


def test_a_nullable_field_must_be_present_but_may_be_null() -> None:
    nested = strict_input_schema(ExtractionOutput)["$defs"]["ExtractedStatement"]
    assert "role" in nested["required"]
    assert {"type": "null"} in nested["properties"]["role"]["anyOf"]


def test_the_tool_is_marked_strict_and_keeps_its_name() -> None:
    tool = strict_tool("submit_link_proposals", "Return links.", LinkProposals)
    assert tool["strict"] is True and tool["name"] == "submit_link_proposals"
    assert tool["input_schema"]["required"] == ["links"]


def test_ids_stay_uuid_formatted_so_the_api_enforces_them() -> None:
    link = strict_input_schema(LinkProposals)["$defs"]["ProposedLink"]["properties"]
    assert link["source_statement_id"]["format"] == "uuid"
