import uuid

import httpx
import pytest

from app.config import get_settings
from app.database import engine
from app.main import app
from tests.pdf_factory import make_pdf

PDF_PAGES = [
    ["Introduction", "Mouse models of the disease respond to treatment with compound X."],
    ["Results", "The compound reduced tumour volume by half in treated animals."],
]


@pytest.fixture
async def api(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path))
    # The app's pool is created at import time and bound to one event loop; each test has its own.
    await engine.dispose()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post("/api/workspaces", json={"title": "upload tests"})
        client.workspace = created.json()["id"]
        yield client
        await client.delete(f"/api/workspaces/{client.workspace}")
    await engine.dispose()


async def upload(api, name: str, content: bytes, content_type: str):
    return await api.post(
        f"/api/workspaces/{api.workspace}/sources",
        files={"file": (name, content, content_type)},
    )


async def test_workspace_sources_are_discovered_and_isolated(api) -> None:
    source = (await upload(api, "evidence.md", b"Evidence from a study.", "text/markdown")).json()
    other = (await api.post("/api/workspaces", json={"title": "other sources"})).json()["id"]
    try:
        listed = await api.get(f"/api/workspaces/{api.workspace}/sources")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()] == [source["id"]]
        assert listed.json()[0]["workspace_id"] == api.workspace
        assert (await api.get(f"/api/workspaces/{other}/sources")).json() == []
    finally:
        await api.delete(f"/api/workspaces/{other}")


async def test_listing_sources_requires_an_existing_workspace(api) -> None:
    assert (await api.get(f"/api/workspaces/{uuid.uuid4()}/sources")).status_code == 404


async def test_pdf_upload_creates_a_source_with_located_excerpts(api, tmp_path) -> None:
    response = await upload(api, "study.pdf", make_pdf(PDF_PAGES), "application/pdf")

    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "document" and body["mime_type"] == "application/pdf"
    assert body["metadata"]["parser"] == "pdf_pypdf_v1" and body["metadata"]["page_count"] == 2
    assert [e["locator"]["page"] for e in body["excerpts"]] == [1, 2]
    assert [e["locator"]["section"] for e in body["excerpts"]] == ["Introduction", "Results"]
    assert body["excerpts"][0]["text"].startswith("Mouse models")
    stored = list(tmp_path.rglob("study.pdf"))
    assert len(stored) == 1 and stored[0].read_bytes().startswith(b"%PDF")  # original kept

    listed = await api.get(f"/api/sources/{body['id']}/excerpts")
    assert [e["id"] for e in listed.json()] == [e["id"] for e in body["excerpts"]]


async def test_pdf_is_recognised_by_extension_when_the_type_is_generic(api) -> None:
    response = await upload(api, "study.PDF", make_pdf(PDF_PAGES), "application/octet-stream")
    assert response.status_code == 201


async def test_uploading_the_same_pdf_twice_is_a_conflict_naming_the_original(api) -> None:
    first = await upload(api, "a.pdf", make_pdf(PDF_PAGES), "application/pdf")
    second = await upload(api, "b.pdf", make_pdf(PDF_PAGES), "application/pdf")

    assert second.status_code == 409
    assert second.json()["detail"]["source_id"] == first.json()["id"]


@pytest.mark.parametrize(
    ("content", "status", "fragment"),
    [
        (make_pdf([[]]), 422, "OCR"),
        (b"not really a pdf", 422, "not a readable PDF"),
    ],
)
async def test_unusable_pdfs_are_rejected_with_a_reason(api, content, status, fragment) -> None:
    response = await upload(api, "bad.pdf", content, "application/pdf")
    assert response.status_code == status and fragment in response.json()["detail"]


async def test_text_uploads_still_work(api) -> None:
    response = await upload(
        api, "notes.md", b"# Findings\n\nThe assay was reproducible.", "text/markdown"
    )
    assert response.status_code == 201
    assert response.json()["metadata"]["parser"] == "structured_text_v1"
    assert response.json()["excerpts"][1]["locator"]["section"] == "Findings"


async def test_other_file_types_are_still_refused(api) -> None:
    response = await upload(api, "data.csv", b"a,b\n1,2", "text/csv")
    assert response.status_code == 415 and "PDF" in response.json()["detail"]


async def test_non_utf8_text_is_rejected(api) -> None:
    response = await upload(api, "latin.txt", "café".encode("latin-1"), "text/plain")
    assert response.status_code == 422


async def test_text_with_nul_characters_is_rejected_not_a_server_error(api) -> None:
    response = await upload(api, "binary.txt", b"Results\x00 were measured.", "text/plain")
    assert response.status_code == 422 and "NUL" in response.json()["detail"]
