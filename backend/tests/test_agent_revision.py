"""The agent amends its own graph: withdrawing and replacing claims, and what that does to the
argument built on them."""

import json
import uuid

import pytest
from sqlalchemy import select

from app.agent.loop import execute_run
from app.agent.toolbox import Toolbox
from app.config import get_settings
from app.database import engine, session_factory
from app.models import AgentEvent, GraphEdge, GraphEvent, Statement
from tests.agent_helpers import FakeClaude, FakeJudge, claim_args, make_world, reply, text, tool

# These test the verifier's rules; the evidence ceiling is tested in test_agent_evidence.py.
pytestmark = pytest.mark.usefixtures("ample_evidence")

SOURCES = {
    "trial": ["Drug X reduced 28-day mortality by 30% in a randomised trial of adults."],
    "review": ["A review found the benefit was smaller than first reported."],
}


@pytest.fixture(autouse=True)
async def fresh_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path))
    monkeypatch.setattr(get_settings(), "claude_api_key", None)
    monkeypatch.setattr("app.agent.loop.Judge", FakeJudge)
    await engine.dispose()
    yield
    await engine.dispose()


def box(world) -> Toolbox:
    return Toolbox(session_factory, world.run_id, None)


async def call(toolbox, name, tool_id=None, **arguments) -> dict:
    return await toolbox.call(name, arguments, tool_id or f"toolu_{uuid.uuid4().hex[:8]}")


async def record(toolbox, world, text_, source, **extra) -> str:
    result = await call(toolbox, "record_claim", **claim_args(world, text_, source, **extra))
    assert "statement_id" in result, result
    return result["statement_id"]


async def reason(toolbox, premises, conclusion, **extra) -> dict:
    return await call(
        toolbox,
        "record_reasoning",
        premise_ids=premises,
        conclusion_id=conclusion,
        explanation="So.",
        scope_change=None,
        revises_step_id=None,
        **extra,
    )


async def revise(toolbox, old, new=None, reason_="Worded too strongly.", **extra) -> dict:
    return await call(
        toolbox, "revise_claim", statement_id=old, reason=reason_, replaced_by_id=new, **extra
    )


async def lifecycle_of(statement_id: str) -> str:
    async with session_factory() as session:
        return (await session.get(Statement, uuid.UUID(statement_id))).lifecycle


# -- revise_claim ------------------------------------------------------------------------------


async def test_a_claim_is_superseded_by_a_corrected_one() -> None:
    world = await make_world(sources=SOURCES)
    toolbox = box(world)
    old = await record(toolbox, world, "Drug X cures mortality.", "trial")
    new = await record(toolbox, world, "Drug X reduced mortality in one trial.", "trial")
    result = await revise(toolbox, old, new)
    assert result["withdrawn"] == old and result["replaced_by"] == new
    async with session_factory() as session:
        statement = await session.get(Statement, uuid.UUID(old))
        edge = await session.scalar(
            select(GraphEdge).where(
                GraphEdge.workspace_id == world.workspace_id, GraphEdge.relation == "revises"
            )
        )
        event = await session.scalar(
            select(GraphEvent).where(
                GraphEvent.workspace_id == world.workspace_id,
                GraphEvent.event_type == "agent_revision",
            )
        )
    assert statement.lifecycle == "rejected" and statement.superseded_by == uuid.UUID(new)
    assert (str(edge.source_node_id), str(edge.target_node_id)) == (new, old)
    assert edge.metadata_["reason"] == "Worded too strongly."
    assert event.payload["withdrawn"] == old and event.provenance["actor_type"] == "agent"
    assert await lifecycle_of(new) == "proposed"  # the replacement stands


async def test_a_claim_can_be_withdrawn_without_a_replacement() -> None:
    world = await make_world(sources=SOURCES)
    toolbox = box(world)
    old = await record(toolbox, world, "Drug X works.", "trial")
    result = await revise(toolbox, old)
    assert result["replaced_by"] is None and await lifecycle_of(old) == "rejected"


async def test_repeating_the_same_call_changes_nothing_more() -> None:
    world = await make_world(sources=SOURCES)
    toolbox = box(world)
    old = await record(toolbox, world, "Drug X works.", "trial")
    first = await revise(toolbox, old, tool_id="toolu_same")
    again = await revise(toolbox, old, tool_id="toolu_same")
    assert "error" not in first and again["withdrawn"] == old


async def test_only_claims_the_agent_recorded_can_be_revised() -> None:
    world = await make_world(sources=SOURCES)
    async with session_factory() as session:
        other = Statement(
            workspace_id=world.workspace_id,
            text="Extracted from a paper.",
            assertion_mode="reported",
            provenance={"actor_type": "extractor", "run_id": str(uuid.uuid4())},
        )
        session.add(other)
        await session.commit()
        other_id = str(other.id)
    result = await revise(box(world), other_id)
    assert "only claims the research agent recorded" in result["error"].lower()
    assert await lifecycle_of(other_id) == "proposed"


async def test_bad_revisions_are_refused_with_a_reason() -> None:
    world = await make_world(sources=SOURCES)
    toolbox = box(world)
    old = await record(toolbox, world, "Drug X works.", "trial")
    gone = await record(toolbox, world, "Drug X works well.", "trial")
    await revise(toolbox, gone)
    assert "No recorded claim" in (await revise(toolbox, str(uuid.uuid4())))["error"]
    assert "must be an id" in (await revise(toolbox, "nonsense"))["error"]
    assert "already withdrawn" in (await revise(toolbox, gone))["error"]
    assert "must be a claim" in (await revise(toolbox, old, gone))["error"]
    assert "must be a claim" in (await revise(toolbox, old, str(uuid.uuid4())))["error"]
    assert "replace itself" in (await revise(toolbox, old, old))["error"]
    assert "why" in (await revise(toolbox, old, reason_="  "))["error"]
    assert await lifecycle_of(old) == "proposed"


async def test_reasoning_cannot_use_a_withdrawn_claim() -> None:
    world = await make_world(sources=SOURCES)
    toolbox = box(world)
    old = await record(toolbox, world, "Drug X works.", "trial")
    conclusion = await record(toolbox, world, "So Drug X is good.", "trial", role="conclusion")
    await revise(toolbox, old)
    result = await reason(toolbox, [old], conclusion)
    assert "was withdrawn" in result["error"]


# -- what the guardrail makes of it ------------------------------------------------------------


async def check(toolbox, statement_id) -> dict:
    return await call(toolbox, "check_conclusion", statement_id=statement_id)


def kinds(packet) -> list[str]:
    return sorted(o["kind"] for o in packet["obligations"])


async def test_a_step_resting_on_a_withdrawn_claim_blocks_until_it_is_re_recorded() -> None:
    world = await make_world(sources=SOURCES)
    toolbox = box(world)
    old = await record(toolbox, world, "Drug X cures mortality.", "trial")
    conclusion = await record(toolbox, world, "Drug X helps.", "trial", role="conclusion")
    step = (await reason(toolbox, [old], conclusion))["step_id"]
    assert kinds(await check(toolbox, conclusion)) == []

    new = await record(toolbox, world, "Drug X reduced mortality in one trial.", "trial")
    await revise(toolbox, old, new)
    stale = await check(toolbox, conclusion)
    assert kinds(stale) == ["withdrawn_premise"]
    assert stale["obligations"][0]["applies_to"] == step
    assert new in stale["obligations"][0]["required_condition"]
    assert stale["can_finalize_as"] == ["speculative"]  # even a caveated answer is refused
    assert old not in str(stale["reasoning_steps"]) and old not in str(stale["evidence_chain"])

    fixed = await call(
        toolbox,
        "record_reasoning",
        premise_ids=[new],
        conclusion_id=conclusion,
        explanation="The corrected claim supports it.",
        scope_change=None,
        revises_step_id=step,
    )
    assert "step_id" in fixed
    clean = await check(toolbox, conclusion)
    assert kinds(clean) == [] and clean["can_finalize_as"][0] == "established"


async def test_a_withdrawn_claim_cannot_be_the_conclusion() -> None:
    world = await make_world(sources=SOURCES)
    toolbox = box(world)
    claim = await record(toolbox, world, "Drug X works.", "trial", role="conclusion")
    await revise(toolbox, claim)
    assert "error" in await check(toolbox, claim)


# -- the trace ---------------------------------------------------------------------------------


async def test_every_graph_change_is_in_the_trace_as_it_happens() -> None:
    world = await make_world(sources=SOURCES)
    ids = {}

    def claim_turn(name, text_, source):
        def make(request):
            return reply(
                tool("record_claim", **claim_args(world, text_, source, role=name)),
                stop="tool_use",
            )

        return make

    async def second(request):
        ids["old"] = _statement_id(request)
        return reply(
            tool("record_claim", **claim_args(world, "Narrower.", "review", role="premise")),
            stop="tool_use",
        )

    async def third(request):
        ids["new"] = _statement_id(request)
        return reply(
            tool(
                "revise_claim",
                statement_id=ids["old"],
                reason="Too strong.",
                replaced_by_id=ids["new"],
            ),
            stop="tool_use",
        )

    client = FakeClaude(
        claim_turn("premise", "Drug X cures mortality.", "trial"),
        second,
        third,
        reply(text("Done."), stop="end_turn"),
    )
    await execute_run(world.run_id, client=client)
    async with session_factory() as session:
        changes = [
            e.payload
            for e in await session.scalars(
                select(AgentEvent)
                .where(AgentEvent.run_id == world.run_id, AgentEvent.type == "graph_change")
                .order_by(AgentEvent.seq)
            )
        ]
    assert [c["change"] for c in changes] == ["claim_added", "claim_added", "claim_superseded"]
    assert changes[0]["text"] == "Drug X cures mortality."
    assert changes[2]["statement_id"] == ids["old"] and changes[2]["replaced_by_id"] == ids["new"]
    assert changes[2]["reason"] == "Too strong."


def _statement_id(request) -> str:
    results = [b for b in request["messages"][-1]["content"] if b["type"] == "tool_result"]
    return json.loads(results[0]["content"])["statement_id"]


# -- working position, from the first reading ---------------------------------------------------


async def read(toolbox, world, name) -> dict:
    result = await call(toolbox, "read_source", source_id=str(world.sources[name]), offset=0)
    assert "excerpts" in result, result
    return result


async def test_reading_reminds_the_agent_to_record_and_to_hold_a_position() -> None:
    world = await make_world(sources=SOURCES)
    toolbox = box(world)
    first = await read(toolbox, world, "trial")
    assert "record_claim" in first["note"] and "working position" not in first["note"]
    second = await read(toolbox, world, "review")
    assert "no working position yet" in second["note"] and "revise_claim" in second["note"]

    await record(toolbox, world, "Drug X might reduce mortality.", "trial", role="conclusion")
    third = await read(toolbox, world, "trial")
    assert "record_claim" in third["note"] and "working position" not in third["note"]


async def test_a_claim_that_is_not_a_conclusion_does_not_count_as_a_position() -> None:
    world = await make_world(sources=SOURCES)
    toolbox = box(world)
    await read(toolbox, world, "trial")
    await record(toolbox, world, "A premise.", "trial", role="premise")
    assert "no working position yet" in (await read(toolbox, world, "review"))["note"]


async def test_a_baseline_run_gets_no_recording_reminders() -> None:
    world = await make_world(mode="baseline", sources=SOURCES)
    page = await read(box(world), world, "trial")
    assert "note" not in page


def test_position_changes_are_marked_in_the_trace() -> None:
    from app.agent.loop import _graph_change

    added = _graph_change(
        "record_claim", {"role": "conclusion", "text": "T"}, {"statement_id": "s"}
    )
    assert added["position"] is True
    premise = _graph_change("record_claim", {"role": "premise", "text": "T"}, {"statement_id": "s"})
    assert premise["position"] is False
    moved = _graph_change(
        "revise_claim",
        {"reason": "New trial."},
        {"withdrawn": "old", "replaced_by": "new", "role": "conclusion"},
    )
    assert moved["change"] == "claim_superseded" and moved["position"] is True
    plain = _graph_change(
        "revise_claim",
        {"reason": "x"},
        {"withdrawn": "old", "replaced_by": None, "role": "premise"},
    )
    assert plain["change"] == "claim_withdrawn" and plain["position"] is False


def test_the_contract_asks_for_an_early_position_and_for_it_to_be_revised() -> None:
    from app.agent.prompts import GUARDED_SYSTEM

    assert "working position early" in GUARDED_SYSTEM
    assert "revise_claim on the old one" in GUARDED_SYSTEM


# -- a conclusion must be reasoned, and a changed position must be recorded ----------------------


async def test_an_asserted_conclusion_with_no_reasoning_behind_it_is_flagged() -> None:
    world = await make_world(sources=SOURCES)
    toolbox = box(world)
    premise = await record(toolbox, world, "Drug X reduced mortality in a trial.", "trial")
    # citing an excerpt directly does not stand in for the reasoning
    bare = await record(
        toolbox,
        world,
        "Drug X works for everyone.",
        "trial",
        role="conclusion",
        assertion_mode="asserted",
    )
    packet = await check(toolbox, bare)
    assert "unreasoned_conclusion" in kinds(packet)
    assert "established" not in packet["can_finalize_as"]
    assert "record_reasoning" in packet["obligations"][0]["required_condition"]

    await reason(toolbox, [premise], bare)
    assert "unreasoned_conclusion" not in kinds(await check(toolbox, bare))


async def test_a_reported_claim_needs_no_reasoning() -> None:
    world = await make_world(sources=SOURCES)
    toolbox = box(world)
    claim = await record(toolbox, world, "Drug X reduced mortality.", "trial")
    assert "unreasoned_conclusion" not in kinds(await check(toolbox, claim))


async def test_recording_a_second_conclusion_points_at_the_first_to_revise() -> None:
    world = await make_world(sources=SOURCES)
    toolbox = box(world)
    first = await call(
        toolbox,
        "record_claim",
        **claim_args(world, "Maybe it works.", "trial", role="conclusion"),
    )
    assert "revise_claim" not in first["note"]
    second = await call(
        toolbox,
        "record_claim",
        **claim_args(world, "It does not work.", "review", role="conclusion"),
    )
    old, new = first["statement_id"], second["statement_id"]
    assert f"revise_claim on {old}" in second["note"] and new in second["note"]

    # once the old position is revised, the new one is the position and no reminder follows
    await revise(toolbox, old, new)
    third = await call(
        toolbox, "record_claim", **claim_args(world, "A premise.", "trial", role="premise")
    )
    assert "revise_claim" not in third["note"]
    again = await call(
        toolbox, "record_claim", **claim_args(world, "Final.", "review", role="conclusion")
    )
    assert f"revise_claim on {new}" in again["note"]


async def test_withdrawing_the_position_means_the_agent_has_none() -> None:
    world = await make_world(sources=SOURCES)
    toolbox = box(world)
    position = await record(toolbox, world, "Maybe.", "trial", role="conclusion")
    await read(toolbox, world, "trial")
    await revise(toolbox, position)
    assert "no working position yet" in (await read(toolbox, world, "review"))["note"]


async def test_a_follow_up_run_can_correct_the_agents_own_earlier_answer() -> None:
    from app.models import AgentRun, ResearchGoal

    world = await make_world(sources=SOURCES)
    old = await record(box(world), world, "Earlier answer.", "trial")
    async with session_factory() as session:
        run = await session.get(AgentRun, world.run_id)
        goal = ResearchGoal(
            workspace_id=run.workspace_id,
            question="And now?",
            completion_criteria=[],
            falsifiers=[],
        )
        session.add(goal)
        await session.flush()
        follow_up = AgentRun(
            workspace_id=run.workspace_id,
            goal_id=goal.id,
            idempotency_key=str(uuid.uuid4()),
            mode="guarded",
            model=run.model,
            status="running",
            budgets=run.budgets,
            usage=run.usage,
        )
        session.add(follow_up)
        await session.commit()
        follow_up_id = follow_up.id
    result = await revise(Toolbox(session_factory, follow_up_id, None), old)
    assert "error" not in result and await lifecycle_of(old) == "rejected"
