from typing import Any

from anthropic import transform_schema
from pydantic import BaseModel


def strict_input_schema(model: type[BaseModel]) -> dict[str, Any]:
    """A JSON schema the API can enforce exactly, built from a pydantic model.

    The SDK's transformer closes every object (`additionalProperties: false`) and folds the
    constraints strict mode does not support into the descriptions. It leaves fields that have
    defaults optional, and a model that is free to omit a list will sometimes omit it, so every
    property is made required too. A nullable field must still be present, as null.
    """
    schema = transform_schema(model)
    _require_every_property(schema)
    return schema


def strict_tool(name: str, description: str, model: type[BaseModel]) -> dict[str, Any]:
    """A tool definition whose input is guaranteed to match `model`.

    Without `strict`, Claude sometimes returns an array as a JSON-encoded string, or leaves a
    list out, and the answer is lost to a validation error.
    """
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": strict_input_schema(model),
    }


def _require_every_property(node: Any) -> None:
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            node["required"] = list(node["properties"])
        for value in node.values():
            _require_every_property(value)
    elif isinstance(node, list):
        for value in node:
            _require_every_property(value)
