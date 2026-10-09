import json

import httpx

from app.services import literature
from app.services.literature import LiteratureClient, Paper, merge

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2603.23361v2</id>
    <title>Central Dogma
      Transformer III</title>
    <summary>We present CDT-III.</summary>
    <published>2026-03-24T15:57:23Z</published>
    <arxiv:doi>10.48550/x</arxiv:doi>
  </entry>
</feed>"""
EMPTY = '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'


def test_merge_interleaves_indexes_and_folds_duplicates() -> None:
    amass = [
        Paper(["amass"], "Trial of X", ids={"amass_id": "A1", "pmid": "1"}, retracted=True),
        Paper(["amass"], "Second", ids={"amass_id": "A2"}),
    ]
    s2 = [
        Paper(["semantic_scholar"], "TRIAL of X.", ids={"doi": "10.1/x"}, citations=50),
        Paper(["semantic_scholar"], "Other", ids={"pmid": "9"}),
    ]
    arxiv = [Paper(["arxiv"], "Preprint", ids={"doi": "10.1/X"})]  # same DOI as s2's first

    merged = merge(amass, s2, arxiv, limit=3)

    assert [p.title for p in merged] == ["Trial of X", "Second", "Other"]
    first = merged[0]
    assert first.found_in == ["amass", "semantic_scholar", "arxiv"]
    assert first.ids == {"amass_id": "A1", "pmid": "1", "doi": "10.1/x"}
    assert first.citations == 50 and first.retracted is True


async def test_semantic_scholar_search_and_arxiv_fallback_to_any_term(monkeypatch) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "api.semanticscholar.org":
            body = {
                "data": [
                    {
                        "paperId": "abc",
                        "title": "Dexamethasone in Covid-19",
                        "abstract": "It lowered mortality.",
                        "venue": "",
                        "year": 2021,
                        "citationCount": 5027,
                        "externalIds": {"DOI": "10.1056/x", "PubMed": "32678530", "CorpusId": 1},
                        "openAccessPdf": {"url": ""},
                    }
                ]
            }
            return httpx.Response(200, text=json.dumps(body))
        query = request.url.params["search_query"]
        return httpx.Response(200, text=EMPTY if " AND all:" in query else ATOM)

    async def no_wait(_):
        return None

    monkeypatch.setattr(literature.asyncio, "sleep", no_wait)
    client = LiteratureClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    [s2] = await client.search_semantic_scholar("dexamethasone covid", 5, "2020-01-01")
    assert s2.ids == {"semantic_scholar_id": "abc", "doi": "10.1056/x", "pmid": "32678530"}
    assert (s2.published, s2.citations, s2.venue, s2.open_access_pdf) == ("2021", 5027, None, None)
    assert requests[0].url.params["publicationDateOrYear"] == "2020-01-01:"

    [paper] = await client.search_arxiv("what is the central dogma transformer", 5, "2024-01-01")
    assert paper.title == "Central Dogma Transformer III"
    assert paper.ids == {"arxiv_id": "2603.23361", "doi": "10.48550/x"}
    assert paper.open_access_pdf == "https://arxiv.org/pdf/2603.23361"
    assert paper.published == "2026-03-24"
    queries = [r.url.params["search_query"] for r in requests[1:]]
    assert queries == [
        "(all:central AND all:dogma AND all:transformer) AND submittedDate:"
        "[202401010000 TO 300001010000]",
        "(all:central OR all:dogma OR all:transformer) AND submittedDate:"
        "[202401010000 TO 300001010000]",
    ]


async def test_openalex_answers_when_semantic_scholar_refuses(monkeypatch) -> None:
    work = {
        "doi": "https://doi.org/10.48550/arXiv.2101.00001",
        "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/123"},
        "display_name": "A study",
        "publication_date": "2021-01-01",
        "cited_by_count": 7,
        "is_retracted": False,
        "primary_location": {"source": {"display_name": "Journal"}},
        "best_oa_location": {"pdf_url": "https://oa.example/a.pdf"},
        "abstract_inverted_index": {"rose": [2], "Mortality": [0], "not": [1]},
    }
    paths, searched = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.host == "api.semanticscholar.org":
            return httpx.Response(429)
        if request.url.path.endswith("/works"):
            assert request.url.params["filter"] == "from_publication_date:2020-01-01"
            searched.append(request.url.params["search"])
            return httpx.Response(200, text=json.dumps({"results": [work]}))
        return httpx.Response(200, text=json.dumps(work))

    async def no_wait(_):
        return None

    monkeypatch.setattr(literature.asyncio, "sleep", no_wait)
    client = LiteratureClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    [paper] = await client.search_semantic_scholar("x?", 5, "2020-01-01")
    assert "?" not in searched[0]  # OpenAlex refuses ? as a wildcard
    assert paper.found_in == ["openalex"] and paper.abstract == "Mortality not rose"
    assert paper.ids == {
        "doi": "10.48550/arXiv.2101.00001",
        "pmid": "123",
        "arxiv_id": "2101.00001",
    }
    assert (paper.venue, paper.citations, paper.retracted) == ("Journal", 7, False)
    assert paper.open_access_pdf == "https://oa.example/a.pdf"

    found = await client.lookup_semantic_scholar("PMID:123")
    assert found.title == "A study" and paths[-1] == "/works/pmid:123"


async def test_citations_are_followed_both_ways_most_cited_first() -> None:
    def s2(paper_id: str, title: str, cited: int) -> dict:
        return {"paperId": paper_id, "title": title, "citationCount": cited, "externalIds": {}}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["limit"] == "200"  # a wide page, ranked here
        if request.url.path.endswith("/paper/DOI:10.1/review/references"):
            rows = [
                {"citedPaper": s2("a", "Small trial", 12)},
                {"citedPaper": s2("b", "Landmark trial", 900)},
                {"citedPaper": s2("h", "Cochrane Handbook for Systematic Reviews", 43000)},
                {"citedPaper": {"paperId": None, "title": None}},  # unresolved reference
            ]
            return httpx.Response(200, text=json.dumps({"data": rows}))
        assert request.url.path.endswith("/paper/DOI:10.1/review/citations")
        rows = [{"citingPaper": s2("c", "A rebuttal", 40)}]
        return httpx.Response(200, text=json.dumps({"data": rows}))

    client = LiteratureClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    cited = await client.citations("DOI:10.1/review", "references", 5)
    assert [p.title for p in cited] == ["Landmark trial", "Small trial"]
    # Words for what is wanted put matching works ahead of the more cited ones.
    cited = await client.citations("DOI:10.1/review", "references", 5, about="small trials")
    assert [p.title for p in cited] == ["Small trial", "Landmark trial"]
    citing = await client.citations("DOI:10.1/review", "cited_by", 5)
    assert [p.title for p in citing] == ["A rebuttal"]


async def test_openalex_follows_citations_when_semantic_scholar_refuses(monkeypatch) -> None:
    filters = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.semanticscholar.org":
            return httpx.Response(429)
        if request.url.path.endswith("/works/doi:10.1/review"):
            return httpx.Response(200, text=json.dumps({"id": "https://openalex.org/W7"}))
        filters.append((request.url.params["filter"], request.url.params["sort"]))
        work = {"display_name": "Cited study", "cited_by_count": 3}
        return httpx.Response(200, text=json.dumps({"results": [work]}))

    async def no_wait(_):
        return None

    monkeypatch.setattr(literature.asyncio, "sleep", no_wait)
    client = LiteratureClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    [paper] = await client.citations("DOI:10.1/review", "references", 5)
    await client.citations("DOI:10.1/review", "cited_by", 5)
    assert paper.title == "Cited study" and paper.found_in == ["openalex"]
    assert filters == [("cited_by:W7", "cited_by_count:desc"), ("cites:W7", "cited_by_count:desc")]


async def test_the_same_request_is_answered_from_memory_and_failures_are_not_remembered(
    monkeypatch,
) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(400)  # an error, and the fallback finds nothing: not remembered
        row = {"paperId": "a", "title": "A trial", "citationCount": 3, "externalIds": {}}
        return httpx.Response(200, text=json.dumps({"data": [row]}))

    client = LiteratureClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(client, "_search_openalex", _no_fallback)
    assert await client.search_semantic_scholar("x", 5) == []
    first = await client.search_semantic_scholar("x", 5)
    first[0].found_in.append("changed by a merge")
    again = await client.search_semantic_scholar("x", 5)
    assert len(calls) == 2  # the second success came from memory
    assert again[0].found_in == ["semantic_scholar"]  # a copy, untouched by the merge


async def _no_fallback(query, limit, published_after):
    return []


async def test_openalex_gets_the_key_and_a_refusing_semantic_scholar_is_rested(
    monkeypatch,
) -> None:
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "openalex_api_key", "test-openalex-key")
    hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.host == "api.semanticscholar.org":
            return httpx.Response(429)
        assert request.url.params["api_key"] == "test-openalex-key"
        work = {"display_name": f"Work for {request.url.params['search']}", "cited_by_count": 1}
        return httpx.Response(200, text=json.dumps({"results": [work]}))

    async def no_wait(_):
        return None

    monkeypatch.setattr(literature.asyncio, "sleep", no_wait)
    client = LiteratureClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await client.search_semantic_scholar("first", 5)
    assert hosts.count("api.semanticscholar.org") == 4  # tried, with retries
    await client.search_semantic_scholar("second", 5)
    # Resting: the second search goes straight to OpenAlex.
    assert hosts.count("api.semanticscholar.org") == 4 and hosts[-1] == "api.openalex.org"
