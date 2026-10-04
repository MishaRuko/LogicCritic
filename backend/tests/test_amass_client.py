import httpx
import pytest

from app.services.amass import AmassClient, AmassError, SlidingWindowLimiter


class Clock:
    """A controllable clock whose sleep advances time, so the limiter runs instantly."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def make_client(handler, clock: Clock | None = None, limit: int = 60) -> AmassClient:
    clock = clock or Clock()
    return AmassClient(
        "amass_test_key",
        base_url="https://amass.test/api/v1",
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        limiter=SlidingWindowLimiter(limit, clock=clock, sleep=clock.sleep),
        sleep=clock.sleep,
    )


def error_body(status: int, code: str, message: str = "boom", **extra) -> httpx.Response:
    return httpx.Response(
        status, json={"error": {"status": status, "code": code, "message": message, **extra}}
    )


RECORD = {"amassId": "AMBC_1", "title": "A study", "abstract": "It worked.", "isRetracted": False}


async def test_requests_carry_the_bearer_key_and_search_parameters() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["Authorization"]
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"data": [RECORD]})

    client = make_client(handler)
    records = await client.search_biomedcore(
        "kras inhibitors",
        5,
        min_publication_date="2020-01-01",
        min_citation_count=10,
        is_retracted=False,
    )

    assert seen["auth"] == "Bearer amass_test_key"
    assert seen["url"].startswith("https://amass.test/api/v1/cores/biomedcore/records?")
    for fragment in (
        "query=kras+inhibitors",
        "limit=5",
        "minPublicationDate=2020-01-01",
        "minCitationCount=10",
        "isRetracted=false",
    ):
        assert fragment in seen["url"]
    assert records[0].amass_id == "AMBC_1" and records[0].is_retracted is False


async def test_get_record_unwraps_data_and_asks_for_fulltext_only_when_wanted() -> None:
    urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json={"data": RECORD})

    client = make_client(handler)
    assert (await client.get_biomedcore("AMBC_1"))["title"] == "A study"
    await client.get_biomedcore("AMBC_1", fulltext=True)
    assert urls == [
        "https://amass.test/api/v1/cores/biomedcore/records/AMBC_1",
        "https://amass.test/api/v1/cores/biomedcore/records/AMBC_1?include=fulltext",
    ]


async def test_lookup_resolves_a_pmid_or_doi_and_reports_unknown_ids_as_empty() -> None:
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        if b"999" in request.content:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"input": {"pmid": "999"}, "error": {"code": "NOT_FOUND", "message": "x"}}
                    ]
                },
            )
        return httpx.Response(
            200, json={"data": [{"input": {"pmid": "1"}, "amassIds": ["AMBC_9"]}]}
        )

    client = make_client(handler)
    assert await client.lookup_biomedcore(pmid="1") == ["AMBC_9"]
    assert await client.lookup_biomedcore(pmid="999") == []
    assert b'"items"' in bodies[0] and b'"pmid"' in bodies[0]
    with pytest.raises(ValueError):
        await client.lookup_biomedcore(pmid="1", doi="10.1/x")


@pytest.mark.parametrize(
    ("response", "status", "code"),
    [
        (error_body(401, "UNAUTHORIZED", "bad key"), 401, "UNAUTHORIZED"),
        (error_body(404, "NOT_FOUND", "no such record"), 404, "NOT_FOUND"),
        (error_body(400, "BAD_REQUEST", "bad", fields={"query": "required"}), 400, "BAD_REQUEST"),
        (httpx.Response(500, text="<html>oops</html>"), 500, "ERROR"),
    ],
)
async def test_errors_become_amass_errors_with_their_code(response, status, code) -> None:
    client = make_client(lambda request: response)
    with pytest.raises(AmassError) as error:
        await client.get_biomedcore("AMBC_1")
    assert (error.value.status, error.value.code) == (status, code)


async def test_a_short_rate_limit_is_waited_out_once() -> None:
    clock = Clock()
    replies = iter(
        [
            error_body(429, "TOO_MANY_REQUESTS", retryAfter=3),
            httpx.Response(200, json={"data": RECORD}),
        ]
    )
    client = make_client(lambda request: next(replies), clock)

    assert (await client.get_biomedcore("AMBC_1"))["amassId"] == "AMBC_1"
    assert clock.slept == [3.0]


async def test_a_long_rate_limit_is_handed_back_to_the_caller() -> None:
    client = make_client(lambda request: error_body(429, "TOO_MANY_REQUESTS", retryAfter=45))
    with pytest.raises(AmassError) as error:
        await client.get_biomedcore("AMBC_1")
    assert error.value.status == 429 and error.value.retry_after == 45


async def test_repeated_rate_limiting_gives_up_after_one_retry() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return error_body(429, "TOO_MANY_REQUESTS", retryAfter=1)

    with pytest.raises(AmassError):
        await make_client(handler).get_biomedcore("AMBC_1")
    assert len(calls) == 2


async def test_network_failures_are_reported_as_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    with pytest.raises(AmassError) as error:
        await make_client(handler).get_biomedcore("AMBC_1")
    assert error.value.code == "UNREACHABLE"


async def test_limiter_spaces_out_requests_beyond_the_window() -> None:
    clock = Clock()
    limiter = SlidingWindowLimiter(2, window=60, clock=clock, sleep=clock.sleep)
    for _ in range(2):
        await limiter.acquire()
    assert clock.slept == []

    await limiter.acquire()  # the third has to wait for the first to leave the window
    assert clock.slept == [pytest.approx(60.0)]
    await limiter.acquire()  # the fourth is already clear
    assert len(clock.slept) == 1


async def test_a_network_failure_says_what_kind_even_when_it_has_no_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("")  # timeouts carry no text of their own

    with pytest.raises(AmassError) as caught:
        await make_client(handler).search_biomedcore("x", 5)
    assert caught.value.status == 502 and "ReadTimeout" in caught.value.message
