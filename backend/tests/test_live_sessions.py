import uuid

import cv2
import numpy as np

from app.config import get_settings
from app.models import ExperimentRun
from app.services import experiments
from app.services.experiments import numbered_protocol, process_experiment_run
from tests.test_experiments import api, prepare  # noqa: F401 - the fixture is used by name


def jpeg() -> bytes:
    ok, buffer = cv2.imencode(".jpg", np.full((32, 32, 3), 127, dtype=np.uint8))
    assert ok
    return buffer.tobytes()


class FakeLLM:
    """Reports the first step as performed in every window it is shown."""

    model = "test-live"
    usage = {"requests": 1, "input_tokens": 0, "output_tokens": 0}

    def __init__(self, *args, **kwargs) -> None:
        pass

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
                        "notes": "Seen in the stream.",
                    }
                ],
                "unexpected_events": [],
            }
        )


def test_a_live_stream_is_analysed_window_by_window(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path))
    monkeypatch.setattr(experiments, "make_llm", FakeLLM)
    run = ExperimentRun(
        id=uuid.uuid4(), workspace_id=uuid.uuid4(), mode="live", filename="Live session"
    )
    directory = experiments.live_directory(run)
    (directory / "frames").mkdir(parents=True)
    for second in range(0, 25, 2):
        (directory / "frames" / f"{second * 1000:09d}.jpg").write_bytes(jpeg())
    (directory / "ended").touch()

    progress = []
    protocol = numbered_protocol("1. Inspect the sample.\n\n2. Close the tube.", "p", "test")
    result = experiments.execute_protocol(protocol, run, progress.append)

    assert result["summary"]["windows"] == 3  # 10-second windows over 24 seconds
    assert result["observations"][0]["step_id"] == "s1"
    assert result["duration"] == 24
    # The dashboard sees findings as they happen, not only at the end.
    live = [p["live"] for p in progress if "live" in p]
    assert live and live[0]["observations"][0]["step_id"] == "s1"
    assert any("processed_seconds" in p for p in progress)


async def test_a_phone_streams_records_and_stops_a_live_session(api, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path))
    monkeypatch.setattr(get_settings(), "claude_api_key", "test-key")
    monkeypatch.setattr(experiments, "make_llm", FakeLLM)
    _, draft = await prepare(api)
    response = await api.post(
        f"{api.base}/experiment-runs", data={"protocol_id": draft["id"], "mode": "live"}
    )
    assert response.status_code == 202, response.text
    run = response.json()
    assert run["mode"] == "live" and run["result"]["live"] == {"observations": [], "deviations": []}
    base = f"/api/experiment-runs/{run['id']}"

    sent = await api.post(
        f"{base}/frames",
        data={"timestamps": ["0", "4.5", "12"]},
        files=[("frames", (f"{i}.jpg", jpeg(), "image/jpeg")) for i in range(3)],
    )
    assert sent.status_code == 200 and sent.json()["received"] == 3

    webm = {"Content-Type": "video/webm;codecs=vp8"}
    assert (await api.post(f"{base}/recording?index=0", content=b"one", headers=webm)).json() == {
        "stored": 1
    }
    # A retried piece is ignored; a gap is refused rather than corrupting the file.
    assert (await api.post(f"{base}/recording?index=0", content=b"one", headers=webm)).json() == {
        "stored": 1
    }
    assert (
        await api.post(f"{base}/recording?index=2", content=b"three", headers=webm)
    ).status_code == 409
    assert (await api.post(f"{base}/recording?index=1", content=b"two", headers=webm)).json() == {
        "stored": 2
    }
    assert (
        await api.post(
            f"{base}/recording?index=2", content=b"x", headers={"Content-Type": "text/plain"}
        )
    ).status_code == 415

    assert (await api.post(f"{base}/stop")).status_code == 200
    late = await api.post(
        f"{base}/frames", data={"timestamps": ["20"]}, files=[("frames", ("x.jpg", jpeg()))]
    )
    assert late.status_code == 409

    await process_experiment_run(uuid.UUID(run["id"]))
    finished = (await api.get(base)).json()
    assert finished["status"] == "succeeded", finished
    assert finished["filename"] == "Live session.webm"
    assert finished["result"]["observations"][0]["step_id"] == "s1"
    recording = await api.get(f"{base}/recording")
    assert recording.status_code == 200 and recording.content == b"onetwo"
    source = (await api.get(f"/api/sources/{finished['result']['source_id']}")).json()
    assert source["title"].startswith("Live session:") and source["metadata"]["mode"] == "live"


async def test_live_sessions_need_the_vision_model(api, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "claude_api_key", None)
    _, draft = await prepare(api)
    response = await api.post(
        f"{api.base}/experiment-runs", data={"protocol_id": draft["id"], "mode": "live"}
    )
    assert response.status_code == 503
