import httpx
import pytest

from app.services.claude_errors import describe_claude_failure


def status_error(status: int, body: dict | str) -> httpx.HTTPStatusError:
    response = httpx.Response(
        status,
        json=body if isinstance(body, dict) else None,
        text=body if isinstance(body, str) else None,
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
    )
    return httpx.HTTPStatusError("failed", request=response.request, response=response)


def test_reports_anthropics_own_message() -> None:
    error = status_error(
        404, {"type": "error", "error": {"type": "not_found_error", "message": "model: gone"}}
    )
    assert describe_claude_failure(error) == "Claude returned 404 not_found_error: model: gone"


def test_falls_back_to_the_status_when_the_body_is_not_json() -> None:
    assert describe_claude_failure(status_error(502, "<html>bad gateway</html>")) == (
        "Claude returned HTTP 502."
    )


def test_describes_timeouts_and_network_failures() -> None:
    assert "timed out" in describe_claude_failure(httpx.ReadTimeout("slow"))
    assert "Could not reach Claude" in describe_claude_failure(httpx.ConnectError("no route"))


@pytest.mark.parametrize("error", [KeyError("content"), StopIteration(), ValueError("bad")])
def test_stays_silent_about_failures_that_are_not_the_http_call(error: Exception) -> None:
    assert describe_claude_failure(error) == ""


def test_the_request_and_its_api_key_are_never_part_of_the_description() -> None:
    request = httpx.Request("POST", "https://api.anthropic.com", headers={"x-api-key": "sk-secret"})
    error = httpx.HTTPStatusError(
        "x",
        request=request,
        response=httpx.Response(
            401,
            json={"error": {"type": "authentication_error", "message": "invalid x-api-key"}},
            request=request,
        ),
    )
    assert "sk-secret" not in describe_claude_failure(error)
