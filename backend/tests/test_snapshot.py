import base64
import uuid

from app.database import session_factory
from app.models import ExperimentRun
from tests.test_experiments import api, prepare, upload  # noqa: F401 - the fixture is used by name


async def test_the_snapshot_matches_the_separate_endpoints_and_answers_304_when_unchanged(api):  # noqa: F811
    source, _ = await prepare(api)
    response = await api.get(f"{api.base}/snapshot")
    assert response.status_code == 200
    snapshot = response.json()

    assert snapshot["graph"] == (await api.get(f"{api.base}/graph")).json()
    for statement in snapshot["graph"]["statements"]:
        context = (await api.get(f"{api.base}/statements/{statement['id']}/context")).json()
        assert context in snapshot["contexts"]
    excerpts = (await api.get(f"/api/sources/{source['id']}/excerpts")).json()
    assert snapshot["sources"][0]["excerpts"] == excerpts

    etag = response.headers["etag"]
    unchanged = await api.get(f"{api.base}/snapshot", headers={"If-None-Match": etag})
    assert unchanged.status_code == 304 and unchanged.content == b""

    await upload(api, "# More\n\nAnother passage.")
    changed = await api.get(f"{api.base}/snapshot", headers={"If-None-Match": etag})
    assert changed.status_code == 200 and changed.headers["etag"] != etag
    assert len(changed.json()["sources"]) == 2


async def test_overview_frames_are_served_as_images_not_inlined_in_runs(api):  # noqa: F811
    _, draft = await prepare(api)
    jpeg = b"\xff\xd8\xff fake jpeg"
    run_id = uuid.uuid4()
    async with session_factory() as session:
        session.add(
            ExperimentRun(
                id=run_id,
                workspace_id=uuid.UUID(api.workspace),
                protocol_id=uuid.UUID(draft["id"]),
                mode="video",
                status="succeeded",
                filename="clip.mp4",
                result={"overview": [{"t": 1.5, "data": base64.b64encode(jpeg).decode()}]},
            )
        )
        await session.commit()

    runs = (await api.get(f"{api.base}/experiments")).json()["runs"]
    assert runs[0]["result"]["overview"] == [{"t": 1.5}]
    frame = await api.get(f"/api/experiment-runs/{run_id}/overview/0.jpg")
    assert frame.status_code == 200 and frame.content == jpeg
    assert frame.headers["content-type"] == "image/jpeg"
    assert "immutable" in frame.headers["cache-control"]
    assert (await api.get(f"/api/experiment-runs/{run_id}/overview/1.jpg")).status_code == 404


async def test_the_experiments_list_answers_304_while_nothing_changes(api):  # noqa: F811
    await prepare(api)
    first = await api.get(f"{api.base}/experiments")
    assert first.status_code == 200
    again = await api.get(
        f"{api.base}/experiments", headers={"If-None-Match": first.headers["etag"]}
    )
    assert again.status_code == 304
