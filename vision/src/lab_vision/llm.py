import logging
from typing import Any, Protocol, TypeVar

import anthropic
from pydantic import BaseModel

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "claude-opus-5-5"
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(RuntimeError):
    """The model did not return a usable structured answer."""


class StructuredLLM(Protocol):
    """Asks a model a question and returns the answer as a validated pydantic object."""

    model: str

    def generate(self, *, system: str, content: list[dict[str, Any]], output: type[T]) -> T: ...


class ClaudeLLM:
    """Structured-output calls to Claude.

    The response schema is enforced by the API, so there is no JSON repair loop. Transient
    errors are retried by the SDK. Refusals are retried server-side on a fallback model where
    the platform supports it: life-science content such as plasmids and cell cultures can
    trip safety classifiers, and a refused window would otherwise be a gap in the record.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        effort: str | None = "medium",
        max_tokens: int = 8000,
        fallbacks: bool = True,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.fallbacks = fallbacks
        self.usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0}
        self.served_models: set[str] = set()
        # With no key given the SDK resolves credentials itself (environment or login profile).
        self._client = client or anthropic.Anthropic(api_key=api_key)

    def generate(self, *, system: str, content: list[dict[str, Any]], output: type[T]) -> T:
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": content}],
            "output_format": output,
        }
        if self.effort:
            request["output_config"] = {"effort": self.effort}
        try:
            if self.fallbacks:
                response = self._client.beta.messages.parse(
                    **request, betas=[_FALLBACK_BETA], fallbacks="default"
                )
            else:
                response = self._client.messages.parse(**request)
        except anthropic.APIError as exc:
            raise LLMError(f"Claude request failed: {exc}") from exc

        self._record(response)
        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None)
            raise LLMError(f"Claude declined the request (category: {category})")
        if response.stop_reason == "max_tokens":
            raise LLMError("Claude's answer was cut off; raise max_tokens")
        parsed = response.parsed_output
        if parsed is None:
            raise LLMError("Claude returned no structured output")
        return parsed

    def _record(self, response: Any) -> None:
        """Keep running totals, so a run can report what it cost and which model answered."""
        usage = getattr(response, "usage", None)
        self.usage["requests"] += 1
        self.usage["input_tokens"] += getattr(usage, "input_tokens", 0) or 0
        self.usage["output_tokens"] += getattr(usage, "output_tokens", 0) or 0
        model = getattr(response, "model", None)
        if isinstance(model, str):
            self.served_models.add(model)
