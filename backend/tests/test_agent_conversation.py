import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.agent.conversation import conversation_messages, workspace_context
from app.agent.loop import execute_run
from app.agent.runs import claim_next_agent_run, create_run
from app.config import get_settings
from app.database import engine, session_factory
from app.models import AgentRun, Statement, StatementExcerpt, Workspace
from app.schemas import AgentRunCreate
from tests.agent_helpers import FakeClaude, make_world, reply, text


@pytest.fixture(autouse=True)
async def fresh_engine(monkeypatch):
    monkeypatch.setattr(get_settings(), "claude_api_key", None)
    await engine.dispose()
    yield
    await engine.dispose()


async def test_follow_up_reads_prior_answer_and_the_existing_workspace_graph():
    previous = await make_world(mode="baseline", sources={"study": ["The study used mice only."]})
    other = await make_world(mode="baseline")
    async with session_factory() as session:
        prior = await session.get(AgentRun, previous.run_id)
        prior.status = "succeeded"
        prior.final_report = "Earlier answer: the evidence is from mice, not humans."
        unrelated = await session.get(AgentRun, other.run_id)
        unrelated.status = "succeeded"
        unrelated.final_report = "PRIVATE OTHER WORKSPACE ANSWER"
        claim = Statement(
            workspace_id=previous.workspace_id, text="Mouse evidence only.",
            assertion_mode="reported", role="premise", lifecycle="proposed",
            provenance={"actor_type": "agent", "actor_id": "test"},
        )
        session.add(claim)
        await session.flush()
        session.add(StatementExcerpt(statement_id=claim.id, excerpt_id=previous.excerpts["study"][0]))
        await session.commit()
        current = await create_run(session, previous.workspace_id, AgentRunCreate(
            idempotency_key=str(uuid.uuid4()), question="What about evidence in humans?", mode="baseline",
        ))
        current.status = "running"
        current.heartbeat_at = datetime.now(UTC)
        await session.commit()
        current_id, claim_id = current.id, claim.id

    client = FakeClaude(reply(text("Human evidence still needs to be gathered.")))
    await execute_run(current_id, client=client)
    messages = client.requests[0]["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant", "user"]
    assert "Earlier answer" in messages[1]["content"]
    assert "What about evidence in humans?" in messages[-1]["content"]
    assert str(previous.sources["study"]) in messages[-1]["content"]
    assert str(claim_id) in messages[-1]["content"]
    assert previous.excerpt("study") in messages[-1]["content"]
    assert "PRIVATE OTHER WORKSPACE" not in str(messages)


async def test_context_excludes_future_prompts_and_queued_runs():
    world = await make_world(mode="baseline")
    async with session_factory() as session:
        future = await create_run(session, world.workspace_id, AgentRunCreate(
            idempotency_key=str(uuid.uuid4()), question="FUTURE USER MESSAGE", mode="baseline",
        ))
        future.status = "succeeded"
        future.final_report = "FUTURE ANSWER"
        await session.commit()
        assert await conversation_messages(session, world.run_id) == []
        assert await workspace_context(session, world.run_id) == ""


async def test_follow_up_queue_waits_for_prior_run_and_survives_competing_workers():
    async with session_factory() as session:
        workspace = Workspace(title="conversation queue test")
        session.add(workspace)
        await session.commit()
        first = await create_run(session, workspace.id, AgentRunCreate(
            idempotency_key=str(uuid.uuid4()), question="First queued question?",
        ))
        second = await create_run(session, workspace.id, AgentRunCreate(
            idempotency_key=str(uuid.uuid4()), question="Second queued follow-up?",
        ))
        first_id, second_id = first.id, second.id
    isolation = session_factory()
    # Other tests leave queued runs; make their workspaces unavailable to these workers.
    await isolation.execute(
        select(Workspace).where(Workspace.id != workspace.id).with_for_update()
    )
    try:
        claimed = await asyncio.gather(claim_next_agent_run(), claim_next_agent_run())
        assert sorted(str(item) for item in claimed if item) == [str(first_id)]
        assert await claim_next_agent_run() is None
        async with session_factory() as session:
            first = await session.get(AgentRun, first_id)
            first.status = "succeeded"
            await session.commit()
        assert await claim_next_agent_run() == second_id
    finally:
        await isolation.rollback()
        await isolation.close()
        async with session_factory() as session:
            await session.delete(await session.get(Workspace, workspace.id))
            await session.commit()
