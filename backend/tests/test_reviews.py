"""The reviewer calls behind the document checks (step critic, cross-source links) and the
shared structured-call helper they all use."""

import json

import anthropic
import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.agent.toolbox import Toolbox
from app.config import get_settings
from app.database import engine, session_factory
from app.models import Annotation, GraphEdge
from app.schemas import ArgumentCheckRequest, SynthesisRequest
from app.services.argument_check import check_arguments
from app.services.claude_call import ClaudeCallFailed, new_tally, structured_call
from app.services.judge import Judge, LinkAudits
from app.services.synthesis import synthesize_workspace
from tests.agent_helpers import FakeClaude, claim_args, make_world, reply, text, tool

SOURCES = {"a": ["Drug X cut mortality by 30%."], "b": ["Drug X did not change mortality."]}


@pytest.fixture(autouse=True)
async def fresh_engine(monkeypatch):
    monkeypatch.setattr(get_settings(), "claude_api_key", "test-key")
    await engine.dispose()
    yield
    await engine.dispose()


async def record(toolbox, world, text_, source) -> str:
    result = await toolbox.call(
        "record_claim", claim_args(world, text_, source), f"toolu_{len(text_)}"
    )
    assert "statement_id" in result, result
    return result["statement_id"]


async def two_claims_and_a_step():
    world = await make_world(sources=SOURCES)
    toolbox = Toolbox(session_factory, world.run_id, None)
    premise = await record(toolbox, world, "Drug X cut mortality.", "a")
    other = await record(toolbox, world, "Drug X had no effect on mortality.", "b")
    step = await toolbox.call(
        "record_reasoning",
        {
            "premise_ids": [premise],
            "conclusion_id": other,
            "explanation": "So.",
            "scope_change": None,
            "revises_step_id": None,
        },
        "t2",
    )
    assert "step_id" in step, step
    return world, premise, other, step["step_id"]


def material_of(request) -> dict:
    return json.loads(request["messages"][0]["content"])


# -- the step critic ---------------------------------------------------------------------------


async def test_the_critic_flags_steps_through_the_judge_and_counts_its_tokens() -> None:
    world, _, _, step_id = await two_claims_and_a_step()
    client = FakeClaude(
        reply(
            tool(
                "submit_argument_check",
                assessments=[
                    {"reasoning_step_id": step_id, "verdict": "needs_support", "rationale": "Gap."}
                ],
            ),
            stop="tool_use",
        )
    )
    judge = Judge(client, model="judge-model")
    async with session_factory() as session:
        result = await check_arguments(
            session,
            world.workspace_id,
            ArgumentCheckRequest(idempotency_key="k1", model="critic-model"),
            judge=judge,
        )
        flagged = list(
            await session.scalars(
                select(Annotation).where(
                    Annotation.workspace_id == world.workspace_id,
                    Annotation.type == "required_premise",
                )
            )
        )
    assert (result.checked_steps, result.flagged_steps) == (1, 1)
    assert [a.value["description"] for a in flagged] == ["Gap."]
    assert client.requests[0]["model"] == "critic-model"  # the request's model wins
    assert material_of(client.requests[0])["steps"][0]["reasoning_step_id"] == step_id
    assert judge.usage["calls"] == 1 and judge.usage["input_tokens"] == 1000


async def test_a_critic_failure_is_a_readable_502_and_writes_nothing() -> None:
    world, *_ = await two_claims_and_a_step()
    judge = Judge(FakeClaude(reply(text("no tool"))), model="m")
    async with session_factory() as session:
        with pytest.raises(HTTPException) as caught:
            await check_arguments(
                session, world.workspace_id, ArgumentCheckRequest(idempotency_key="k2"), judge=judge
            )
        assert not list(
            await session.scalars(
                select(Annotation).where(Annotation.workspace_id == world.workspace_id)
            )
        )
    assert caught.value.status_code == 502
    assert caught.value.detail.startswith("Claude argument check failed.")


async def test_a_critic_that_skips_a_step_is_refused() -> None:
    world, *_ = await two_claims_and_a_step()
    judge = Judge(FakeClaude(reply(tool("submit_argument_check", assessments=[]))), model="m")
    async with session_factory() as session:
        with pytest.raises(HTTPException) as caught:
            await check_arguments(
                session, world.workspace_id, ArgumentCheckRequest(idempotency_key="k3"), judge=judge
            )
    assert caught.value.status_code == 502 and "every reasoning step" in caught.value.detail


# -- cross-source links ------------------------------------------------------------------------


async def test_links_are_proposed_then_audited_by_the_judge() -> None:
    world, premise, other, _ = await two_claims_and_a_step()

    def proposals(request):
        return reply(
            tool(
                "submit_link_proposals",
                links=[
                    {
                        "source_statement_id": premise,
                        "target_statement_id": other,
                        "relation": "rebuts",
                        "rationale": "Opposite findings.",
                    }
                ],
            ),
            stop="tool_use",
        )

    def audits(request):
        link = material_of(request)["links"][0]
        assert link["source"]["excerpts"] and link["relation"] == "rebuts"
        return reply(
            tool(
                "submit_link_audits",
                audits=[
                    {
                        "source_statement_id": premise,
                        "target_statement_id": other,
                        "relation": "rebuts",
                        "verdict": "supported",
                        "rationale": "Direct conflict.",
                    }
                ],
            ),
            stop="tool_use",
        )

    client = FakeClaude(proposals, audits)
    judge = Judge(client, model="m")
    async with session_factory() as session:
        result = await synthesize_workspace(
            session, world.workspace_id, SynthesisRequest(idempotency_key="s1"), judge=judge
        )
        edges = list(
            await session.scalars(
                select(GraphEdge).where(
                    GraphEdge.workspace_id == world.workspace_id, GraphEdge.relation == "rebuts"
                )
            )
        )
    assert (result.proposed_links, result.audited_links, result.links_needing_review) == (1, 1, 0)
    assert [e.metadata_["audit_verdict"] for e in edges] == ["supported"]
    assert judge.usage["calls"] == 2  # the proposal and the audit are both counted


async def test_no_proposed_links_means_no_audit_call() -> None:
    world, *_ = await two_claims_and_a_step()
    client = FakeClaude(reply(tool("submit_link_proposals", links=[]), stop="tool_use"))
    judge = Judge(client, model="m")
    async with session_factory() as session:
        result = await synthesize_workspace(
            session, world.workspace_id, SynthesisRequest(idempotency_key="s2"), judge=judge
        )
    assert result.proposed_links == 0 and judge.usage["calls"] == 1


async def test_a_synthesis_failure_is_a_readable_502() -> None:
    world, *_ = await two_claims_and_a_step()
    judge = Judge(FakeClaude(reply(text("no tool"))), model="m")
    async with session_factory() as session:
        with pytest.raises(HTTPException) as caught:
            await synthesize_workspace(
                session, world.workspace_id, SynthesisRequest(idempotency_key="s3"), judge=judge
            )
    assert caught.value.status_code == 502
    assert caught.value.detail.startswith("Claude synthesis request failed.")


# -- the shared call ----------------------------------------------------------------------------


async def test_a_call_describes_anthropics_own_error_without_the_request() -> None:
    def fail(request):
        response = httpx.Response(
            404,
            json={"type": "error", "error": {"type": "not_found_error", "message": "model: gone"}},
            request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
        )
        raise anthropic.NotFoundError("not found", response=response, body=None)

    with pytest.raises(ClaudeCallFailed) as caught:
        await structured_call(
            FakeClaude(fail),
            model="m",
            system="s",
            content={},
            tool_name="t",
            description="d",
            schema=LinkAudits,
            task="x",
        )
    assert str(caught.value) == "Claude returned 404 not_found_error: model: gone"


async def test_a_call_counts_usage_even_when_the_answer_is_unusable() -> None:
    tally = new_tally()
    with pytest.raises(ClaudeCallFailed, match="cut off"):
        await structured_call(
            FakeClaude(reply(tool("t", audits=[]), stop="max_tokens")),
            model="m",
            system="s",
            content="plain text input",
            tool_name="t",
            description="d",
            schema=LinkAudits,
            task="x",
            tally=tally,
        )
    assert tally == {"calls": 1, "input_tokens": 1000, "output_tokens": 200}


async def test_a_call_ignores_a_tool_it_did_not_ask_for() -> None:
    with pytest.raises(ClaudeCallFailed, match="no result"):
        await structured_call(
            FakeClaude(reply(tool("other", audits=[]), stop="tool_use")),
            model="m",
            system="s",
            content={},
            tool_name="t",
            description="d",
            schema=LinkAudits,
            task="x",
        )


async def test_the_guardrails_own_critic_call_lands_in_the_judges_tally() -> None:
    world = await make_world(sources={"a": ["Drug X cut mortality.", "Mortality fell 30%."]})
    client = FakeClaude(
        lambda request: reply(
            tool(
                "submit_argument_check",
                assessments=[
                    {
                        "reasoning_step_id": material_of(request)["steps"][0]["reasoning_step_id"],
                        "verdict": "supported",
                        "rationale": "Stated.",
                    }
                ],
            ),
            stop="tool_use",
        )
    )
    judge = Judge(client, model="claude-sonnet-5")
    toolbox = Toolbox(session_factory, world.run_id, None, judge)
    premise = await record(toolbox, world, "Drug X cut mortality.", "a")
    conclusion = (
        await toolbox.call(
            "record_claim",
            claim_args(world, "Mortality fell.", "a", index=1, role="conclusion"),
            "toolu_conclusion",
        )
    )["statement_id"]
    await toolbox.call(
        "record_reasoning",
        {
            "premise_ids": [premise],
            "conclusion_id": conclusion,
            "explanation": "So.",
            "scope_change": None,
            "revises_step_id": None,
        },
        "toolu_step",
    )
    await toolbox.call("check_conclusion", {"statement_id": conclusion}, "toolu_check")
    assert judge.usage["calls"] == 1 and client.requests[0]["model"] == "claude-sonnet-5"
