import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.config import get_settings

BIOMEDCORE = "biomedcore"
# A 429 that asks for a short wait is retried once; longer waits are passed back to the caller.
MAX_AUTO_RETRY_SECONDS = 10.0


class AmassError(Exception):
    """An Amass request failed. `code` is Amass's own error code, or a local one."""

    def __init__(
        self, status: int, code: str, message: str, retry_after: float | None = None
    ) -> None:
        super().__init__(f"Amass {status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after


class AmassNotConfigured(AmassError):
    def __init__(self) -> None:
        super().__init__(503, "NOT_CONFIGURED", "AMASS_API_KEY is not configured.")


class BiomedRecord(BaseModel):
    """The BiomedCore fields this app uses. Unknown fields are ignored, not rejected."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")

    amass_id: str
    pmid: str | None = None
    pmcid: str | None = None
    doi: str | None = None
    url: str | None = None
    title: str | None = None
    abstract: str | None = None
    authors: list[str] = Field(default_factory=list)
    journal: str | None = None
    publication_date: str | None = None
    last_update_date: str | None = None
    is_retracted: bool | None = None
    citation_count: float | None = None
    has_fulltext: bool | None = None
    fulltext: str | None = None


class SlidingWindowLimiter:
    """At most `limit` requests in any `window` seconds, shared by everything using it.

    Amass applies its limit per organisation, so this only protects against this process
    exceeding it. Another process (the worker) has its own window.
    """

    def __init__(
        self,
        limit: int,
        window: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.limit = limit
        self.window = window
        self._clock = clock
        self._sleep = sleep
        self._sent: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                while self._sent and now - self._sent[0] >= self.window:
                    self._sent.popleft()
                if len(self._sent) < self.limit:
                    self._sent.append(now)
                    return
                await self._sleep(self.window - (now - self._sent[0]))


class AmassClient:
    """A small async client for the Amass Data Platform API (BiomedCore)."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.amass.tech/api/v1",
        http: httpx.AsyncClient | None = None,
        limiter: SlidingWindowLimiter | None = None,
        timeout: float = 30.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http = http or httpx.AsyncClient(timeout=timeout)
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._limiter = limiter or SlidingWindowLimiter(60)
        self._sleep = sleep

    async def search_biomedcore(
        self,
        query: str,
        limit: int = 10,
        *,
        min_publication_date: str | None = None,
        max_publication_date: str | None = None,
        min_citation_count: int | None = None,
        is_retracted: bool | None = None,
    ) -> list[BiomedRecord]:
        params: dict[str, Any] = {"query": query, "limit": limit}
        if min_publication_date:
            params["minPublicationDate"] = min_publication_date
        if max_publication_date:
            params["maxPublicationDate"] = max_publication_date
        if min_citation_count is not None:
            params["minCitationCount"] = min_citation_count
        if is_retracted is not None:
            params["isRetracted"] = "true" if is_retracted else "false"
        body = await self._request("GET", f"/cores/{BIOMEDCORE}/records", params=params)
        return [BiomedRecord.model_validate(item) for item in body["data"]]

    async def get_biomedcore(self, amass_id: str, *, fulltext: bool = False) -> dict:
        """The raw record, so a snapshot keeps every field Amass returned."""
        params = {"include": "fulltext"} if fulltext else None
        body = await self._request("GET", f"/cores/{BIOMEDCORE}/records/{amass_id}", params=params)
        return body["data"] if "data" in body else body

    async def lookup_biomedcore(
        self, *, pmid: str | None = None, doi: str | None = None
    ) -> list[str]:
        """Canonical Amass IDs for a PMID or DOI. Empty when Amass does not know it."""
        if (pmid is None) == (doi is None):
            raise ValueError("Give exactly one of pmid or doi")
        item = {"pmid": pmid} if pmid is not None else {"doi": doi}
        body = await self._request(
            "POST", f"/cores/{BIOMEDCORE}/records/lookup", json={"items": [item]}
        )
        result = body["data"][0] if body.get("data") else {}
        if "error" in result:
            return []
        return list(result.get("amassIds", []))

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        for attempt in (1, 2):
            await self._limiter.acquire()
            try:
                response = await self._http.request(
                    method, f"{self._base_url}{path}", headers=self._headers, **kwargs
                )
            except httpx.HTTPError as error:
                raise AmassError(
                    502,
                    "UNREACHABLE",
                    f"Could not reach Amass ({type(error).__name__}): {error}".rstrip(": "),
                ) from error
            if response.is_success:
                return response.json()
            error = _error_from(response)
            if (
                error.status == 429
                and attempt == 1
                and (error.retry_after or 0) <= MAX_AUTO_RETRY_SECONDS
            ):
                await self._sleep(error.retry_after or 1.0)
                continue
            raise error
        raise AssertionError("unreachable")  # pragma: no cover


def _error_from(response: httpx.Response) -> AmassError:
    try:
        error = response.json()["error"]
        retry = error.get("retryAfter")
        if retry is None and response.headers.get("Retry-After"):
            retry = float(response.headers["Retry-After"])
        return AmassError(
            response.status_code,
            str(error.get("code", "ERROR")),
            str(error.get("message", "Request failed")),
            float(retry) if retry is not None else None,
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        return AmassError(response.status_code, "ERROR", response.text[:200] or "Request failed")


def get_amass_client() -> AmassClient:
    """FastAPI dependency. Tests override it with a fake."""
    settings = get_settings()
    if not settings.amass_api_key:
        raise AmassNotConfigured()
    return _shared_client(settings.amass_api_key, settings.amass_base_url)


_client: AmassClient | None = None


def _shared_client(api_key: str, base_url: str) -> AmassClient:
    """One client (and so one rate-limit window) for the life of the process."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = AmassClient(
            api_key,
            base_url,
            limiter=SlidingWindowLimiter(settings.amass_requests_per_minute),
            timeout=settings.amass_timeout_seconds,
        )
    return _client
