import httpx
import pytest

from app.services.http_retry import send


def client(replies):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        reply = replies[min(len(calls), len(replies)) - 1]
        if isinstance(reply, Exception):
            raise reply
        return reply

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


async def test_a_busy_or_failing_service_is_waited_out_with_growing_pauses() -> None:
    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    http, calls = client([httpx.Response(429), httpx.Response(503), httpx.Response(200)])
    response = await send(http, "GET", "https://api.test/x", sleep=sleep)
    assert response.status_code == 200 and len(calls) == 3 and slept == [2.0, 5.0]


async def test_it_gives_up_after_three_retries_and_hands_back_the_last_answer() -> None:
    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    http, calls = client([httpx.Response(429)])
    response = await send(http, "GET", "https://api.test/x", sleep=sleep)
    assert response.status_code == 429 and len(calls) == 4 and slept == [2.0, 5.0, 10.0]


async def test_a_requested_wait_is_honoured_unless_it_is_too_long() -> None:
    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    http, _ = client([httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200)])
    assert (await send(http, "GET", "https://api.test/x", sleep=sleep)).status_code == 200
    assert slept == [7.0]

    http, calls = client([httpx.Response(429, headers={"Retry-After": "120"})])
    assert (await send(http, "GET", "https://api.test/x", sleep=sleep)).status_code == 429
    assert len(calls) == 1  # better to try another source than wait two minutes


async def test_client_errors_are_not_retried_and_unreachable_services_are() -> None:
    async def sleep(seconds):
        pass

    http, calls = client([httpx.Response(404)])
    assert (await send(http, "GET", "https://api.test/x", sleep=sleep)).status_code == 404
    assert len(calls) == 1

    http, calls = client([httpx.ConnectError("down"), httpx.Response(200)])
    assert (await send(http, "GET", "https://api.test/x", sleep=sleep)).status_code == 200

    http, calls = client([httpx.ReadTimeout("slow")])
    with pytest.raises(httpx.ReadTimeout):
        await send(http, "GET", "https://api.test/x", sleep=sleep)
    assert len(calls) == 4
