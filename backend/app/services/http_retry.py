"""Waiting and retrying for the outside APIs the research tools call (Semantic Scholar,
OpenAlex, arXiv, Amass).

A busy service answers 429 or a 5xx, and an unreachable one times out. Both usually pass within
seconds, so a request is retried after 2, 5 and 10 seconds (longer if the service asks for it
with Retry-After) before the error is passed on. A service asking for a longer wait than
`MAX_WAIT_SECONDS` is not waited for: the agent is better off trying another source.

Claude calls are not sent through this: the Anthropic SDK retries them itself.
"""

import asyncio
from collections.abc import Awaitable, Callable

import httpx

RETRY_STATUSES = {429, 500, 502, 503, 504}
WAITS = (2.0, 5.0, 10.0)
MAX_WAIT_SECONDS = 30.0


def retry_after(response: httpx.Response) -> float | None:
    """The wait a service asked for, in seconds, if it gave one as a number."""
    try:
        return float(response.headers["Retry-After"])
    except (KeyError, ValueError):
        return None


async def send(
    http: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    waits: tuple[float, ...] = WAITS,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    before: Callable[[], Awaitable[None]] | None = None,
    asked_wait: Callable[[httpx.Response], float | None] = retry_after,
    **kwargs,
) -> httpx.Response:
    """Send a request, retrying while the service is busy, failing or unreachable.

    Returns the last response (the caller decides what a 4xx or a final 429 means) and raises
    the last transport error if the service could never be reached. `before` runs ahead of every
    attempt, for a client-side rate limiter; `asked_wait` reads the wait a service asked for,
    for services that put it somewhere other than Retry-After.
    """
    for wait in (*waits, None):
        if before is not None:
            await before()
        try:
            response = await http.request(method, url, **kwargs)
        except httpx.TransportError:  # timeouts and connection failures
            if wait is None:
                raise
        else:
            if response.status_code not in RETRY_STATUSES or wait is None:
                return response
            asked = asked_wait(response)
            if asked is not None and asked > MAX_WAIT_SECONDS:
                return response
            wait = max(wait, asked or 0.0)
        await (sleep or asyncio.sleep)(wait)
    raise AssertionError("unreachable")  # pragma: no cover
