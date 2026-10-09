import uuid

import httpx
import pytest

from app.agent.runs import EventLog
from app.config import get_settings
from app.database import engine, session_factory
from app.main import app
from app.models import AgentRun, ResearchGoal


@pytest.fixture
async def api(monkeypatch):
    monkeypatch.setattr(get_settings(), "claude_api_key", "test-key")
    await engine.dispose()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        client.workspace = (
            await client.post("/api/workspaces", json={"title": "agent api"})
        ).json()["id"]
        yield client
        await client.delete(f"/api/workspaces/{client.workspace}")
    await engine.dispose()


def start_url(api) -> str:
    return f"/api/workspaces/{api.workspace}/agent-runs"


def body(**overrides) -> dict:
    return {
        "idempotency_key": str(uuid.uuid4()),
        "question": "Does drug X reduce mortality in hospitalised adults?",
        "completion_criteria": ["Evidence from a human randomised trial"],
        "falsifiers": ["A larger trial showing no effect"],
        **overrides,
    }


async def test_starting_a_run_queues_it_with_its_goal_and_default_budgets(api) -> None:
    response = await api.post(start_url(api), json=body())

    assert response.status_code == 202
    run = response.json()
    assert run["question"] == body()["question"] and run["kind"] == "question"
    listed = (await api.get(start_url(api))).json()
    assert listed[0]["question"] == run["question"] and listed[0]["kind"] == "question"
    assert (run["status"], run["mode"], run["model"]) == (
        "queued",
        "guarded",
        get_settings().agent_model,
    )
    # Thorough is the default depth: more room, and the minimums the guard asks for.
    assert run["budgets"] == {
        "depth": "thorough",
        "max_turns": get_settings().agent_thorough_max_turns,
        "max_web_searches": get_settings().agent_thorough_max_web_searches,
        "min_sources": get_settings().agent_thorough_min_sources,
        "min_searches": get_settings().agent_thorough_min_searches,
        "min_web_searches": get_settings().agent_thorough_min_web_searches,
        "max_total_output_tokens": 300_000,
    }
    assert run["usage"]["turns"] == 0 and run["final_report"] is None and run["certainty"] is None


async def test_options_override_the_defaults(api) -> None:
    run = (
        await api.post(
            start_url(api),
            json=body(mode="baseline", model="claude-opus-5-5", max_turns=7, max_web_searches=0),
        )
    ).json()
    assert (run["mode"], run["model"]) == ("baseline", "claude-opus-5-5")
    assert run["budgets"]["max_turns"] == 7 and run["budgets"]["max_web_searches"] == 0


async def test_the_same_idempotency_key_returns_the_original_run(api) -> None:
    payload = body()
    first = (await api.post(start_url(api), json=payload)).json()
    again = (
        await api.post(
            start_url(api), json={**payload, "question": "A different question entirely?"}
        )
    ).json()
    assert again["id"] == first["id"] and again["goal_id"] == first["goal_id"]
    assert len((await api.get(start_url(api))).json()) == 1


@pytest.mark.parametrize(
    "bad",
    [
        {"question": "Why?"},
        {"completion_criteria": ["x"] * 9},
        {"mode": "yolo"},
        {"max_turns": 0},
        {"max_turns": 500},
        {"max_web_searches": -1},
        {"idempotency_key": ""},
    ],
)
async def test_invalid_requests_are_rejected_before_anything_is_queued(api, bad) -> None:
    assert (await api.post(start_url(api), json=body(**bad))).status_code == 422
    assert (await api.get(start_url(api))).json() == []


async def test_a_run_cannot_start_without_an_api_key(api, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "claude_api_key", None)
    response = await api.post(start_url(api), json=body())
    assert response.status_code == 503 and "CLAUDE_API_KEY" in response.json()["detail"]


async def test_unknown_workspaces_and_runs_are_404(api) -> None:
    missing = uuid.uuid4()
    assert (await api.post(f"/api/workspaces/{missing}/agent-runs", json=body())).status_code == 404
    assert (await api.get(f"/api/workspaces/{missing}/agent-runs")).status_code == 404
    for path in (f"/api/agent-runs/{missing}", f"/api/agent-runs/{missing}/events"):
        assert (await api.get(path)).status_code == 404
    assert (await api.delete(f"/api/agent-runs/{missing}")).status_code == 404


async def test_runs_are_listed_newest_first(api) -> None:
    first = (await api.post(start_url(api), json=body())).json()
    second = (await api.post(start_url(api), json=body())).json()
    listed = (await api.get(start_url(api))).json()
    assert [r["id"] for r in listed] == [second["id"], first["id"]]


async def test_events_are_returned_in_order_and_can_be_polled_by_sequence(api) -> None:
    run = (await api.post(start_url(api), json=body())).json()
    log = EventLog(session_factory, uuid.UUID(run["id"]))
    await log.start()
    for kind in ("run_started", "thinking", "tool_call", "tool_result", "check"):
        await log.add(kind, {"n": kind})

    everything = (await api.get(f"/api/agent-runs/{run['id']}/events")).json()
    assert [e["seq"] for e in everything] == [1, 2, 3, 4, 5]
    assert everything[2]["type"] == "tool_call" and everything[2]["payload"] == {"n": "tool_call"}

    newer = (await api.get(f"/api/agent-runs/{run['id']}/events", params={"after": 3})).json()
    assert [e["seq"] for e in newer] == [4, 5]
    page = (await api.get(f"/api/agent-runs/{run['id']}/events", params={"limit": 2})).json()
    assert [e["seq"] for e in page] == [1, 2]
    assert (await api.get(f"/api/agent-runs/{run['id']}/events", params={"after": 99})).json() == []


async def test_cancelling_a_queued_run_ends_it_and_is_idempotent(api) -> None:
    run = (await api.post(start_url(api), json=body())).json()
    cancelled = await api.delete(f"/api/agent-runs/{run['id']}")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["completed_at"] is not None
    assert (await api.delete(f"/api/agent-runs/{run['id']}")).json()["status"] == "cancelled"


async def test_a_finished_run_cannot_be_cancelled(api) -> None:
    run = (await api.post(start_url(api), json=body())).json()
    async with session_factory() as session:
        row = await session.get(AgentRun, uuid.UUID(run["id"]))
        row.status = "succeeded"
        await session.commit()
    assert (await api.delete(f"/api/agent-runs/{run['id']}")).json()["status"] == "succeeded"


async def test_deleting_a_workspace_removes_its_runs_and_events(api) -> None:
    run = (await api.post(start_url(api), json=body())).json()
    log = EventLog(session_factory, uuid.UUID(run["id"]))
    await log.start()
    await log.add("run_started", {})
    other = (await api.post("/api/workspaces", json={"title": "scratch"})).json()["id"]
    run2 = (await api.post(f"/api/workspaces/{other}/agent-runs", json=body())).json()
    assert (await api.delete(f"/api/workspaces/{other}")).status_code == 204
    assert (await api.get(f"/api/agent-runs/{run2['id']}")).status_code == 404
    assert (await api.get(f"/api/agent-runs/{run['id']}")).status_code == 200


async def test_the_kind_is_stored_and_must_be_known(api) -> None:
    response = await api.post(start_url(api), json=body(kind="hypothesis"))
    assert response.status_code == 202
    async with session_factory() as session:
        run = await session.get(AgentRun, uuid.UUID(response.json()["id"]))
        goal = await session.get(ResearchGoal, run.goal_id)
    assert goal.kind == "hypothesis"
    assert (await api.post(start_url(api), json=body(kind="rumour"))).status_code == 422


async def test_criteria_are_optional(api) -> None:
    response = await api.post(
        start_url(api), json=body(completion_criteria=[], falsifiers=[], kind="claim")
    )
    assert response.status_code == 202
