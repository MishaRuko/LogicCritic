import json

import httpx
import pytest
from sqlalchemy import delete

from app.config import get_settings
from app.database import engine, session_factory
from app.main import app
from app.models import AmassCacheEntry
from app.routes.amass import amass_client
from app.services.amass import AmassError, BiomedRecord

ABSTRACT = "Compound X reduced tumour volume in mice.\n\nNo effect was seen on body weight."
FULLTEXT = "# Methods\n\nMice were dosed daily for 14 days.\n\n# Results\n\nTumours shrank by half."


def record(amass_id="AMBC_1", pmid="111", doi="10.1000/x1", retracted=False, **extra) -> dict:
    return {
        "amassId": amass_id,
        "pmid": pmid,
        "pmcid": None,
        "doi": doi,
        "url": f"https://pubmed.test/{pmid}",
        "title": "Compound X in a mouse model",
        "abstract": ABSTRACT,
        "authors": [f"Author {n}" for n in range(12)],
        "journal": "Journal of Examples",
        "publicationDate": "2024-05-01",
        "lastUpdateDate": "2024-06-01",
        "isRetracted": retracted,
        "citationCount": 12,
        "hasFulltext": True,
        **extra,
    }


class FakeAmass:
    """Stands in for the Amass API and counts how often it is called."""

    def __init__(self, records: dict[str, dict]) -> None:
        self.records = records
        self.calls: list[tuple] = []
        self.error: AmassError | None = None

    async def lookup_biomedcore(self, *, pmid=None, doi=None):
        self.calls.append(("lookup", pmid or doi))
        for amass_id, item in self.records.items():
            if item.get("pmid") == pmid or item.get("doi") == doi:
                return [amass_id]
        return []

    async def get_biomedcore(self, amass_id, *, fulltext=False):
        self.calls.append(("get", amass_id, fulltext))
        if self.error:
            raise self.error
        item = dict(self.records[amass_id])
        if fulltext:
            item["fulltext"] = FULLTEXT
        return item

    async def search_biomedcore(self, query, limit=10, **filters):
        self.calls.append(("search", query, limit, filters))
        return [BiomedRecord.model_validate(item) for item in self.records.values()][:limit]

    def count(self, kind: str) -> int:
        return sum(1 for call in self.calls if call[0] == kind)


async def clear_cache() -> None:
    # The cache is global by design (it saves API credits across workspaces), so isolate tests.
    async with session_factory() as session:
        await session.execute(delete(AmassCacheEntry))
        await session.commit()


@pytest.fixture
async def amass(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path))
    fake = FakeAmass({"AMBC_1": record()})
    app.dependency_overrides[amass_client] = lambda: fake
    await engine.dispose()
    await clear_cache()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.fake = fake
        client.workspaces = []
        client.new_workspace = lambda: _new_workspace(client)
        client.workspace = await _new_workspace(client)
        yield client
        for workspace in client.workspaces:
            await client.delete(f"/api/workspaces/{workspace}")
    app.dependency_overrides.pop(amass_client, None)
    await engine.dispose()


async def _new_workspace(client) -> str:
    workspace = (await client.post("/api/workspaces", json={"title": "amass"})).json()["id"]
    client.workspaces.append(workspace)
    return workspace


def import_url(workspace: str) -> str:
    return f"/api/workspaces/{workspace}/integrations/amass/import"


async def test_search_returns_a_trimmed_view_without_saving_anything(amass) -> None:
    amass.fake.records["AMBC_1"]["abstract"] = "x" * 2000
    response = await amass.post(
        "/api/integrations/amass/search", json={"query": "compound x", "limit": 5}
    )

    assert response.status_code == 200
    [result] = response.json()["results"]
    assert result["amass_id"] == "AMBC_1" and result["doi"] == "10.1000/x1"
    assert len(result["authors"]) == 8 and len(result["abstract_preview"]) <= 603
    assert amass.fake.calls[0][3] == {
        "min_publication_date": None,
        "max_publication_date": None,
        "min_citation_count": None,
        "is_retracted": None,
    }


async def test_import_by_amass_id_creates_an_amass_source_with_located_excerpts(
    amass, tmp_path
) -> None:
    response = await amass.post(import_url(amass.workspace), json={"amass_id": "AMBC_1"})

    assert response.status_code == 201
    body = response.json()
    assert (body["already_imported"], body["retracted"]) == (False, False)
    source = body["source"]
    assert (source["kind"], source["origin"], source["mime_type"]) == (
        "amass_record",
        "amass",
        "application/json",
    )
    assert source["title"] == "Compound X in a mouse model"
    assert source["external_ids"] == {"amass_id": "AMBC_1", "pmid": "111", "doi": "10.1000/x1"}
    assert source["metadata"]["parser"] == "amass_biomedcore_v1"
    assert (
        source["metadata"]["fulltext_imported"] is True
        and source["metadata"]["journal"] == "Journal of Examples"
    )

    paths = [(e["locator"]["jsonPath"], e["text"]) for e in source["excerpts"]]
    assert paths == [
        ("title", "Compound X in a mouse model"),
        ("abstract", "Compound X reduced tumour volume in mice."),
        ("abstract", "No effect was seen on body weight."),
        ("fulltext", "# Methods"),
        ("fulltext", "Mice were dosed daily for 14 days."),
        ("fulltext", "# Results"),
        ("fulltext", "Tumours shrank by half."),
    ]
    sections = {e["text"]: e["locator"].get("section") for e in source["excerpts"]}
    assert sections["Mice were dosed daily for 14 days."] == "Methods"
    assert sections["Tumours shrank by half."] == "Results"
    assert "section" not in source["excerpts"][1]["locator"]  # the abstract has no headings

    results_excerpt = source["excerpts"][-1]
    start, end = results_excerpt["locator"]["start"], results_excerpt["locator"]["end"]
    assert FULLTEXT[start:end] == results_excerpt["text"]

    snapshot = json.loads(next(tmp_path.rglob("AMBC_1.json")).read_text())
    assert snapshot["record"]["amassId"] == "AMBC_1" and "retrieved_at" in snapshot


async def test_import_by_pmid_and_doi_resolves_the_id_first(amass) -> None:
    by_pmid = await amass.post(import_url(amass.workspace), json={"pmid": "111"})
    assert by_pmid.status_code == 201 and amass.fake.calls[0] == ("lookup", "111")

    second = await amass.new_workspace()
    by_doi = await amass.post(import_url(second), json={"doi": "10.1000/x1"})
    assert (
        by_doi.status_code == 201
        and by_doi.json()["source"]["external_ids"]["amass_id"] == "AMBC_1"
    )


async def test_importing_twice_returns_the_original_and_costs_no_amass_calls(amass) -> None:
    first = await amass.post(import_url(amass.workspace), json={"amass_id": "AMBC_1"})
    calls_before = len(amass.fake.calls)

    again = await amass.post(import_url(amass.workspace), json={"amass_id": "AMBC_1"})

    assert again.status_code == 200 and again.json()["already_imported"] is True
    assert again.json()["source"]["id"] == first.json()["source"]["id"]
    assert len(again.json()["source"]["excerpts"]) == 7
    assert len(amass.fake.calls) == calls_before


async def test_a_second_workspace_is_served_from_the_cache(amass) -> None:
    await amass.post(import_url(amass.workspace), json={"amass_id": "AMBC_1"})
    other = await amass.new_workspace()

    response = await amass.post(import_url(other), json={"amass_id": "AMBC_1"})

    assert response.status_code == 201
    assert amass.fake.count("get") == 1  # the second import read the cached record


async def test_a_cache_without_fulltext_is_refetched_when_fulltext_is_wanted(amass) -> None:
    await amass.post(
        import_url(amass.workspace), json={"amass_id": "AMBC_1", "include_fulltext": False}
    )
    other = await amass.new_workspace()
    response = await amass.post(import_url(other), json={"amass_id": "AMBC_1"})

    assert amass.fake.count("get") == 2
    assert response.json()["source"]["metadata"]["fulltext_imported"] is True


async def test_abstract_only_import_skips_the_fulltext(amass) -> None:
    response = await amass.post(
        import_url(amass.workspace), json={"amass_id": "AMBC_1", "include_fulltext": False}
    )
    assert {e["locator"]["jsonPath"] for e in response.json()["source"]["excerpts"]} == {
        "title",
        "abstract",
    }
    assert amass.fake.calls[-1] == ("get", "AMBC_1", False)


async def test_a_retracted_paper_is_invalidated_and_flagged_by_verification(amass) -> None:
    amass.fake.records["AMBC_2"] = record("AMBC_2", pmid="222", doi="10.1000/x2", retracted=True)
    imported = await amass.post(import_url(amass.workspace), json={"amass_id": "AMBC_2"})
    source = imported.json()["source"]
    assert imported.json()["retracted"] is True

    validity = (await amass.get(f"/api/sources/{source['id']}/validity")).json()
    assert validity["status"] == "invalidated" and "PubMed" in validity["reason"]
    assert validity["provenance"] == {"actor_type": "integration", "actor_id": "amass"}

    patch = await amass.post(
        f"/api/workspaces/{amass.workspace}/graph-patches",
        json={
            "idempotency_key": "claim-1",
            "operations": [
                {
                    "op": "create_statement",
                    "client_ref": "claim",
                    "text": "Compound X reduces tumour volume.",
                    "assertion_mode": "reported",
                    "excerpt_ids": [source["excerpts"][1]["id"]],
                    "provenance": {"actor_type": "user", "actor_id": "tester"},
                }
            ],
        },
    )
    statement_id = patch.json()["id_map"]["claim"]
    await amass.post(f"/api/workspaces/{amass.workspace}/verify")
    context = (
        await amass.get(f"/api/workspaces/{amass.workspace}/statements/{statement_id}/context")
    ).json()
    assert "invalidated_source" in {issue["rule_code"] for issue in context["issues"]}


async def test_a_clean_paper_has_no_validity_decision(amass) -> None:
    source = (await amass.post(import_url(amass.workspace), json={"amass_id": "AMBC_1"})).json()[
        "source"
    ]
    assert (await amass.get(f"/api/sources/{source['id']}/validity")).status_code == 404


async def test_refresh_invalidates_a_source_retracted_after_import_and_does_so_once(amass) -> None:
    source = (await amass.post(import_url(amass.workspace), json={"amass_id": "AMBC_1"})).json()[
        "source"
    ]
    refresh = f"/api/sources/{source['id']}/amass/refresh"

    unchanged = await amass.post(refresh)
    assert unchanged.json() == {"retracted": False, "newly_invalidated": False}

    amass.fake.records["AMBC_1"]["isRetracted"] = True
    flagged = await amass.post(refresh)
    assert flagged.json() == {"retracted": True, "newly_invalidated": True}
    assert (await amass.get(f"/api/sources/{source['id']}/validity")).json()[
        "status"
    ] == "invalidated"

    repeat = await amass.post(refresh)
    assert repeat.json() == {"retracted": True, "newly_invalidated": False}


async def test_refresh_refuses_a_source_that_did_not_come_from_amass(amass) -> None:
    upload = await amass.post(
        f"/api/workspaces/{amass.workspace}/sources",
        files={"file": ("notes.md", b"Plain notes about the assay.", "text/markdown")},
    )
    response = await amass.post(f"/api/sources/{upload.json()['id']}/amass/refresh")
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (AmassError(404, "NOT_FOUND", "No such record"), 404),
        (AmassError(401, "UNAUTHORIZED", "bad key"), 502),
        (AmassError(429, "TOO_MANY_REQUESTS", "slow down", retry_after=30), 429),
        (AmassError(500, "INTERNAL_SERVER_ERROR", "oops"), 502),
    ],
)
async def test_amass_failures_map_to_clear_responses_and_import_nothing(
    amass, error, status
) -> None:
    amass.fake.error = error
    response = await amass.post(import_url(amass.workspace), json={"amass_id": "AMBC_1"})

    assert response.status_code == status
    if status == 429:
        assert response.headers["Retry-After"] == "30"
    if status == 502 and error.status == 401:
        assert "API key" in response.json()["detail"] and "amass_test" not in response.text
    graph = await amass.get(f"/api/workspaces/{amass.workspace}/graph")
    assert graph.status_code == 200


async def test_an_unknown_pmid_is_a_404(amass) -> None:
    response = await amass.post(import_url(amass.workspace), json={"pmid": "424242"})
    assert response.status_code == 404 and "424242" in response.json()["detail"]


@pytest.mark.parametrize(
    "body",
    [{}, {"amass_id": "AMBC_1", "pmid": "111"}, {"amass_id": "nope"}, {"pmid": "abc"}],
)
async def test_import_needs_exactly_one_valid_identifier(amass, body) -> None:
    response = await amass.post(import_url(amass.workspace), json=body)
    assert response.status_code == 422


async def test_a_record_with_nothing_to_read_is_rejected(amass) -> None:
    amass.fake.records["AMBC_3"] = record(
        "AMBC_3", pmid="333", doi="10.1000/x3", title=None, abstract=None
    )
    response = await amass.post(
        import_url(amass.workspace), json={"amass_id": "AMBC_3", "include_fulltext": False}
    )
    assert response.status_code == 422


async def test_without_an_api_key_the_endpoints_say_so(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "amass_api_key", None)
    await engine.dispose()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/integrations/amass/search", json={"query": "anything"})
    assert response.status_code == 503 and "AMASS_API_KEY" in response.json()["detail"]
    await engine.dispose()
