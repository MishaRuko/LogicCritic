"""The one way the backend asks Claude for a structured answer.

A forced, strict tool call: the reply is validated against a pydantic model, an answer cut off
mid-way is refused, and token usage is counted into a tally the caller owns.
"""

import json
from typing import Any

import anthropic
from pydantic import BaseModel, ValidationError

from app.config import get_settings
from app.services.claude_errors import describe_claude_failure
from app.services.claude_tools import strict_tool

CALL_TIMEOUT_SECONDS = 90


class ClaudeCallFailed(Exception):
    """The model could not give a usable answer. The message says why, for users and agents."""


def new_tally() -> dict[str, int]:
    return {"calls": 0, "input_tokens": 0, "output_tokens": 0}


def get_client() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(
        api_key=get_settings().claude_api_key, timeout=CALL_TIMEOUT_SECONDS
    )


async def structured_call[T: BaseModel](
    client: Any,
    *,
    model: str,
    system: str,
    content: Any,
    tool_name: str,
    description: str,
    schema: type[T],
    task: str,
    max_tokens: int = 8192,
    tally: dict[str, int] | None = None,
    force_tool: bool = True,
    allow_text_json: bool = False,
    use_tools: bool = True,
) -> T:
    """Ask for one `schema`-shaped answer. Raises ClaudeCallFailed if there is not one."""
    try:
        request = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [
                {
                    "role": "user",
                    "content": content
                    if isinstance(content, str)
                    else json.dumps(content, ensure_ascii=False),
                }
            ],
        }
        # Opus currently accepts tools but rejects forced tool selection. Evaluation calls opt out
        # while preserving the same validated schema when the model submits its result.
        if use_tools:
            request["tools"] = [strict_tool(tool_name, description, schema)]
        if use_tools and force_tool:
            request["tool_choice"] = {"type": "tool", "name": tool_name}
        response = await client.messages.create(**request)
    except anthropic.APIError as error:
        detail = describe_claude_failure(error) or f"the request failed ({type(error).__name__})"
        raise ClaudeCallFailed(detail) from error
    if tally is not None:
        usage = response.usage
        tally["calls"] += 1
        tally["input_tokens"] += getattr(usage, "input_tokens", 0) or 0
        tally["output_tokens"] += getattr(usage, "output_tokens", 0) or 0
    if response.stop_reason == "max_tokens":
        raise ClaudeCallFailed(f"the {task} was cut off before it finished")
    block = next(
        (b for b in response.content if b.type == "tool_use" and b.name == tool_name), None
    )
    if block is None:
        if allow_text_json:
            text = "".join(
                getattr(item, "text", "") for item in response.content if item.type == "text"
            )
            try:
                return schema.model_validate_json(text)
            except ValidationError:
                # Some models wrap otherwise valid JSON in a short explanation or a code fence.
                start, end = text.find("{"), text.rfind("}")
                if start >= 0 and end > start:
                    try:
                        return schema.model_validate_json(text[start : end + 1])
                    except ValidationError:
                        pass
        kinds = ", ".join(f"{item.type}:{getattr(item, 'name', '')}" for item in response.content)
        preview = text[:400].replace("\n", " ") if allow_text_json else ""
        raise ClaudeCallFailed(
            f"the {task} returned no result ({response.stop_reason}; {kinds}; {preview})"
        )
    try:
        return schema.model_validate(dict(block.input))
    except ValidationError as error:
        raise ClaudeCallFailed(f"the {task} returned a malformed result") from error
