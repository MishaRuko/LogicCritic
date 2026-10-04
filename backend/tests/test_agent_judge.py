"""The independent judge: criteria and causal designs are decided from the cited text."""

import uuid

import anthropic
import httpx
import pytest
from sqlalchemy import select

from app.agent.loop import execute_run
from app.agent.toolbox import Toolbox
from app.config import get_settings
from app.database import engine, session_factory
from app.models import AgentEvent, AgentRun, JudgeVerdict, ResearchGoal
from app.services.claude_call import ClaudeCallFailed
from app.services.judge import (
    DEFAULT_CRITERIA,
    CriterionVerdict,
    DesignVerdict,
    Judge,
    JudgeOutput,
    ProposedCriteria,
)
from tests.agent_helpers import (
    FakeClaude,
    FakeJudge,
    claim_args,
    make_world,
    reply,
    text,
    tool,
)

SOURCES = {
    "trial": ["Patients were randomly assigned to drug X or placebo; mortality fell."],
    "cohort": ["In a cohort study, users of drug X had lower mortality."],
}


@pytest.fixture(autouse=True)
async def fresh_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path))
    monkeypatch.setattr(get_settings(), "claude_api_key", None)
    monkeypatch.setattr("app.agent.loop.Judge", FakeJudge)
    await engine.dispose()
    yield
    await engine.dispose()


def box(world, judge) -> Toolbox:
    return Toolbox(session_factory, world.run_id, None, judge)


async def call(toolbox, name, **arguments) -> dict:
    return await toolbox.call(name, arguments, f"toolu_{uuid.uuid4().hex[:8]}")


async def record(toolbox, world, text_, source, **extra) -> str:
    result = await call(toolbox, "record_claim", **claim_args(world, text_, source, **extra))
    assert "statement_id" in result, result
    return result["statement_id"]


def kinds(packet: dict) -> list[str]:
    return sorted(o["kind"] for o in packet["obligations"])


def judged(met: bool = True, shown: bool = True):
    def verdict(material: dict) -> JudgeOutput:
        ids = [c["statement_id"] for c in material["claims"]]
        return JudgeOutput(
            criteria=[
                CriterionVerdict(
                    index=i, met=met, rationale="Judged.", supporting_statement_ids=ids[:1]
                )
                for i in range(len(material["criteria"]))
            ],
            designs=[
                DesignVerdict(statement_id=c["statement_id"], design_shown=shown, rationale="r")
                for c in material["claims"]
                if c["declared_design"]
            ],
        )

    return verdict


async def test_the_judge_overrules_the_agents_own_tags() -> None:
    world = await make_world(criteria=["A randomised trial"], sources=SOURCES)
    toolbox = box(world, FakeJudge(verdict=judged(met=False)))
    claim = await record(
        toolbox, world, "Drug X works.", "cohort", role="conclusion", criteria_satisfied=[0]
    )
    packet = await call(toolbox, "check_conclusion", statement_id=claim)
    assert kinds(packet) == ["unmet_criteria"]
    assert "Judged." in packet["obligations"][0]["description"]
    assert packet["can_finalize_as"] == ["conditional", "hypothesis"]


async def test_a_criterion_the_judge_accepts_needs_no_tag() -> None:
    world = await make_world(criteria=["A randomised trial"], sources=SOURCES)
    toolbox = box(world, FakeJudge(verdict=judged(met=True)))
    claim = await record(toolbox, world, "Drug X works.", "trial", role="conclusion")
    packet = await call(toolbox, "check_conclusion", statement_id=claim)
    assert kinds(packet) == [] and packet["can_finalize_as"][0] == "established"


async def test_the_judge_sees_text_hints_and_queries_but_not_retracted_claims() -> None:
    world = await make_world(
        criteria=["A randomised trial"],
        sources={**SOURCES, "bad": ["Drug X is safe."]},
        retracted=("bad",),
    )
    async with session_factory() as session:
        session.add_all(
            [
                AgentEvent(
                    run_id=world.run_id,
                    seq=1,
                    type="tool_call",
                    payload={"name": "search_papers", "input": {"query": "drug x harms"}},
                ),
                AgentEvent(
                    run_id=world.run_id, seq=2, type="web_search", payload={"query": "x failure"}
                ),
            ]
        )
        await session.commit()
    judge = FakeJudge(verdict=judged())
    toolbox = box(world, judge)
    good = await record(toolbox, world, "Drug X works.", "trial", criteria_satisfied=[0])
    bad = await record(toolbox, world, "Drug X is safe.", "bad")
    conclusion = await record(toolbox, world, "Drug X is good.", "trial", role="conclusion")
    for premise in (good, bad):
        await call(
            toolbox,
            "record_reasoning",
            premise_ids=[premise],
            conclusion_id=conclusion,
            explanation="So.",
            scope_change=None,
            revises_step_id=None,
        )
    await call(toolbox, "check_conclusion", statement_id=conclusion)
    material = judge.materials[-1]
    ids = {c["statement_id"]: c for c in material["claims"]}
    assert bad not in ids and good in ids
    assert ids[good]["criteria_hint"] == [0]
    assert "randomly assigned" in ids[good]["cited"][0]["text"]
    assert material["searches"] == ["drug x harms", "x failure"]
    assert material["criteria"] == ["A randomised trial"]


async def test_a_declared_design_the_text_does_not_show_blocks_the_conclusion() -> None:
    world = await make_world(sources=SOURCES)
    toolbox = box(world, FakeJudge(verdict=judged(shown=False)))
    claim = await record(
        toolbox,
        world,
        "Drug X lowered mortality.",
        "cohort",
        role="conclusion",
        claim_strength="causal",
        causal_support={"design": "randomised_trial", "justification": "trust me"},
    )
    packet = await call(toolbox, "check_conclusion", statement_id=claim)
    assert "causal_design_not_shown" in kinds(packet)
    assert "established" not in packet["can_finalize_as"]


async def test_when_the_judge_is_down_the_gate_closes_but_a_caveated_answer_is_allowed() -> None:
    world = await make_world(criteria=["A randomised trial"], sources=SOURCES)
    toolbox = box(world, FakeJudge(down=True))
    claim = await record(
        toolbox, world, "Drug X works.", "trial", role="conclusion", criteria_satisfied=[0]
    )
    packet = await call(toolbox, "check_conclusion", statement_id=claim)
    assert kinds(packet) == ["judge_unavailable"]
    assert packet["can_finalize_as"] == ["conditional", "hypothesis"]


async def test_a_verdict_is_reused_until_the_evidence_changes() -> None:
    world = await make_world(criteria=["A randomised trial"], sources=SOURCES)
    judge = FakeJudge(verdict=judged())
    toolbox = box(world, judge)
    claim = await record(toolbox, world, "Drug X works.", "trial", role="conclusion")
    await call(toolbox, "check_conclusion", statement_id=claim)
    await call(toolbox, "check_conclusion", statement_id=claim)
    assert judge.usage["calls"] == 1
    other = await record(toolbox, world, "Another claim.", "cohort")
    await call(
        toolbox,
        "record_reasoning",
        premise_ids=[other],
        conclusion_id=claim,
        explanation="So.",
        scope_change=None,
        revises_step_id=None,
    )
    await call(toolbox, "check_conclusion", statement_id=claim)
    assert judge.usage["calls"] == 2


async def test_no_criteria_and_no_causal_claims_means_the_judge_is_not_called() -> None:
    world = await make_world(sources=SOURCES)
    judge = FakeJudge()
    toolbox = box(world, judge)
    claim = await record(toolbox, world, "Drug X works.", "trial", role="conclusion")
    await call(toolbox, "check_conclusion", statement_id=claim)
    assert judge.materials == []


# -- the model-backed judge --------------------------------------------------------------------


def judge_reply(**fields):
    response = reply(tool("submit_assessment", **fields), stop="tool_use")
    return response


async def test_the_real_judge_forces_its_tool_and_counts_its_tokens() -> None:
    client = FakeClaude(
        judge_reply(
            criteria=[
                {
                    "index": 0,
                    "met": True,
                    "rationale": "Trial text.",
                    "supporting_statement_ids": ["s1"],
                }
            ],
            designs=[],
        )
    )
    judge = Judge(client, model="claude-sonnet-5")
    output = await judge.assess({"criteria": ["c"], "claims": [{"statement_id": "s1"}]})
    request = client.requests[0]
    assert request["tool_choice"] == {"type": "tool", "name": "submit_assessment"}
    assert request["tools"][0]["strict"] is True and "temperature" not in request
    assert output.criteria[0].met is True
    assert judge.usage == {"calls": 1, "input_tokens": 1000, "output_tokens": 200}


async def test_a_verdict_is_made_complete_and_cannot_cite_invented_claims() -> None:
    client = FakeClaude(
        judge_reply(
            criteria=[
                {
                    "index": 0,
                    "met": True,
                    "rationale": "Looks fine.",
                    "supporting_statement_ids": ["invented"],
                },
                {
                    "index": 7,
                    "met": True,
                    "rationale": "Out of range.",
                    "supporting_statement_ids": [],
                },
            ],
            designs=[{"statement_id": "invented", "design_shown": False, "rationale": "x"}],
        )
    )
    output = await Judge(client, model="m").assess(
        {"criteria": ["a", "b"], "claims": [{"statement_id": "s1"}]}
    )
    assert [c.met for c in output.criteria] == [False, False]  # unsupported "met"; missing one
    assert output.designs == []


async def test_a_search_based_criterion_can_be_met_without_a_supporting_claim() -> None:
    client = FakeClaude(
        judge_reply(
            criteria=[
                {
                    "index": 0,
                    "met": True,
                    "rationale": "The searches targeted harms and negative results.",
                    "supporting_statement_ids": [],
                }
            ],
            designs=[],
        )
    )
    output = await Judge(client, model="m").assess({"criteria": ["opposing"], "claims": []})
    assert output.criteria[0].met is True


@pytest.mark.parametrize(
    "response",
    [
        reply(text("no tool"), stop="end_turn"),
        reply(tool("submit_assessment", criteria="oops", designs=[]), stop="tool_use"),
        reply(tool("submit_assessment", criteria=[], designs=[]), stop="max_tokens"),
    ],
)
async def test_an_unusable_judge_answer_is_unavailable(response) -> None:
    with pytest.raises(ClaudeCallFailed):
        await Judge(FakeClaude(response), model="m").assess({"criteria": [], "claims": []})


async def test_an_api_failure_is_unavailable() -> None:
    def fail(request):
        raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://x"))

    with pytest.raises(ClaudeCallFailed):
        await Judge(FakeClaude(fail), model="m").assess({"criteria": [], "claims": []})


# -- the run: criteria are settled before research starts ---------------------------------------


async def run_goal(world, judge=None, mode="guarded"):
    client = FakeClaude(reply(text("Nothing to add."), stop="end_turn"))
    await execute_run(world.run_id, client=client, judge=judge)
    async with session_factory() as session:
        goal = await session.get(ResearchGoal, world.goal_id)
        events = list(
            await session.scalars(
                select(AgentEvent).where(AgentEvent.run_id == world.run_id).order_by(AgentEvent.seq)
            )
        )
        run = await session.get(AgentRun, world.run_id)
    return goal, events, run, client


async def test_criteria_are_proposed_up_front_when_none_are_given() -> None:
    world = await make_world()
    proposed = ProposedCriteria(completion_criteria=["A", "B", " "], falsifiers=["F"])
    judge = FakeJudge(proposed=proposed)
    goal, events, run, client = await run_goal(world, judge)
    assert goal.completion_criteria == ["A", "B"] and goal.falsifiers == ["F"]
    event = next(e for e in events if e.type == "criteria_proposed")
    assert event.payload == {"criteria": ["A", "B"], "falsifiers": ["F"]}
    assert "0. A" in client.requests[0]["messages"][0]["content"]


async def test_given_criteria_are_kept_and_not_regenerated() -> None:
    world = await make_world(criteria=["Mine"])
    judge = FakeJudge()
    goal, events, *_ = await run_goal(world, judge)
    assert goal.completion_criteria == ["Mine"] and judge.usage["calls"] == 0
    assert [e.type for e in events if e.type.startswith("criteria")] == ["criteria_given"]


async def test_generic_criteria_apply_when_none_can_be_proposed() -> None:
    world = await make_world()
    goal, events, *_ = await run_goal(world, FakeJudge(down=True))
    assert goal.completion_criteria == DEFAULT_CRITERIA
    assert any(e.type == "criteria_default" for e in events)


async def test_a_baseline_run_has_no_judge_and_no_criteria() -> None:
    world = await make_world(mode="baseline")
    judge = FakeJudge()
    goal, events, *_ = await run_goal(world, judge)
    assert goal.completion_criteria == [] and judge.usage["calls"] == 0
    assert not any(e.type.startswith("criteria") for e in events)


async def test_the_judges_tokens_are_part_of_the_run_cost() -> None:
    world = await make_world()
    _, _, run, _ = await run_goal(world, FakeJudge())
    assert run.usage["judge"]["calls"] == 1
    assert run.usage["judge_cost_usd"] > 0
    agent_only = (1000 * 2.0 + 200 * 10.0) / 1_000_000
    assert run.usage["cost_usd"] > round(agent_only, 4)


async def test_the_kind_shapes_the_opening_message() -> None:
    for kind, phrase in (
        ("claim", "Claim to assess"),
        ("hypothesis", "Look for what would falsify it"),
        ("question", "Research question"),
    ):
        world = await make_world(criteria=["x"])
        async with session_factory() as session:
            goal = await session.get(ResearchGoal, world.goal_id)
            goal.kind = kind
            await session.commit()
        *_, client = await run_goal(world, FakeJudge())
        assert phrase in client.requests[0]["messages"][0]["content"], kind


async def test_a_partly_met_criterion_is_not_met() -> None:
    client = FakeClaude(
        judge_reply(
            criteria=[
                {
                    "index": 0,
                    "met": True,
                    "gap": "Severe and hospitalised patients are not covered.",
                    "rationale": "Mostly covered by the trial.",
                    "supporting_statement_ids": ["s1"],
                }
            ],
            designs=[],
        )
    )
    output = await Judge(client, model="m").assess(
        {"criteria": ["c"], "claims": [{"statement_id": "s1"}]}
    )
    assert output.criteria[0].met is False
    assert "Still missing: Severe and hospitalised" in output.criteria[0].rationale


async def test_verdicts_are_kept_in_their_own_table_not_in_the_run_usage() -> None:
    world = await make_world(criteria=["A randomised trial"], sources=SOURCES)
    judge = FakeJudge(verdict=judged())
    toolbox = box(world, judge)
    claim = await record(toolbox, world, "Drug X works.", "trial", role="conclusion")
    await call(toolbox, "check_conclusion", statement_id=claim)
    await call(toolbox, "check_conclusion", statement_id=claim)
    async with session_factory() as session:
        rows = list(
            await session.scalars(select(JudgeVerdict).where(JudgeVerdict.run_id == world.run_id))
        )
        run = await session.get(AgentRun, world.run_id)
    assert len(rows) == 1 and judge.usage["calls"] == 1
    assert rows[0].material["criteria"] == ["A randomised trial"]
    assert rows[0].verdict["criteria"][0]["met"] is True
    assert "judge_cache" not in run.usage


def test_design_verdicts_on_claims_without_a_declared_design_are_dropped() -> None:
    from app.services.judge import DesignVerdict, JudgeOutput, _tidy

    material = {
        "criteria": [],
        "claims": [
            {"statement_id": "declared", "declared_design": "randomised_trial"},
            {"statement_id": "descriptive", "declared_design": None},
        ],
    }
    output = JudgeOutput(
        criteria=[],
        designs=[
            DesignVerdict(statement_id="declared", design_shown=False, rationale="no randomisation"),
            DesignVerdict(statement_id="descriptive", design_shown=False, rationale="not causal"),
        ],
    )

    assert [d.statement_id for d in _tidy(output, material).designs] == ["declared"]
