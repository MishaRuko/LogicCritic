"""Open scholarly indexes the research agent searches alongside Amass: Semantic Scholar and arXiv.

Both are free and need no key. Semantic Scholar's keyless pool is shared and often refuses
requests (429), so when it does, OpenAlex answers instead (free without a key, about 100 searches
a day). Results are normalised to `Paper` so the toolbox can merge them with Amass results.

Google Scholar (through SerpAPI) can be added the same way: a search method here that returns
`Paper`s, and one more entry in `Toolbox.search_papers` and in `SearchPapersInput.sources`.
"""

import asyncio
import copy
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import get_settings
from app.services.http_retry import send

S2_URL = "https://api.semanticscholar.org/graph/v1"
S2_FIELDS = "title,abstract,venue,publicationDate,year,citationCount,externalIds,openAccessPdf"
OPENALEX_URL = "https://api.openalex.org/works"
OPENALEX_FIELDS = (
    "doi,ids,display_name,publication_date,cited_by_count,is_retracted,primary_location,"
    "best_oa_location,abstract_inverted_index"
)
CACHE_SECONDS = 24 * 3600
S2_REST_SECONDS = 300  # Semantic Scholar is skipped this long after it keeps refusing
CACHE_SIZE = 2000  # requests remembered per process
CITATION_PAGE = 200  # citing or cited works fetched before ranking them by citations
ARXIV_URL = "https://export.arxiv.org/api/query"
ARXIV_SPACING_SECONDS = 3.0  # arXiv asks for no more than one request every three seconds
ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
ARXIV_ID = re.compile(r"arxiv\.org/(?:abs|pdf)/([^\s?#]+?)(?:v\d+)?(?:\.pdf)?$")
# Works cited for method, not findings: reporting standards, statistics, software, handbooks.
METHOD_REFERENCE = re.compile(
    r"preferred reporting items|prisma|risk of bias|cochrane (handbook|collaboration)|handbook|"
    r"heterogeneity|publication bias|funnel plot|meta-analys[ie]s? (detected|in)|"
    r"introduction to meta-analysis|language and environment for statistical|"
    r"mostly harmless econometrics|grade guidelines|statistical power analysis|"
    r"r package|inconsistency in meta|effect sizes|file drawer|"
    r"observational studies in epidemiology|guidelines? (for|on)|"
    r"^bleu$|^bert:|^bart:|ieee conference on computer vision",
    re.IGNORECASE,
)
STOPWORDS = set(
    "a an and the of in on for to with by is are does do what how or vs versus from at as".split()
)


class LiteratureError(Exception):
    """A search index could not answer. The message is for the agent."""


@dataclass
class Paper:
    """One search result, whichever index it came from."""

    found_in: list[str]
    title: str | None
    abstract: str | None = None
    venue: str | None = None
    published: str | None = None
    citations: int | None = None
    retracted: bool | None = None
    open_access_pdf: str | None = None
    ids: dict[str, str] = field(default_factory=dict)  # amass_id, doi, pmid, arxiv_id, s2 id

    def keys(self) -> list[tuple[str, str]]:
        """What identifies this paper across indexes, for merging duplicates."""
        keys = [(name, value.lower()) for name, value in self.ids.items() if value]
        if self.title:
            keys.append(("title", re.sub(r"[^a-z0-9]", "", self.title.lower())))
        return keys


def merge(*result_lists: list[Paper], limit: int) -> list[Paper]:
    """Interleave the indexes' rankings and fold duplicates (same DOI, PMID, arXiv ID or title)
    into the first occurrence, so each index's best results appear near the top."""
    merged: list[Paper] = []
    seen: dict[tuple[str, str], Paper] = {}
    depth = max((len(r) for r in result_lists), default=0)
    for rank in range(depth):
        for results in result_lists:
            if rank >= len(results):
                continue
            paper = results[rank]
            same = next((seen[k] for k in paper.keys() if k in seen), None)
            if same is None:
                if len(merged) >= limit:
                    continue
                merged.append(paper)
                same = paper
            else:
                same.found_in += [i for i in paper.found_in if i not in same.found_in]
                for name, value in paper.ids.items():
                    same.ids.setdefault(name, value)
                for attr in ("abstract", "venue", "published", "citations", "open_access_pdf"):
                    if getattr(same, attr) is None:
                        setattr(same, attr, getattr(paper, attr))
                if paper.retracted:
                    same.retracted = True
            for key in paper.keys():
                seen.setdefault(key, same)
    return merged


class LiteratureClient:
    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self._http = http or httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        self._arxiv_lock = asyncio.Lock()
        self._arxiv_last = 0.0
        self._cache: dict[tuple, tuple[float, Any]] = {}
        self._s2_resting_until = 0.0

    async def _cached(self, key: tuple, compute: Callable[[], Awaitable[Any]]) -> Any:
        """The same request within a day is answered from memory: repeated questions and
        follow-up runs ask the indexes the same things, and the keyless pools are scarce. Errors
        and empty answers are not remembered. Copies are handed out because merging changes
        papers in place."""
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit is not None and hit[0] > now:
            return copy.deepcopy(hit[1])
        value = await compute()
        if not value:
            return value  # an empty answer is often a sign of trouble: ask again next time
        if len(self._cache) >= CACHE_SIZE:
            self._cache.pop(next(iter(self._cache)))  # the oldest entry
        self._cache[key] = (now + CACHE_SECONDS, value)
        return copy.deepcopy(value)

    # -- Semantic Scholar ---------------------------------------------------------------------

    async def _s2_get(self, path: str, params: dict) -> dict | None:
        key = get_settings().semantic_scholar_api_key
        headers = {"x-api-key": key} if key else {}
        if time.monotonic() < self._s2_resting_until:
            raise LiteratureError("Semantic Scholar is rate limiting requests; try again shortly.")
        # The keyless pool is shared with every keyless user and often refuses for a while.
        response = await send(self._http, "GET", f"{S2_URL}{path}", params=params, headers=headers)
        if response.status_code == 404:
            return None
        if response.status_code == 429:
            # Still refusing after the retries: go straight to the fallback for a while rather
            # than spend every search waiting on it.
            self._s2_resting_until = time.monotonic() + S2_REST_SECONDS
            raise LiteratureError("Semantic Scholar is rate limiting requests; try again shortly.")
        if response.status_code != 200:
            raise LiteratureError(f"Semantic Scholar returned {response.status_code}.")
        return response.json()

    async def search_semantic_scholar(
        self, query: str, limit: int, published_after: str | None = None
    ) -> list[Paper]:
        return await self._cached(
            ("s2", query, limit, published_after),
            lambda: self._search_semantic_scholar(query, limit, published_after),
        )

    async def _search_semantic_scholar(
        self, query: str, limit: int, published_after: str | None
    ) -> list[Paper]:
        params = {"query": query, "limit": limit, "fields": S2_FIELDS}
        if published_after:
            params["publicationDateOrYear"] = f"{published_after}:"
        try:
            body = await self._s2_get("/paper/search", params) or {}
        except (LiteratureError, httpx.HTTPError):
            return await self._search_openalex(query, limit, published_after)
        return [_s2_paper(item) for item in body.get("data") or []]

    async def lookup_semantic_scholar(self, paper_id: str) -> Paper | None:
        """A paper by Semantic Scholar ID, or prefixed DOI:, PMID: or ARXIV: identifier."""
        return await self._cached(("lookup", paper_id), lambda: self._lookup(paper_id))

    async def _lookup(self, paper_id: str) -> Paper | None:
        try:
            body = await self._s2_get(f"/paper/{paper_id}", {"fields": S2_FIELDS})
        except (LiteratureError, httpx.HTTPError):
            kind, _, value = paper_id.partition(":")
            if kind not in ("DOI", "PMID"):
                raise
            return await self._lookup_openalex(f"{kind.lower()}:{value}")
        return _s2_paper(body) if body else None

    async def citations(
        self, paper_id: str, direction: str, limit: int, about: str | None = None
    ) -> list[Paper]:
        """The works a paper cites (`references`) or the works citing it (`cited_by`): those
        sharing words with `about` first, then the most cited. `paper_id` is as for
        `lookup_semantic_scholar`."""
        return await self._cached(
            ("citations", paper_id, direction, limit, about),
            lambda: self._citations(paper_id, direction, limit, about),
        )

    async def _citations(
        self, paper_id: str, direction: str, limit: int, about: str | None
    ) -> list[Paper]:
        edge, key = (
            ("references", "citedPaper")
            if direction == "references"
            else ("citations", "citingPaper")
        )
        try:
            # The order Semantic Scholar returns is not by importance, so take a wide page.
            body = await self._s2_get(
                f"/paper/{paper_id}/{edge}", {"fields": S2_FIELDS, "limit": CITATION_PAGE}
            )
        except (LiteratureError, httpx.HTTPError):
            return _rank(await self._citations_openalex(paper_id, direction, limit), about)
        if body is None:
            return []
        papers = [_s2_paper(row[key]) for row in body.get("data") or [] if row.get(key)]
        papers = [p for p in papers if p.title]
        if direction == "references":
            papers = [p for p in papers if not METHOD_REFERENCE.search(p.title)]
        return _rank(papers, about)[:limit]

    async def _citations_openalex(self, paper_id: str, direction: str, limit: int) -> list[Paper]:
        kind, _, value = paper_id.partition(":")
        if kind not in ("DOI", "PMID"):
            raise LiteratureError(
                "Following citations needs a DOI or PMID while Semantic Scholar is unavailable."
            )
        response = await send(
            self._http,
            "GET",
            f"{OPENALEX_URL}/{kind.lower()}:{value}",
            params=openalex_params({"select": "id"}),
        )
        if response.status_code != 200:
            return []
        work = response.json()["id"].rsplit("/", 1)[-1]
        relation = "cited_by" if direction == "references" else "cites"
        body = await self._openalex_get(
            OPENALEX_URL,
            {"filter": f"{relation}:{work}", "sort": "cited_by_count:desc", "per_page": limit},
        )
        papers = [_openalex_paper(item) for item in (body or {}).get("results") or []]
        if direction == "references":
            papers = [p for p in papers if not METHOD_REFERENCE.search(p.title or "")]
        return papers

    # -- OpenAlex (the fallback) --------------------------------------------------------------

    async def _openalex_get(self, url: str, params: dict) -> dict | None:
        response = await send(
            self._http, "GET", url, params=openalex_params({**params, "select": OPENALEX_FIELDS})
        )
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise LiteratureError(f"OpenAlex returned {response.status_code}.")
        return response.json()

    async def _search_openalex(
        self, query: str, limit: int, published_after: str | None
    ) -> list[Paper]:
        # OpenAlex reads ? and * as wildcards and refuses a query using them that way (400).
        params = {"search": re.sub(r"[?*]", " ", query), "per_page": limit}
        if published_after:
            params["filter"] = f"from_publication_date:{published_after}"
        body = await self._openalex_get(OPENALEX_URL, params) or {}
        return [_openalex_paper(item) for item in body.get("results") or []]

    async def _lookup_openalex(self, work_id: str) -> Paper | None:
        body = await self._openalex_get(f"{OPENALEX_URL}/{work_id}", {})
        return _openalex_paper(body) if body else None

    # -- arXiv --------------------------------------------------------------------------------

    async def search_arxiv(
        self, query: str, limit: int, published_after: str | None = None
    ) -> list[Paper]:
        return await self._cached(
            ("arxiv", query, limit, published_after),
            lambda: self._search_arxiv(query, limit, published_after),
        )

    async def _search_arxiv(
        self, query: str, limit: int, published_after: str | None
    ) -> list[Paper]:
        terms = [w for w in re.findall(r"[A-Za-z0-9-]+", query) if w.lower() not in STOPWORDS]
        if not terms:
            return []
        date = (
            f" AND submittedDate:[{published_after.replace('-', '')}0000 TO 300001010000]"
            if published_after
            else ""
        )
        # Every term must match; if that finds nothing, any term may.
        for joiner in (" AND ", " OR "):
            expression = joiner.join(f"all:{t}" for t in terms[:8])
            papers = await self._arxiv_query(f"({expression}){date}", limit)
            if papers:
                return papers
        return []

    async def _arxiv_query(self, search_query: str, limit: int) -> list[Paper]:
        async with self._arxiv_lock:
            wait = self._arxiv_last + ARXIV_SPACING_SECONDS - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                response = await send(
                    self._http,
                    "GET",
                    ARXIV_URL,
                    params={
                        "search_query": search_query,
                        "max_results": limit,
                        "sortBy": "relevance",
                    },
                )
            finally:
                self._arxiv_last = time.monotonic()
        if response.status_code != 200:
            raise LiteratureError(f"arXiv returned {response.status_code}.")
        return [
            _arxiv_paper(entry)
            for entry in ET.fromstring(response.text).iter(f"{{{ATOM['a']}}}entry")
        ]


def openalex_params(params: dict) -> dict:
    """OpenAlex query parameters, with the API key when one is configured."""
    key = get_settings().openalex_api_key
    return {**params, "api_key": key} if key else params


def _words(text: str | None) -> set[str]:
    words = re.findall(r"[a-z0-9]{4,}", (text or "").lower())
    return {w.removesuffix("s") for w in words if w not in STOPWORDS}  # trials matches trial


def _rank(papers: list[Paper], about: str | None) -> list[Paper]:
    """On-topic first (shared words with `about`), then by citations. Citation count alone
    puts a field's background (guidelines, statistics, burden studies) ahead of the studies."""
    topic = _words(about)
    return sorted(
        papers,
        key=lambda p: (len(_words(p.title) & topic), p.citations or 0),
        reverse=True,
    )


def _s2_paper(item: dict) -> Paper:
    external = item.get("externalIds") or {}
    ids = {
        "semantic_scholar_id": item.get("paperId"),
        "doi": external.get("DOI"),
        "pmid": external.get("PubMed"),
        "arxiv_id": external.get("ArXiv"),
    }
    return Paper(
        found_in=["semantic_scholar"],
        title=item.get("title"),
        abstract=item.get("abstract"),
        venue=item.get("venue") or None,
        published=item.get("publicationDate") or (str(item["year"]) if item.get("year") else None),
        citations=item.get("citationCount"),
        open_access_pdf=(item.get("openAccessPdf") or {}).get("url") or None,
        ids={k: str(v) for k, v in ids.items() if v},
    )


def _openalex_paper(item: dict) -> Paper:
    doi = (item.get("doi") or "").removeprefix("https://doi.org/") or None
    pmid = ((item.get("ids") or {}).get("pmid") or "").rstrip("/").rsplit("/", 1)[-1] or None
    arxiv = (
        doi[len("10.48550/arxiv.") :] if doi and doi.lower().startswith("10.48550/arxiv.") else None
    )
    words = item.get("abstract_inverted_index") or {}
    # OpenAlex ships abstracts as {word: [positions]}; put the words back in order.
    order = sorted((i, word) for word, positions in words.items() for i in positions)
    source = (item.get("primary_location") or {}).get("source") or {}
    ids = {"doi": doi, "pmid": pmid, "arxiv_id": arxiv}
    return Paper(
        found_in=["openalex"],
        title=item.get("display_name"),
        abstract=" ".join(word for _, word in order) or None,
        venue=source.get("display_name"),
        published=item.get("publication_date"),
        citations=item.get("cited_by_count"),
        retracted=item.get("is_retracted"),
        open_access_pdf=(item.get("best_oa_location") or {}).get("pdf_url"),
        ids={k: v for k, v in ids.items() if v},
    )


def _arxiv_paper(entry: ET.Element) -> Paper:
    def text(path: str) -> str | None:
        found = entry.find(path, ATOM)
        return " ".join(found.text.split()) if found is not None and found.text else None

    match = ARXIV_ID.search(text("a:id") or "")
    arxiv_id = match.group(1) if match else None
    ids = {"arxiv_id": arxiv_id, "doi": text("arxiv:doi")}
    return Paper(
        found_in=["arxiv"],
        title=text("a:title"),
        abstract=text("a:summary"),
        venue="arXiv preprint",
        published=(text("a:published") or "")[:10] or None,
        open_access_pdf=f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else None,
        ids={k: v for k, v in ids.items() if v},
    )


_client: LiteratureClient | None = None


def get_literature_client() -> LiteratureClient:
    global _client
    if _client is None:
        _client = LiteratureClient()
    return _client
