"""The uncertainty bar: a named level that follows the verifier, and endpoints for a UI."""

import json
import uuid

import httpx
import pytest
from sqlalchemy import select

from app.agent import assurance
from app.agent.guardrail import Obligation
from app.agent.loop import execute_run
from app.config import get_settings
from app.database import engine, session_factory
from app.main import app
from app.models import AgentEvent, AgentRun, JudgeVerdict
from tests.agent_helpers import FakeClaude, FakeJudge, claim_args, make_world, reply, tool

SOURCES = {"trial": ["Drug X reduced 28-day mortality by 30% in a randomised trial."]}


def obligation(kind: str, severity: str = "critical") -> Obligation:
    return Obligation(
        kind=kind, severity=severity, description=f"{kind} text", required_condition=""
    )


# -- the scale ---------------------------------------------------------------------------------


def test_the_scale_is_ordered_named_and_free_of_numbers() -> None:
    assert assurance.SCALE[0] == "unexplored" and assurance.SCALE[-1] == "settled"
    assert set(assurance.LABELS) == set(assurance.SCALE)
    packet = assurance.from_check([]).as_dict()
    assert not any(isinstance(v, (int, float)) for v in packet.values())
    assert packet["scale"] == assurance.SCALE


def test_no_critical_obligation_is_well_supported_and_only_an_accepted_answer_is_settled() -> None:
    advisory = [obligation("possible_conflict", "advisory")]
    assert assurance.from_check(advisory).level == "well_supported"
    assert assurance.from_check(advisory, established=True).level == "settled"


@pytest.mark.parametrize("kind", ["unmet_criteria", "missing_premise", "judge_unavailable"])
def test_evidence_gaps_leave_the_answer_provisional(kind) -> None:
    result = assurance.from_check([obligation(kind)])
    assert result.level == "provisional"
    assert result.holding_back == [{"kind": kind, "description": f"{kind} text"}]


@pytest.mark.parametrize("kind", sorted(assurance.UNSOUND))
def test_a_flaw_in_the_argument_is_contested_even_beside_gaps(kind) -> None:
    assert (
        assurance.from_check([obligation("unmet_criteria"), obligation(kind)]).level == "contested"
    )


def test_the_reasons_are_capped_and_shortened() -> None:
    many = [obligation("unmet_criteria") for _ in range(9)]
    many[0].description = "x" * 1000
    held = assurance.from_check(many).holding_back
    assert len(held) == assurance.MAX_REASONS and len(held[0]["description"]) == 220


# -- over a run --------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def fresh_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path))
    monkeypatch.setattr(get_settings(), "claude_api_key", None)
    monkeypatch.setattr("app.agent.loop.Judge", FakeJudge)
    await engine.dispose()
    yield
    await engine.dispose()


def last_result(request) -> dict:
    block = [b for b in request["messages"][-1]["content"] if b["type"] == "tool_result"][0]
    return json.loads(block["content"])


async def events(world, kind) -> list[AgentEvent]:
    async with session_factory() as session:
        return list(
            await session.scalars(
                select(AgentEvent)
                .where(AgentEvent.run_id == world.run_id, AgentEvent.type == kind)
                .order_by(AgentEvent.seq)
            )
        )


async def test_the_level_rises_as_the_agent_earns_it() -> None:
    world = await make_world(criteria=["A randomised trial"], sources=SOURCES)
    claim = claim_args(
        world, "Drug X reduced mortality.", "trial", role="conclusion", criteria_satisfied=[0]
    )
    seen = {}

    def check(request):
        seen["id"] = last_result(request)["statement_id"]
        return reply(tool("check_conclusion", statement_id=seen["id"]), stop="tool_use")

    def finalize(request):
        return reply(
            tool("finalize_conclusion", statement_id=seen["id"], certainty="established"),
            stop="tool_use",
        )

    client = FakeClaude(
        reply(tool("record_claim", **claim), stop="tool_use"),
        check,
        finalize,
        reply(stop="end_turn"),
    )
    await execute_run(world.run_id, client=client)
    levels = [e.payload["level"] for e in await events(world, "assurance")]
    assert levels == ["unexplored", "exploring", "well_supported", "settled"]
    async with session_factory() as session:
        run = await session.get(AgentRun, world.run_id)
    assert run.usage["assurance"]["level"] == "settled" and run.certainty == "established"


async def test_a_flawed_check_shows_what_holds_the_answer_back() -> None:
    world = await make_world(criteria=["A randomised trial"], sources=SOURCES)
    claim = claim_args(world, "Drug X reduced mortality.", "trial", role="conclusion")
    client = FakeClaude(
        reply(tool("record_claim", **claim), stop="tool_use"),
        lambda r: reply(
            tool("check_conclusion", statement_id=last_result(r)["statement_id"]), stop="tool_use"
        ),
        reply(stop="end_turn"),
        reply(stop="end_turn"),
        reply(stop="end_turn"),
    )
    await execute_run(world.run_id, client=client)
    shown = [e.payload for e in await events(world, "assurance")]
    assert [p["level"] for p in shown][:3] == ["unexplored", "exploring", "provisional"]
    assert shown[2]["holding_back"][0]["kind"] == "unmet_criteria"


async def test_a_baseline_run_has_no_assurance() -> None:
    world = await make_world(mode="baseline")
    await execute_run(world.run_id, client=FakeClaude(reply(stop="end_turn")))
    assert await events(world, "assurance") == []
    async with session_factory() as session:
        run = await session.get(AgentRun, world.run_id)
    assert run.usage["assurance"] is None


# -- endpoints ---------------------------------------------------------------------------------


@pytest.fixture
async def api(monkeypatch):
    monkeypatch.setattr(get_settings(), "claude_api_key", "test-key")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        client.workspace = (await client.post("/api/workspaces", json={"title": "a"})).json()["id"]
        yield client
        await client.delete(f"/api/workspaces/{client.workspace}")


async def test_a_run_carries_its_goal_everywhere_it_is_returned(api) -> None:
    body = {
        "idempotency_key": str(uuid.uuid4()),
        "question": "Does drug X work in adults?",
        "kind": "claim",
        "completion_criteria": ["A randomised trial"],
        "falsifiers": ["A larger null trial"],
    }
    started = (await api.post(f"/api/workspaces/{api.workspace}/agent-runs", json=body)).json()
    goal = {
        "id": started["goal"]["id"],
        "question": "Does drug X work in adults?",
        "kind": "claim",
        "completion_criteria": ["A randomised trial"],
        "falsifiers": ["A larger null trial"],
        "status": "open",
    }
    assert started["goal"] == goal
    fetched = (await api.get(f"/api/agent-runs/{started['id']}")).json()
    listed = (await api.get(f"/api/workspaces/{api.workspace}/agent-runs")).json()
    cancelled = (await api.delete(f"/api/agent-runs/{started['id']}")).json()
    assert fetched["goal"] == goal and listed[0]["goal"] == goal and cancelled["goal"] == goal


async def test_verdicts_pair_each_criterion_with_its_text_and_reason(api) -> None:
    run = (
        await api.post(
            f"/api/workspaces/{api.workspace}/agent-runs",
            json={"idempotency_key": str(uuid.uuid4()), "question": "Does drug X work?"},
        )
    ).json()
    async with session_factory() as session:
        session.add(
            JudgeVerdict(
                run_id=uuid.UUID(run["id"]),
                input_hash="h1",
                material={
                    "criteria": ["A randomised trial", "Opposing evidence"],
                    "searches": ["q"],
                },
                verdict={
                    "criteria": [
                        {
                            "index": 1,
                            "met": False,
                            "rationale": "Only supportive searches.",
                            "supporting_statement_ids": [],
                        }
                    ],
                    "designs": [{"statement_id": "s1", "design_shown": True, "rationale": "ok"}],
                },
            )
        )
        await session.commit()
    verdicts = (await api.get(f"/api/agent-runs/{run['id']}/verdicts")).json()
    assert len(verdicts) == 1
    assert verdicts[0]["criteria"] == [
        {
            "index": 1,
            "criterion": "Opposing evidence",
            "met": False,
            "rationale": "Only supportive searches.",
            "supporting_statement_ids": [],
        }
    ]
    assert verdicts[0]["designs"][0]["design_shown"] is True and verdicts[0]["searches"] == ["q"]
    assert (await api.get(f"/api/agent-runs/{uuid.uuid4()}/verdicts")).status_code == 404


async def test_the_graph_shows_which_claim_replaced_a_withdrawn_one(api) -> None:
    from app.agent.toolbox import Toolbox

    world = await make_world(sources=SOURCES)
    toolbox = Toolbox(session_factory, world.run_id, None)

    async def record(text, call_id):
        args = claim_args(world, text, "trial")
        return (await toolbox.call("record_claim", args, call_id))["statement_id"]

    old, new = await record("Overstated.", "toolu_a"), await record("Corrected.", "toolu_b")
    await toolbox.call(
        "revise_claim", {"statement_id": old, "reason": "Too strong.", "replaced_by_id": new}, "r"
    )
    graph = (await api.get(f"/api/workspaces/{world.workspace_id}/graph")).json()
    by_id = {s["id"]: s for s in graph["statements"]}
    assert by_id[old]["lifecycle"] == "rejected" and by_id[old]["superseded_by"] == new
    assert by_id[new]["superseded_by"] is None


async def test_a_workspace_lists_its_sources_including_those_an_agent_fetched(api) -> None:
    world = await make_world(sources=SOURCES)
    other = await make_world(sources={"elsewhere": ["Not ours."]})
    listed = (await api.get(f"/api/workspaces/{world.workspace_id}/sources")).json()
    assert [s["title"] for s in listed] == ["trial"]
    assert (await api.get(f"/api/workspaces/{other.workspace_id}/sources")).json()[0]["title"] == (
        "elsewhere"
    )
    assert (await api.get(f"/api/workspaces/{uuid.uuid4()}/sources")).status_code == 404
