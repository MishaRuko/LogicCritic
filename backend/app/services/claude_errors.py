import httpx
from fastapi import HTTPException, status


def describe_claude_failure(error: Exception) -> str:
    """Anthropic's own explanation of a failed call, for the error shown to the user.

    Only the response is described, never the request, so the API key cannot leak. Returns an
    empty string for failures that are not about the HTTP call (such as a malformed result).
    """
    response = getattr(error, "response", None)
    if response is not None:
        try:
            body = response.json()["error"]
            return f"Claude returned {response.status_code} {body['type']}: {body['message']}"
        except (ValueError, KeyError, TypeError):
            return f"Claude returned HTTP {response.status_code}."
    if isinstance(error, httpx.TimeoutException):
        return "The request to Claude timed out."
    if isinstance(error, httpx.HTTPError):
        return f"Could not reach Claude ({type(error).__name__})."
    return ""


def ensure_complete(body: dict, task: str) -> None:
    """Refuse an answer Claude was cut off in the middle of, rather than use part of it."""
    if body.get("stop_reason") == "max_tokens":
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Claude's {task} was cut off before it finished; no graph update was applied.",
        )
