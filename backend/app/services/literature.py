"""Open scholarly indexes the research agent searches alongside Amass: Semantic Scholar and arXiv.

Both are free and need no key. Semantic Scholar's keyless pool is shared and often refuses
requests (429), so when it does, OpenAlex answers instead (free without a key, about 100 searches
a day). Results are normalised to `Paper` so the toolbox can merge them with Amass results.

Google Scholar (through SerpAPI) can be added the same way: a search method here that returns
`Paper`s, and one more entry in `Toolbox.search_papers` and in `SearchPapersInput.sources`.
"""

import asyncio
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import httpx

from app.config import get_settings

S2_URL = "https://api.semanticscholar.org/graph/v1"
S2_FIELDS = "title,abstract,venue,publicationDate,year,citationCount,externalIds,openAccessPdf"
OPENALEX_URL = "https://api.openalex.org/works"
OPENALEX_FIELDS = (
    "doi,ids,display_name,publication_date,cited_by_count,is_retracted,primary_location,"
    "best_oa_location,abstract_inverted_index"
)
ARXIV_URL = "https://export.arxiv.org/api/query"
ARXIV_SPACING_SECONDS = 3.0  # arXiv asks for no more than one request every three seconds
ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
ARXIV_ID = re.compile(r"arxiv\.org/(?:abs|pdf)/([^\s?#]+?)(?:v\d+)?(?:\.pdf)?$")
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

    # -- Semantic Scholar ---------------------------------------------------------------------

    async def _s2_get(self, path: str, params: dict) -> dict | None:
        key = get_settings().semantic_scholar_api_key
        headers = {"x-api-key": key} if key else {}
        for attempt in range(2):
            response = await self._http.get(f"{S2_URL}{path}", params=params, headers=headers)
            if response.status_code == 429 and attempt == 0:
                await asyncio.sleep(2)  # the keyless pool is shared and briefly throttles
                continue
            if response.status_code == 404:
                return None
            if response.status_code != 200:
                raise LiteratureError(f"Semantic Scholar returned {response.status_code}.")
            return response.json()
        raise LiteratureError("Semantic Scholar is rate limiting requests; try again shortly.")

    async def search_semantic_scholar(
        self, query: str, limit: int, published_after: str | None = None
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
        try:
            body = await self._s2_get(f"/paper/{paper_id}", {"fields": S2_FIELDS})
        except (LiteratureError, httpx.HTTPError):
            kind, _, value = paper_id.partition(":")
            if kind not in ("DOI", "PMID"):
                raise
            return await self._lookup_openalex(f"{kind.lower()}:{value}")
        return _s2_paper(body) if body else None

    # -- OpenAlex (the fallback) --------------------------------------------------------------

    async def _openalex_get(self, url: str, params: dict) -> dict | None:
        response = await self._http.get(url, params={**params, "select": OPENALEX_FIELDS})
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise LiteratureError(f"OpenAlex returned {response.status_code}.")
        return response.json()

    async def _search_openalex(
        self, query: str, limit: int, published_after: str | None
    ) -> list[Paper]:
        params = {"search": query, "per_page": limit}
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
                response = await self._http.get(
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
