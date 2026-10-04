import anthropic
import httpx
import pytest
from sqlalchemy import select

from app.agent.loop import build_tools, estimate_cost, execute_run
from app.agent.runs import EventLog
from app.config import get_settings
from app.database import engine, session_factory
from app.models import AgentEvent, AgentRun
from tests.agent_helpers import (
    FakeClaude,
    FakeJudge,
    claim_args,
    make_world,
    reply,
    text,
    thinking,
    tool,
    web_search,
)

SOURCES = {"trial": ["Drug X reduced 28-day mortality by 30% in a randomised trial of adults."]}


@pytest.fixture(autouse=True)
async def fresh_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path))
    monkeypatch.setattr(get_settings(), "claude_api_key", None)  # no critic / synthesis calls
    monkeypatch.setattr("app.agent.loop.Judge", FakeJudge)  # never reach the real judge
    await engine.dispose()
    yield
    await engine.dispose()


async def run_of(world) -> AgentRun:
    async with session_factory() as session:
        return await session.get(AgentRun, world.run_id)


async def events_of(world) -> list[AgentEvent]:
    async with session_factory() as session:
        return list(
            await session.scalars(
                select(AgentEvent).where(AgentEvent.run_id == world.run_id).order_by(AgentEvent.seq)
            )
        )


async def test_cancellation_during_a_model_call_does_not_write_claims_or_finish_the_run():
    world = await make_world(mode="baseline", sources=SOURCES)

    async def cancel_during_call(request):
        async with session_factory() as session:
            run = await session.get(AgentRun, world.run_id)
            run.status = "cancelled"
            await session.commit()
        return reply(text("This answer arrived after cancellation."))

    await execute_run(world.run_id, client=FakeClaude(cancel_during_call))
    run = await run_of(world)
    assert run.status == "cancelled" and run.final_report is None
    assert "assistant_text" not in types(await events_of(world))


def types(events) -> list[str]:
    return [e.type for e in events]


def statement_id_from(client: FakeClaude, request_index: int) -> str:
    import json

    return json.loads(client.tool_results(request_index)[0]["content"])["statement_id"]


# -- the research contract ---------------------------------------------------------------------


async def test_a_guarded_run_records_checks_and_finalizes() -> None:
    world = await make_world(sources=SOURCES)
    claim = claim_args(
        world, "Drug X reduced mortality in a randomised trial.", "trial", criteria_satisfied=[0, 1]
    )  # the run proposes two criteria of its own, since the goal gave none

    def check_it(request):
        return reply(
            tool("check_conclusion", statement_id=statement_id_from(client, 1)), stop="tool_use"
        )

    def finalize_it(request):
        return reply(
            tool(
                "finalize_conclusion",
                statement_id=statement_id_from(client, 1),
                certainty="established",
            ),
            stop="tool_use",
        )

    client = FakeClaude(
        reply(
            thinking("Plan: search first."),
            *web_search("drug x mortality", "https://a.example", "https://b.example"),
            tool("record_claim", **claim),
            stop="tool_use",
            tokens_in=2000,
            tokens_out=400,
        ),
        reply(
            text("Now I will check it."),
            tool(
                "search_papers", query="x", limit=3, published_after=None, exclude_retracted=False
            ),
            stop="tool_use",
        ),
        check_it,
        finalize_it,
        reply(
            text("Drug X reduces mortality (established)."),
            tokens_in=3000,
            tokens_out=100,
            cache_read=2500,
        ),
    )
    # turn 2 is a tool error (no Amass key): the run must carry on
    await execute_run(world.run_id, client=client, amass=None)

    run = await run_of(world)
    assert run.status == "succeeded" and run.certainty == "established"
    assert run.final_report.startswith("Drug X reduces mortality (established).")
    assert "[Verifier record] Final certainty: established." in run.final_report
    assert run.usage["turns"] == 5 and run.usage["input_tokens"] == 2000 + 1000 * 3 + 3000
    assert run.usage["cache_read_tokens"] == 2500 and run.usage["cost_usd"] > 0
    assert run.completed_at is not None

    events = await events_of(world)
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert types(events)[0] == "run_started" and types(events)[-1] == "run_finished"
    for kind in (
        "thinking",
        "web_search",
        "web_results",
        "tool_call",
        "tool_result",
        "check",
        "finalization",
        "assistant_text",
    ):
        assert kind in types(events), kind
    web = next(e for e in events if e.type == "web_results")
    assert [r["url"] for r in web.payload["results"]] == ["https://a.example", "https://b.example"]
    failed_search = next(
        e for e in events if e.type == "tool_result" and e.payload["name"] == "search_papers"
    )
    assert (
        failed_search.payload["is_error"] is True
        and "no Amass API key" in failed_search.payload["result"]["error"]
    )

    first = client.requests[0]
    assert first["model"] == "claude-sonnet-5-5" and first["cache_control"] == {"type": "ephemeral"}
    assert first["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert (
        "check_conclusion" in first["system"]
        and "Does drug X reduce mortality" in first["messages"][0]["content"]
    )
    names = [t["name"] for t in first["tools"]]
    assert (
        names[:4] == ["search_papers", "read_paper", "fetch_url", "read_source"]
        and names[-1] == "web_search"
    )
    assert {
        "record_claim",
        "record_reasoning",
        "check_conclusion",
        "finalize_conclusion",
        "abstain",
    } <= set(names)


async def test_the_guardrail_stops_a_conclusion_that_rests_on_a_retracted_paper() -> None:
    world = await make_world(sources=SOURCES, retracted=("trial",))
    claim = claim_args(world, "Drug X reduced mortality.", "trial")

    def statement() -> str:
        return statement_id_from(client, 1)

    client = FakeClaude(
        reply(tool("record_claim", **claim), stop="tool_use"),
        lambda r: reply(tool("check_conclusion", statement_id=statement()), stop="tool_use"),
        lambda r: reply(
            tool("finalize_conclusion", statement_id=statement(), certainty="established"),
            stop="tool_use",
        ),
        lambda r: reply(
            tool("finalize_conclusion", statement_id=statement(), certainty="hypothesis"),
            stop="tool_use",
        ),
        reply(text("A hypothesis only: the supporting paper was retracted.")),
    )
    await execute_run(world.run_id, client=client)

    run = await run_of(world)
    assert run.status == "succeeded" and run.certainty == "hypothesis"
    finals = [e.payload["result"] for e in await events_of(world) if e.type == "finalization"]
    assert [f["accepted"] for f in finals] == [False, True]
    assert finals[0]["obligations"][0]["kind"] == "invalidated_source"
    assert finals[1]["caveats"][0]["kind"] == "invalidated_source"
    checked = next(e.payload["result"] for e in await events_of(world) if e.type == "check")
    assert checked["can_finalize_as"] == ["hypothesis"]
    assert "retracted" in run.final_report


async def test_an_abstention_ends_the_run_with_its_reason() -> None:
    world = await make_world(sources=SOURCES)
    client = FakeClaude(
        reply(tool("abstain", reason="Only a mouse study exists."), stop="tool_use"),
        reply(text("I cannot conclude anything.")),
    )
    await execute_run(world.run_id, client=client)
    run = await run_of(world)
    assert run.status == "succeeded" and run.certainty == "abstained"
    assert run.final_report.startswith("No conclusion. Only a mouse study exists.")


async def test_a_baseline_run_has_no_guard_tools_and_just_reports() -> None:
    world = await make_world(mode="baseline", sources=SOURCES)
    client = FakeClaude(reply(text("Drug X works. Sources: Trial of X.")))
    await execute_run(world.run_id, client=client)

    run = await run_of(world)
    assert run.status == "succeeded" and run.final_report == "Drug X works. Sources: Trial of X."
    assert run.certainty is None
    names = [t["name"] for t in client.requests[0]["tools"]]
    assert names == ["search_papers", "read_paper", "fetch_url", "read_source", "web_search"]
    assert "check_conclusion" not in client.requests[0]["system"]


async def test_the_agent_is_nudged_to_conclude_then_the_run_fails() -> None:
    world = await make_world(sources=SOURCES)
    client = FakeClaude(
        reply(text("Done!")), reply(text("Really done.")), reply(text("Still done."))
    )
    await execute_run(world.run_id, client=client)

    run = await run_of(world)
    assert (
        run.status == "failed"
        and "without a conclusion" in run.error
        and run.final_report == "Still done."
    )
    events = await events_of(world)
    assert types(events).count("nudge") == 2
    nudge = client.requests[1]["messages"][-1]
    assert nudge["role"] == "user" and "have not reached a conclusion" in nudge["content"]


async def test_bad_tool_calls_are_errors_the_agent_sees_and_the_run_continues() -> None:
    world = await make_world(sources=SOURCES)
    bad = claim_args(world, "x", "trial", excerpt_ids=["not-an-id"])
    client = FakeClaude(
        reply(
            tool("record_claim", **bad),
            tool("read_source", source_id="nope", offset=0),
            stop="tool_use",
        ),
        reply(tool("abstain", reason="I could not record anything."), stop="tool_use"),
        reply(text("Giving up.")),
    )
    await execute_run(world.run_id, client=client)

    results = client.tool_results(1)
    assert len(results) == 2 and all(
        r["is_error"] for r in results
    )  # parallel calls answered together
    assert "must be an id" in results[0]["content"]
    assert (await run_of(world)).status == "succeeded"


# -- limits and failures -----------------------------------------------------------------------


async def test_the_turn_budget_stops_a_run_that_never_finishes() -> None:
    world = await make_world(
        sources=SOURCES,
        budgets={"max_turns": 3, "max_web_searches": 0, "max_total_output_tokens": 10**6},
    )

    def spin(request):
        return reply(tool("read_source", source_id="nope", offset=0), stop="tool_use")

    client = FakeClaude(spin, spin, spin)
    await execute_run(world.run_id, client=client)

    run = await run_of(world)
    assert run.status == "budget_exhausted" and "budget" in run.error
    assert len(client.requests) == 3
    assert all(
        t["name"] != "web_search" for t in client.requests[0]["tools"]
    )  # 0 searches: no search tool


async def test_the_output_token_budget_stops_a_run() -> None:
    world = await make_world(
        sources=SOURCES,
        budgets={"max_turns": 10, "max_web_searches": 0, "max_total_output_tokens": 500},
    )

    def spin(request):
        return reply(
            tool("read_source", source_id="nope", offset=0), stop="tool_use", tokens_out=400
        )

    await execute_run(world.run_id, client=FakeClaude(spin, spin, spin))
    run = await run_of(world)
    assert run.status == "budget_exhausted" and run.usage["turns"] == 2


@pytest.mark.parametrize(
    ("stop", "expected"),
    [("refusal", "declined to continue"), ("max_tokens", "cut off")],
)
async def test_refusals_and_truncation_fail_the_run_with_a_reason(stop, expected) -> None:
    world = await make_world(sources=SOURCES)
    await execute_run(world.run_id, client=FakeClaude(reply(text("..."), stop=stop)))
    run = await run_of(world)
    assert run.status == "failed" and expected in run.error


async def test_a_paused_server_tool_turn_is_sent_back_and_continued() -> None:
    world = await make_world(mode="baseline", sources=SOURCES)
    paused = reply(*web_search("q", "https://a.example"), stop="pause_turn")
    client = FakeClaude(paused, reply(text("Answer after the search.")))
    await execute_run(world.run_id, client=client)

    assert (await run_of(world)).final_report == "Answer after the search."
    resumed = client.requests[1]["messages"]
    assert resumed[-1]["role"] == "assistant" and resumed[-1]["content"] == paused.content


async def test_a_cancelled_run_stops_without_another_model_call() -> None:
    world = await make_world(sources=SOURCES)

    async def cancel_during_turn(request):
        async with session_factory() as session:
            run = await session.get(AgentRun, world.run_id)
            run.status = "cancelled"
            await session.commit()
        return reply(tool("read_source", source_id="nope", offset=0), stop="tool_use")

    client = FakeClaude(cancel_during_turn, reply(text("should never be asked")))
    await execute_run(world.run_id, client=client)

    assert (await run_of(world)).status == "cancelled" and len(client.requests) == 1


async def test_a_claude_api_failure_is_recorded_not_raised() -> None:
    world = await make_world(sources=SOURCES)

    class Broken:
        class messages:  # noqa: N801
            @staticmethod
            async def create(**request):
                raise anthropic.APIConnectionError(
                    request=httpx.Request("POST", "https://api.anthropic.com")
                )

    await execute_run(world.run_id, client=Broken())
    run = await run_of(world)
    assert run.status == "failed" and "Claude request failed" in run.error
    assert types(await events_of(world))[-1] == "run_finished"


async def test_an_unexpected_crash_still_ends_in_a_recorded_state() -> None:
    world = await make_world(sources=SOURCES)

    class Crashing:
        class messages:  # noqa: N801
            @staticmethod
            async def create(**request):
                raise RuntimeError("boom")

    await execute_run(world.run_id, client=Crashing())
    run = await run_of(world)
    assert run.status == "failed" and "RuntimeError: boom" in run.error


async def test_a_run_that_is_not_running_is_left_alone() -> None:
    world = await make_world(sources=SOURCES)
    async with session_factory() as session:
        run = await session.get(AgentRun, world.run_id)
        run.status = "queued"
        await session.commit()
    client = FakeClaude()
    await execute_run(world.run_id, client=client)
    assert client.requests == [] and (await run_of(world)).status == "queued"


# -- helpers -----------------------------------------------------------------------------------


def test_tools_are_strict_and_the_search_tool_follows_the_budget() -> None:
    guarded = build_tools("guarded", 5)
    custom = [t for t in guarded if t["name"] != "web_search"]
    assert custom and all(
        t["strict"] is True and t["input_schema"]["additionalProperties"] is False for t in custom
    )
    assert guarded[-1] == {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}
    assert not any(t["name"] == "web_search" for t in build_tools("guarded", 0))
    assert len(build_tools("baseline", 1)) == 5 and len(build_tools("guarded", 1)) == 10


def test_cost_estimates_use_list_prices_and_cache_discounts() -> None:
    usage = {
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
        "cache_write_tokens": 1_000_000,
        "cache_read_tokens": 1_000_000,
    }
    # sonnet 5.5: $2 in, $10 out; cache write 1.25x input, cache read 0.1x input
    assert estimate_cost("claude-sonnet-5-5", usage) == pytest.approx(2 + 10 + 2.5 + 0.2)
    assert estimate_cost("some-future-model", usage) is None


async def test_the_event_log_numbers_events_and_resumes_after_the_last() -> None:
    world = await make_world()
    log = EventLog(session_factory, world.run_id)
    await log.start()
    assert [await log.add("a", {}), await log.add("b", {"long": "x" * 30_000})] == [1, 2]
    again = EventLog(session_factory, world.run_id)
    await again.start()
    assert await again.add("c", {}) == 3
    stored = {e.seq: e for e in await events_of(world)}
    assert stored[2].payload["long"].endswith("[10000 more characters]")


async def test_every_turn_is_recorded_with_its_own_usage_and_no_block_is_dropped() -> None:
    from anthropic.types import RedactedThinkingBlock, ServerToolUseBlock

    world = await make_world(mode="baseline", sources=SOURCES)
    client = FakeClaude(
        reply(
            tool("read_source", source_id="nope", offset=0),
            stop="tool_use",
            tokens_in=500,
            tokens_out=60,
        ),
        reply(
            RedactedThinkingBlock(type="redacted_thinking", data="opaque"),
            ServerToolUseBlock(
                type="server_tool_use",
                id="srvtoolu_x",
                name="code_execution",
                input={"code": "print(1)"},
            ),
            text("Answer."),
            tokens_in=700,
            tokens_out=90,
            cache_read=300,
        ),
    )
    await execute_run(world.run_id, client=client)

    events = await events_of(world)
    turns = [e.payload for e in events if e.type == "turn"]
    assert [t["turn"] for t in turns] == [1, 2]
    assert turns[0]["stop_reason"] == "tool_use" and turns[1]["stop_reason"] == "end_turn"
    assert turns[0]["model"] == "claude-sonnet-5-5" and turns[0]["latency_s"] >= 0
    assert turns[0]["usage"] == {
        "input_tokens": 500,
        "output_tokens": 60,
        "cache_write_tokens": 0,
        "cache_read_tokens": 0,
        "web_searches": 0,
    }
    assert turns[1]["usage"]["cache_read_tokens"] == 300

    redacted = next(e for e in events if e.type == "thinking")
    assert redacted.payload == {"text": None, "redacted": True}
    other = next(e for e in events if e.type == "server_block")
    assert other.payload["block"]["name"] == "code_execution"
    assert other.payload["block"]["input"] == {"code": "print(1)"}
    assert "web_search" not in types(events)  # a code-execution call is not a web search
