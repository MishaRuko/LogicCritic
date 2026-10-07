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
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.host == "api.semanticscholar.org":
            return httpx.Response(429)
        if request.url.path.endswith("/works"):
            assert request.url.params["filter"] == "from_publication_date:2020-01-01"
            return httpx.Response(200, text=json.dumps({"results": [work]}))
        return httpx.Response(200, text=json.dumps(work))

    async def no_wait(_):
        return None

    monkeypatch.setattr(literature.asyncio, "sleep", no_wait)
    client = LiteratureClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    [paper] = await client.search_semantic_scholar("x", 5, "2020-01-01")
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
