import asyncio
import uuid

import httpx
import pytest
from lab_vision.models import Observation

from app.database import engine
from app.main import app
from app.models import Excerpt, Source
from app.services.experiments import (
    demo_observations,
    extract_protocol,
    extract_source_protocol,
    numbered_protocol,
    process_experiment_run,
    verify_observations,
)

METHODS = (
    "# Methods\n\n1. Set the pipette to 50 uL and transfer water into the sample tube.\n\n"
    "2. Close and invert the tube.\n\n3. Place the tube in the instrument set to 25 °C.\n"
)


def test_video_bridge_runs_the_existing_frame_pipeline(tmp_path, monkeypatch):
    import cv2
    import numpy as np

    from app.config import get_settings
    from app.models import ExperimentRun
    from app.services import experiments

    path = tmp_path / "recording.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 5, (64, 64))
    for _ in range(10):
        writer.write(np.full((64, 64, 3), 127, dtype=np.uint8))
    writer.release()

    class FakeLLM:
        model = "test-vision"
        usage = {"requests": 1, "input_tokens": 0, "output_tokens": 0}

        def generate(self, *, system, content, output):
            assert any(item["type"] == "image" for item in content)
            return output.model_validate(
                {
                    "observations": [
                        {
                            "step_id": "s1",
                            "status": "performed",
                            "confidence": 0.9,
                            "values": [],
                            "notes": "Step visible in the frames.",
                        }
                    ],
                    "unexpected_events": [],
                }
            )

    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path))
    monkeypatch.setattr(get_settings(), "vision_strategy", "windows")
    monkeypatch.setattr(experiments, "make_llm", FakeLLM)
    protocol = numbered_protocol("1. Inspect the sample.", "p", "test")
    run = ExperimentRun(id=uuid.uuid4(), mode="video", storage_key=path.name, filename=path.name)
    progress = []
    result = experiments.execute_protocol(protocol, run, progress.append)
    assert progress and result["summary"]["failed_windows"] == 0
    assert len(result["observations"]) == 1 and result["deviations"] == []
    assert result["observations"][0]["produced_by"]["model"] == "test-vision"


def test_numbered_steps_keep_the_source_order_and_numeric_checks():
    protocol = numbered_protocol(METHODS.split("\n\n", 1)[1], "p", "test")
    assert [s.id for s in protocol.steps] == ["s1", "s2", "s3"]
    assert (
        protocol.steps[0].source_text
        == "Set the pipette to 50 uL and transfer water into the sample tube."
    )
    assert protocol.steps[0].checks[0].expected == 50
    assert protocol.steps[2].checks[0].expected == 25
    assert not protocol.steps[1].checks


def test_missing_or_nonconsecutive_methodology_is_not_invented():
    with pytest.raises(ValueError, match="No methodology"):
        extract_protocol(
            [Excerpt(id=uuid.uuid4(), text="A result.", locator={"section": "Results"})],
            "p",
            "test",
        )
    with pytest.raises(ValueError, match="consecutive"):
        numbered_protocol("1. First.\n\n3. Third.", "p", "test")


def test_uploaded_pdf_with_restarting_phases_uses_original_file_and_existing_citations(
    tmp_path, monkeypatch
):
    from app.config import get_settings
    from tests.pdf_factory import make_pdf

    content = make_pdf(
        [
            ["Protocol", "1. Preparation of the Gel", "1. Weigh the agarose.", "2. Add buffer."],
            [
                "3. Pour the gel.",
                "2. Separation of DNA Fragments",
                "1. Load samples.",
                "2. Run the gel.",
                "3. Observing DNA fragments",
                "1. Photograph the gel.",
                "4. Representative Results",
                "DNA fragments appear as distinct fluorescent bands.",
            ],
        ]
    )
    (tmp_path / "protocol.pdf").write_bytes(content)
    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path))
    source = Source(mime_type="application/pdf", storage_key="protocol.pdf")
    # Existing research ingestion combined steps and mistook a protocol phase
    # for a new non-method section. Preparing an experiment must still work.
    old_excerpts = [
        Excerpt(id=uuid.uuid4(), text="1. Preparation of the Gel", locator={"section": "Protocol"}),
        Excerpt(
            id=uuid.uuid4(),
            text="1. Weigh the agarose. 2. Add buffer. 3. Pour the gel.",
            locator={"section": "Protocol"},
        ),
        Excerpt(
            id=uuid.uuid4(),
            text="2. Separation of DNA Fragments 1. Load samples. 2. Run the gel.",
            locator={"section": "Protocol"},
        ),
        Excerpt(
            id=uuid.uuid4(),
            text="1. Photograph the gel.",
            locator={"section": "Observing DNA fragments"},
        ),
    ]
    protocol, method, citations = extract_source_protocol(source, old_excerpts, "p", "Gel")
    assert method == "numbered_instructions"
    assert [s.id for s in protocol.steps] == [f"s{i}" for i in range(1, 7)]
    assert [s.source_text for s in protocol.steps] == [
        "Weigh the agarose.",
        "Add buffer.",
        "Pour the gel.",
        "Load samples.",
        "Run the gel.",
        "Photograph the gel.",
    ]
    assert citations["s6"] == [str(old_excerpts[-1].id)]
    assert all(citations.values())


def test_protocol_step_can_cite_multiple_excerpt_chunks():
    excerpts = [
        Excerpt(id=uuid.uuid4(), text="1. Set the instrument", locator={"section": "Methods"}),
        Excerpt(id=uuid.uuid4(), text="to 25 °C.", locator={"section": "Methods"}),
    ]
    protocol, _, citations = extract_protocol(excerpts, "p", "test")
    assert len(protocol.steps) == 1 and protocol.steps[0].checks[0].expected == 25
    assert citations["s1"] == [str(e.id) for e in excerpts]


def test_synthetic_observations_are_judged_by_the_real_verifier():
    protocol = numbered_protocol(METHODS.split("\n\n", 1)[1], "p", "test")
    result = verify_observations(protocol, demo_observations(protocol, "r"), "r")
    assert {d["kind"] for d in result["deviations"]} == {"wrong_value", "skipped_step"}
    assert all(not d["needs_review"] for d in result["deviations"])


def test_unknown_identifiers_and_out_of_order_replays_are_rejected():
    protocol = numbered_protocol("1. First.\n\n2. Second.", "p", "test")
    observations = demo_observations(protocol, "r")
    with pytest.raises(ValueError, match="timestamp"):
        verify_observations(protocol, observations[::-1], "r")
    bad = Observation.model_validate({**observations[0].model_dump(), "step_id": "invented"})
    with pytest.raises(ValueError, match="unknown step"):
        verify_observations(protocol, [bad], "r")


@pytest.fixture
async def api():
    await engine.dispose()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        workspace = (
            await client.post("/api/workspaces", json={"title": "experiment integration test"})
        ).json()
        client.workspace = workspace["id"]
        client.base = f"/api/workspaces/{client.workspace}"
        yield client
        await client.delete(client.base)
    await engine.dispose()


async def upload(api, text=METHODS):
    response = await api.post(
        f"{api.base}/sources", files={"file": ("protocol.md", text, "text/markdown")}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def prepare(api):
    source = await upload(api)
    assert (await api.post(f"{api.base}/verify")).status_code == 200
    response = await api.post(f"{api.base}/protocols", json={"source_id": source["id"]})
    assert response.status_code == 201, response.text
    return source, response.json()


async def test_verification_and_protocol_review_are_optional(api):
    source = await upload(api)
    response = await api.post(f"{api.base}/protocols", json={"source_id": source["id"]})
    assert response.status_code == 201, response.text
    assert not (await api.get(f"{api.base}/experiments")).json()["verified"]
    draft = response.json()
    assert draft["approved_at"] is None
    assert set(draft["step_excerpts"]) == {"s1", "s2", "s3"}
    assert all(ids for ids in draft["step_excerpts"].values())
    response = await api.post(
        f"{api.base}/experiment-runs", data={"protocol_id": draft["id"], "mode": "demo"}
    )
    assert response.status_code == 202
    response = await api.post(f"{api.base}/protocols/{draft['id']}/approve")
    assert response.status_code == 200 and response.json()["approved_at"]


async def test_research_changes_invalidate_verification_and_old_protocols(api):
    _, draft = await prepare(api)
    await api.post(f"{api.base}/protocols/{draft['id']}/approve")
    await upload(api, "# More results\n\nNew evidence changes the research.")
    data = (await api.get(f"{api.base}/experiments")).json()
    assert not data["verified"] and not data["protocols"][0]["current"]
    response = await api.post(
        f"{api.base}/experiment-runs", data={"protocol_id": draft["id"], "mode": "demo"}
    )
    assert response.status_code == 202, response.text
    assert response.json()["protocol_id"] != draft["id"]
    response = await api.post(f"{api.base}/protocols/{draft['id']}/approve")
    assert response.status_code == 409


async def test_experiment_waits_for_extraction_without_requiring_verification(api):
    from app.database import session_factory
    from app.models import ExtractionJob

    source = await upload(api)
    async with session_factory() as session:
        session.add(ExtractionJob(
            workspace_id=uuid.UUID(api.workspace), source_id=uuid.UUID(source["id"]),
            idempotency_key=str(uuid.uuid4()), model="test", status="running",
        ))
        await session.commit()
    response = await api.post(
        f"{api.base}/experiment-runs", data={"source_id": source["id"], "mode": "demo"}
    )
    assert response.status_code == 409
    assert "extraction" in response.json()["detail"]
    assert "Verify" not in response.json()["detail"]


async def test_protocols_cannot_cross_workspace_boundaries(api):
    _, draft = await prepare(api)
    other = (await api.post("/api/workspaces", json={"title": "other"})).json()["id"]
    try:
        await api.post(f"/api/workspaces/{other}/verify")
        response = await api.post(f"/api/workspaces/{other}/protocols/{draft['id']}/approve")
        assert response.status_code == 404
    finally:
        await api.delete(f"/api/workspaces/{other}")


async def test_invalidated_sources_cannot_be_used_as_protocols(api):
    source = await upload(api)
    await api.post(
        f"/api/sources/{source['id']}/validity",
        json={
            "status": "invalidated",
            "reason": "Withdrawn",
            "idempotency_key": str(uuid.uuid4()),
            "provenance": {"actor_type": "user", "actor_id": "test"},
        },
    )
    await api.post(f"{api.base}/verify")
    response = await api.post(f"{api.base}/protocols", json={"source_id": source["id"]})
    assert response.status_code == 409


async def test_demo_job_persists_results_and_temporal_evidence(api):
    _, draft = await prepare(api)
    await api.post(f"{api.base}/protocols/{draft['id']}/approve")
    response = await api.post(
        f"{api.base}/experiment-runs", data={"protocol_id": draft["id"], "mode": "demo"}
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    # Execute the same durable job processor used by the worker.
    await process_experiment_run(uuid.UUID(run_id))
    for _ in range(100):
        state = (await api.get(f"{api.base}/experiments")).json()
        run = next(r for r in state["runs"] if r["id"] == run_id)
        if run["status"] in {"succeeded", "failed"}:
            break
        await asyncio.sleep(0.1)
    assert run["status"] == "succeeded", run
    assert state["verified"] and state["protocols"][0]["current"]
    assert {d["kind"] for d in run["result"]["deviations"]} == {"wrong_value", "skipped_step"}
    source_id = run["result"]["source_id"]
    source = (await api.get(f"/api/sources/{source_id}")).json()
    assert source["origin"] == "lab-vision" and source["metadata"]["mode"] == "demo"
    excerpts = (await api.get(f"/api/sources/{source_id}/excerpts")).json()
    assert excerpts and excerpts[0]["locator"]["kind"] == "time_range"
    graph = (await api.get(f"{api.base}/graph")).json()
    assert all(s["assertion_mode"] == "reported" for s in graph["statements"])


async def test_empty_and_unsupported_recordings_are_rejected(api):
    _, draft = await prepare(api)
    await api.post(f"{api.base}/protocols/{draft['id']}/approve")
    data = {"protocol_id": draft["id"], "mode": "replay"}
    assert (await api.post(f"{api.base}/experiment-runs", data=data)).status_code == 422
    assert (
        await api.post(
            f"{api.base}/experiment-runs", data=data, files={"file": ("bad.exe", b"bad")}
        )
    ).status_code == 415
    assert (
        await api.post(
            f"{api.base}/experiment-runs", data=data, files={"file": ("empty.jsonl", b"")}
        )
    ).status_code == 422


async def test_start_automatically_extracts_methodology_without_verification(api):
    source = await upload(api)
    data = {"source_id": source["id"], "mode": "demo"}
    response = await api.post(f"{api.base}/experiment-runs", data=data)
    assert response.status_code == 202, response.text
    experiments = (await api.get(f"{api.base}/experiments")).json()
    assert len(experiments["protocols"]) == 1
    assert experiments["protocols"][0]["approved_at"] is None
    assert response.json()["protocol_id"] == experiments["protocols"][0]["id"]


def test_partial_recording_does_not_report_later_steps_as_skipped():
    protocol = numbered_protocol(
        "1. Spray hands with 70% ethanol.\n2. Get all reagents.\n"
        "3. Place the sample in the cabinet.",
        "p",
        "test",
    )
    first = Observation(
        step_id="s1",
        status="performed",
        confidence=0.9,
        span={"start_s": 0, "end_s": 6},
        values=[],
        produced_by={"actor_type": "integration", "actor_id": "test"},
    )
    result = verify_observations(protocol, [first], "r", complete_recording=False)
    assert {d["kind"] for d in result["deviations"]} == {"unverified_check"}
    assert result["summary"]["needs_review"] == 1
    assert all(d["step_id"] == "s1" for d in result["deviations"])


async def test_report_links_are_persisted_and_grounded_in_the_run(api):
    import copy

    from app.services.experiment_records import canonical_hash

    source, draft = await prepare(api)
    response = await api.post(
        f"{api.base}/experiment-runs", data={"protocol_id": draft["id"], "mode": "demo"}
    )
    run_id = response.json()["id"]
    await process_experiment_run(uuid.UUID(run_id))
    run = (await api.get(f"{api.base}/experiments")).json()["runs"][0]
    assert run["status"] == "succeeded"
    method = {
        "schemaVersion": 1,
        "title": "Test",
        "version": "1",
        "source": source["id"],
        "requirements": [
            {"id": step["id"], "description": step["description"], "quote": step["source_text"]}
            for step in draft["protocol"]["steps"]
        ],
    }
    observations = [
        {
            "id": o["id"],
            "stepId": o["step_id"],
            "timestampStart": o["span"]["start_s"],
            "timestampEnd": o["span"]["end_s"],
            "confidence": o["confidence"],
        }
        for o in run["result"]["observations"]
    ]
    record = {
        "schemaVersion": 2,
        "experimentId": run_id,
        "generatedAt": "2026-10-04T00:00:00Z",
        "method": method,
        "methodHash": canonical_hash(method),
        "run": {"id": run_id, "observations": observations},
        "observationHash": canonical_hash(observations),
        "results": {
            "s1": {"status": "contradicted"},
            "s2": {"status": "contradicted"},
            "s3": {"status": "verified"},
        },
    }
    record["recordHash"] = canonical_hash(record)
    published = await api.post("/api/records", json=record)
    assert published.status_code == 201, published.text
    key = published.json()["path"].rsplit("/", 1)[1]
    assert (await api.get(f"/api/records/{key}")).json() == record
    assert (await api.get(f"/api/experiment-runs/{run_id}/records")).json() == [record]
    assert (await api.post("/api/records", json=record)).status_code == 201
    assert len((await api.get(f"/api/experiment-runs/{run_id}/records")).json()) == 1
    tampered = copy.deepcopy(record)
    tampered["method"]["requirements"][0]["quote"] = "Fabricated source quote"
    tampered["methodHash"] = canonical_hash(tampered["method"])
    tampered["recordHash"] = canonical_hash(
        {k: v for k, v in tampered.items() if k != "recordHash"}
    )
    response = await api.post("/api/records", json=tampered)
    assert response.status_code == 422 and "source passages" in response.text
    tampered = copy.deepcopy(record)
    tampered["results"]["s1"]["status"] = "verified"
    tampered["recordHash"] = canonical_hash(
        {k: v for k, v in tampered.items() if k != "recordHash"}
    )
    response = await api.post("/api/records", json=tampered)
    assert response.status_code == 422 and "cannot be reported as verified" in response.text


def test_video_agent_reports_retain_the_persisted_checks_and_verdicts():
    import copy

    from app.models import ExperimentProtocol, ExperimentRun
    from app.services.experiment_records import canonical_hash, validate_record

    run_id = uuid.uuid4()
    method = {
        "schemaVersion": 1,
        "requirements": [
            {
                "id": "s1",
                "quote": "Invert the tube.",
                "description": "Invert the tube.",
                "checks": ["Tube is inverted"],
            }
        ],
    }
    observation = {
        "id": "o",
        "stepId": "s1",
        "confidence": 0.9,
        "checkResults": [
            {"check": "Tube is inverted", "result": "confirmed", "note": "Visible at 1 s."}
        ],
    }
    verdicts = {"s1": {"status": "verified", "confidence": 0.9, "evidence": []}}
    raw = [{"id": "o", "step_id": "s1", "confidence": 0.9}]
    run = ExperimentRun(
        id=run_id,
        result={
            "agent_method": method,
            "agent_observations": [observation],
            "agent_results": verdicts,
            "observations": raw,
        },
    )
    protocol = ExperimentProtocol(protocol={"steps": [{"id": "s1"}]})
    record = {
        "schemaVersion": 2,
        "experimentId": str(run_id),
        "method": method,
        "methodHash": canonical_hash(method),
        "run": {"id": str(run_id), "observations": [observation]},
        "observationHash": canonical_hash([observation]),
        "results": verdicts,
        "rawObservations": raw,
    }
    record["recordHash"] = canonical_hash(record)
    validate_record(record, run, protocol)
    altered = copy.deepcopy(record)
    altered["results"]["s1"]["status"] = "contradicted"
    altered["recordHash"] = canonical_hash({k: v for k, v in altered.items() if k != "recordHash"})
    with pytest.raises(ValueError, match="persisted video findings"):
        validate_record(altered, run, protocol)
