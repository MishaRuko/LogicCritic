"""The live research loop: Claude, tools, a guardrail and a recorded trace.

The loop is written by hand (not a prebuilt runner) because every step has to be recorded as it
happens, the guardrail sits between the model and its final answer, and each run has a budget.
"""

import logging
import re
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import anthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent import assurance, prompts
from app.agent.conversation import conversation_messages, workspace_context
from app.agent.runs import EventLog
from app.agent.tool_models import GRAPH_TOOLS, GUARD_TOOLS, RECORDING_TOOLS, RESEARCH_TOOLS
from app.agent.toolbox import Toolbox, tool_result_text
from app.config import get_settings
from app.database import session_factory
from app.models import AgentEvent, AgentRun, ResearchGoal, Statement
from app.services.amass import AmassNotConfigured, get_amass_client
from app.services.claude_call import ClaudeCallFailed
from app.services.claude_errors import describe_claude_failure
from app.services.claude_tools import strict_tool
from app.services.judge import DEFAULT_CRITERIA, Judge

log = logging.getLogger(__name__)

# USD per million tokens (input, output), from Anthropic's published list prices.
PRICES = {
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}
CACHE_WRITE_FACTOR, CACHE_READ_FACTOR = 1.25, 0.1
MAX_NUDGES = 2
LOOSE_TOOLS = {"link_claims"}
TERMINAL_TOOLS = {"finalize_conclusion", "abstain"}


def build_tools(mode: str, max_web_searches: int) -> list[dict]:
    # The basic web search tool: the newer version lets the model call search from inside a code
    # cell, where it mis-shaped the arguments and every search failed.
    specs = dict(RESEARCH_TOOLS)
    if mode == "guarded":
        specs |= RECORDING_TOOLS | GUARD_TOOLS
    tools: list[dict[str, Any]] = [
        strict_tool(name, desc, model) for name, (desc, model) in specs.items() if name not in LOOSE_TOOLS
    ]
    # The API caps the combined grammar of strict tools. Tools with flat inputs that the toolbox
    # validates itself go without `strict`, so the whole set stays under that cap.
    loose = {**GRAPH_TOOLS, **{k: v for k, v in specs.items() if k in LOOSE_TOOLS}}
    tools += [{**strict_tool(name, desc, model), "strict": False} for name, (desc, model) in loose.items()]
    if max_web_searches > 0:
        tools.append(
            {"type": "web_search_20250305", "name": "web_search", "max_uses": max_web_searches}
        )
    return tools


def estimate_cost(model: str, usage: dict) -> float | None:
    if model not in PRICES:
        return None
    price_in, price_out = PRICES[model]
    return round(
        (
            usage["input_tokens"] * price_in
            + usage.get("cache_write_tokens", 0) * price_in * CACHE_WRITE_FACTOR
            + usage.get("cache_read_tokens", 0) * price_in * CACHE_READ_FACTOR
            + usage["output_tokens"] * price_out
        )
        / 1_000_000,
        4,
    )


async def execute_run(
    run_id: uuid.UUID,
    *,
    sessions: async_sessionmaker[AsyncSession] = session_factory,
    client: Any = None,
    amass: Any = None,
    judge: Judge | None = None,
) -> None:
    settings = get_settings()
    async with sessions() as session:
        run = await session.get(AgentRun, run_id)
        if run is None or run.status != "running":
            return
        goal = await session.get(ResearchGoal, run.goal_id)
        mode, model, budgets = run.mode, run.model, dict(run.budgets)
    log_ = EventLog(sessions, run_id)
    await log_.start()
    await log_.add("run_started", {"mode": mode, "model": model, "budgets": budgets})

    client = client or anthropic.AsyncAnthropic(api_key=settings.claude_api_key)
    if amass is None:
        try:
            amass = get_amass_client()
        except AmassNotConfigured:
            amass = None
    if mode != "guarded":
        judge = None
    elif judge is None:
        judge = Judge(client)
    toolbox = Toolbox(sessions, run_id, amass, judge)
    state = _State(run_id, sessions, log_, toolbox, mode, model, budgets, judge)
    try:
        if state.assurance is not None:
            await log_.add("assurance", state.assurance)
        if judge is not None:
            await _settle_criteria(state, goal, judge)
        await _drive(state, client, goal)
    except anthropic.APIError as error:
        await state.finish(
            "failed", error=f"Claude request failed. {describe_claude_failure(error)}"
        )
    except Exception as error:  # noqa: BLE001 - a run must always end in a recorded state
        log.exception("agent run %s crashed", run_id)
        await state.finish("failed", error=f"The run crashed: {type(error).__name__}: {error}")


class _State:
    def __init__(self, run_id, sessions, events, toolbox, mode, model, budgets, judge) -> None:
        self.run_id, self.sessions, self.events, self.toolbox = run_id, sessions, events, toolbox
        self.judge = judge
        self.mode, self.model, self.budgets = mode, model, budgets
        self.usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_write_tokens": 0,
            "cache_read_tokens": 0,
            "turns": 0,
            "web_searches": 0,
        }
        self.finished = False
        # Only a guarded run has a verifier to say how settled the answer is.
        self.assurance = assurance.unexplored().as_dict() if mode == "guarded" else None

    async def tick(self, response: Any) -> dict[str, int]:
        """Add this turn's usage to the run's totals, and return the turn's own numbers."""
        u = response.usage
        server = getattr(u, "server_tool_use", None)
        turn = {
            "input_tokens": getattr(u, "input_tokens", 0) or 0,
            "output_tokens": getattr(u, "output_tokens", 0) or 0,
            "cache_write_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
            "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
            "web_searches": getattr(server, "web_search_requests", 0) or 0,
        }
        self.usage["turns"] += 1
        for name, value in turn.items():
            self.usage[name] += value
        await self._save(heartbeat=True)
        return turn

    async def _save(self, heartbeat: bool = False, **fields: Any) -> AgentRun:
        async with self.sessions() as session:
            run = await session.scalar(
                select(AgentRun).where(AgentRun.id == self.run_id).with_for_update()
            )
            run.usage = {**run.usage, **self.usage, **self._cost(), "assurance": self.assurance}
            if heartbeat:
                run.heartbeat_at = datetime.now(UTC)
            for name, value in fields.items():
                if run.status != "cancelled":
                    setattr(run, name, value)
            await session.commit()
            return run

    def _cost(self) -> dict[str, Any]:
        """The agent's cost, plus the judge's, which is a separate model call."""
        cost = estimate_cost(self.model, self.usage)
        if self.judge is None or not self.judge.usage["calls"]:
            return {"cost_usd": cost}
        spent = {"input_tokens": 0, "output_tokens": 0} | {
            k: v for k, v in self.judge.usage.items() if k != "calls"
        }
        extra = estimate_cost(self.judge.model, spent)
        total = None if cost is None or extra is None else round(cost + extra, 4)
        return {"cost_usd": total, "judge": dict(self.judge.usage), "judge_cost_usd": extra}

    async def set_assurance(self, new: dict) -> None:
        """Record a change in how settled the answer is, as an event and on the run."""
        if self.assurance is None or (new["level"], new["holding_back"]) == (
            self.assurance["level"],
            self.assurance["holding_back"],
        ):
            return
        self.assurance = new
        await self.events.add("assurance", new)
        await self._save()

    async def beat(self) -> None:
        await self._save(heartbeat=True)

    async def cancelled(self) -> bool:
        async with self.sessions() as session:
            run = await session.get(AgentRun, self.run_id)
            return run.status == "cancelled"

    async def finish(
        self, status: str, *, error: str | None = None, report: str | None = None
    ) -> None:
        if self.finished:
            return
        self.finished = True
        fields: dict[str, Any] = {"status": status, "completed_at": datetime.now(UTC)}
        if error:
            fields["error"] = error
        if report is not None:
            fields["final_report"] = report
        run = await self._save(**fields)
        await self.events.add(
            "run_finished",
            {"status": run.status, "error": error, "certainty": run.certainty, "usage": run.usage},
        )


async def _settle_criteria(state: _State, goal: ResearchGoal, judge: Judge) -> None:
    """Hold the run to a standard of evidence: the caller's criteria, or ones proposed up front.

    Proposed before any research, so they cannot be shaped to fit what the agent later finds.
    If they cannot be proposed, generic criteria apply: the gate is never left open.
    """
    if goal.completion_criteria:
        await state.events.add("criteria_given", {"criteria": list(goal.completion_criteria)})
        return
    try:
        proposed = await judge.propose_criteria(goal.question, goal.kind)
        criteria = [c.strip() for c in proposed.completion_criteria if c.strip()][:6]
        falsifiers = [f.strip() for f in proposed.falsifiers if f.strip()][:3]
        source = "criteria_proposed"
    except ClaudeCallFailed:
        criteria, falsifiers, source = list(DEFAULT_CRITERIA), [], "criteria_default"
    criteria = criteria or list(DEFAULT_CRITERIA)
    async with state.sessions() as session:
        row = await session.get(ResearchGoal, goal.id)
        row.completion_criteria = criteria
        if not row.falsifiers:
            row.falsifiers = falsifiers
        await session.commit()
        goal.completion_criteria, goal.falsifiers = row.completion_criteria, row.falsifiers
    await state.events.add(source, {"criteria": criteria, "falsifiers": goal.falsifiers})
    await state.beat()


async def _drive(state: _State, client: Any, goal: ResearchGoal) -> None:
    settings = get_settings()
    budgets = state.budgets
    async with state.sessions() as session:
        messages = await conversation_messages(session, state.run_id)
        context = await workspace_context(session, state.run_id)
    messages.append(
        {
            "role": "user",
            "content": prompts.opening_message(
                goal, state.mode, budgets["max_turns"], budgets["max_web_searches"]
            ) + context,
        }
    )
    tools = build_tools(state.mode, budgets["max_web_searches"])
    system = prompts.system_prompt(state.mode)
    nudges = 0
    concluded = False  # a finalize or abstain was accepted; the next end_turn is the answer

    for _ in range(budgets["max_turns"]):
        if await state.cancelled():
            return
        if state.usage["output_tokens"] >= budgets["max_total_output_tokens"]:
            break
        request: dict[str, Any] = {
            "model": state.model,
            "max_tokens": budgets.get("turn_max_tokens") or settings.agent_turn_max_tokens,
            "system": system,
            "tools": tools,
            "messages": messages,
            "cache_control": {"type": "ephemeral"},
            "thinking": {"type": "adaptive", "display": "summarized"},
        }
        effort = budgets.get("effort", settings.agent_effort)
        if effort:
            request["output_config"] = {"effort": effort}
        started = time.monotonic()
        response = await client.messages.create(**request)
        latency = time.monotonic() - started
        turn_usage = await state.tick(response)
        if await state.cancelled():
            return
        await state.events.add(
            "turn",
            {
                "turn": state.usage["turns"],
                "stop_reason": response.stop_reason,
                "model": getattr(response, "model", None),
                "latency_s": round(latency, 2),
                "usage": turn_usage,
            },
        )
        await _record_blocks(state, response)
        messages.append({"role": "assistant", "content": response.content})

        reason = response.stop_reason
        if reason == "refusal":
            category = getattr(getattr(response, "stop_details", None), "category", None)
            await state.finish(
                "failed", error=f"Claude declined to continue (category: {category})."
            )
            return
        if reason == "max_tokens":
            await state.finish("failed", error="Claude's answer was cut off mid-turn.")
            return
        if reason == "pause_turn":
            continue  # a server-side tool (web search) is mid-flight: send it back unchanged
        if reason == "tool_use":
            results, concluded_now = await _run_tools(state, response)
            concluded = concluded or concluded_now
            if concluded:
                await _complete(state, "")
                return
            messages.append({"role": "user", "content": results})
            continue

        # end_turn: the agent has spoken.
        text = _text_of(response)
        if state.mode == "baseline" or concluded:
            await _complete(state, text)
            return
        if nudges >= MAX_NUDGES:
            await state.finish(
                "failed",
                error="The agent ended its turn without a conclusion or an abstention.",
                report=text,
            )
            return
        nudges += 1
        await state.events.add("nudge", {"reason": "no conclusion yet", "count": nudges})
        messages.append(
            {
                "role": "user",
                "content": "You have not reached a conclusion yet. Record your conclusion, call "
                "check_conclusion, then finalize_conclusion, or abstain if the evidence does not "
                "support one.",
            }
        )

    await state.finish(
        "budget_exhausted", error="The turn or token budget ran out before an answer."
    )


async def _run_tools(state: _State, response: Any) -> tuple[list[dict], bool]:
    results, concluded = [], False
    for block in response.content:
        if await state.cancelled():
            break
        if block.type != "tool_use":
            continue
        result = await state.toolbox.call(block.name, dict(block.input), block.id)
        is_error = "error" in result
        results.append(
            {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": tool_result_text(result),
                "is_error": is_error,
            }
        )
        await state.beat()  # a slow check must not make a live run look abandoned
        kind = {"check_conclusion": "check", "finalize_conclusion": "finalization"}.get(block.name)
        await state.events.add(
            kind or "tool_result",
            {"tool_use_id": block.id, "name": block.name, "result": result, "is_error": is_error},
        )
        change = None if is_error else _graph_change(block.name, dict(block.input), result)
        if change:
            await state.events.add("graph_change", change)
        if not is_error:
            await _update_assurance(state, block.name, dict(block.input), result)
        if block.name in TERMINAL_TOOLS and result.get("accepted"):
            concluded = True
    return results, concluded


async def _update_assurance(state: _State, tool: str, args: dict, result: dict) -> None:
    level = (state.assurance or {}).get("level")
    if tool == "record_claim" and args.get("role") == "conclusion" and level == "unexplored":
        await state.set_assurance(assurance.exploring().as_dict())
    elif tool == "check_conclusion" and "assurance" in result:
        await state.set_assurance(result["assurance"])
    elif tool == "finalize_conclusion" and result.get("accepted"):
        if result.get("certainty") == "established":
            await state.set_assurance(assurance.Assurance("settled", []).as_dict())


def _graph_change(tool: str, args: dict, result: dict) -> dict | None:
    """What a successful recording tool did to the graph, for a live or replayed view of it."""
    if tool == "record_claim":
        return {
            "change": "claim_added",
            "statement_id": result["statement_id"],
            "text": args.get("text"),
            "role": args.get("role"),
            "claim_strength": args.get("claim_strength"),
            "excerpt_ids": args.get("excerpt_ids"),
            "position": args.get("role") == "conclusion",
        }
    if tool == "record_reasoning":
        return {
            "change": "step_added",
            "step_id": result["step_id"],
            "premise_ids": args.get("premise_ids"),
            "conclusion_id": args.get("conclusion_id"),
            "revises_step_id": args.get("revises_step_id"),
        }
    if tool == "link_claims" and result.get("linked"):
        return {
            "change": "link_added",
            "relation_id": result["relation_id"],
            "relation": args.get("relation"),
            "source_statement_id": args.get("source_statement_id"),
            "target_statement_id": args.get("target_statement_id"),
            "audit_verdict": result.get("audit_verdict"),
        }
    if tool == "revise_claim":
        return {
            "change": "claim_superseded" if result.get("replaced_by") else "claim_withdrawn",
            "statement_id": result["withdrawn"],
            "replaced_by_id": result.get("replaced_by"),
            "reason": args.get("reason"),
            "position": result.get("role") == "conclusion",
        }
    return None


async def _record_blocks(state: _State, response: Any) -> None:
    """Write what the model produced this turn into the trace, leaving nothing out."""
    for block in response.content:
        kind = block.type
        if kind == "thinking":
            if getattr(block, "thinking", ""):
                await state.events.add("thinking", {"text": block.thinking})
        elif kind == "redacted_thinking":
            await state.events.add("thinking", {"text": None, "redacted": True})
        elif kind == "text":
            if block.text.strip():
                await state.events.add("assistant_text", {"text": block.text})
        elif kind == "tool_use":
            await state.events.add(
                "tool_call",
                {"tool_use_id": block.id, "name": block.name, "input": dict(block.input)},
            )
        elif kind == "server_tool_use" and block.name == "web_search":
            await state.events.add(
                "web_search", {"query": dict(block.input).get("query"), "tool_use_id": block.id}
            )
        elif kind == "web_search_tool_result":
            content = block.content
            if isinstance(content, list):
                found = [
                    {"url": getattr(i, "url", None), "title": getattr(i, "title", None)}
                    for i in content
                ]
                await state.events.add("web_results", {"results": found})
            else:
                await state.events.add(
                    "web_results",
                    {"error": getattr(content, "error_code", "unavailable"), "results": []},
                )
        else:
            # Server-side tools beyond web search (code execution used to filter results, and
            # whatever comes next) are recorded as they are, so the trace stays complete.
            await state.events.add("server_block", {"block": block.model_dump(mode="json")})


def _text_of(response: Any) -> str:
    return "\n".join(
        b.text for b in response.content if b.type == "text" and b.text.strip()
    ).strip()


async def _complete(state: _State, text: str) -> None:
    run = await state._save()
    report = text
    if state.mode == "guarded" and run.certainty and run.certainty != "abstained":
        async with state.sessions() as session:
            conclusion = await session.get(Statement, run.final_statement_id)
            finalizations = list(
                await session.scalars(
                    select(AgentEvent)
                    .where(AgentEvent.run_id == run.id, AgentEvent.type == "finalization")
                    .order_by(AgentEvent.seq.desc())
                )
            )
        accepted = next(
            (
                event.payload["result"]
                for event in finalizations
                if (event.payload or {}).get("result", {}).get("accepted")
            ),
            {},
        )
        report = conclusion.text if conclusion else "Verified conclusion unavailable."
        verdict = re.sub(r"^\W*verdict\W*", "", accepted.get("verdict") or "", flags=re.I).strip()
        if verdict:
            report = f"Verdict: {verdict}\n\n{report}"
        limitations = public_caveats(accepted.get("caveats", []))
        if limitations:
            report += "\n\nLimitations:\n" + "\n".join(f"- {item}" for item in limitations)
    elif run.certainty == "abstained" and run.final_report:
        report = run.final_report
    await state.finish("succeeded", report=report)


PUBLIC_CAVEATS = {
    "missing_premise": "Part of the reasoning relies on a premise the cited passages do not state.",
    "unreasoned_conclusion": "The conclusion was not formally derived from the recorded claims.",
    "causal_design_not_shown": "A causal claim rests on a study design the cited text does not show.",
    "withdrawn_premise": "Part of the reasoning uses a claim that was later withdrawn.",
    "judge_unavailable": "The independent evidence check could not run.",
}


def public_caveats(caveats: list[dict]) -> list[str]:
    """Turn internal verifier obligations into short, deduplicated user-facing limitations."""
    lines: list[str] = []
    for item in caveats:
        kind = item.get("kind")
        description = (item.get("description") or "").strip()
        if kind == "unmet_criteria":
            # Internal checklists: they already set the certainty, and the trace keeps them in
            # full. Restating them tells a reader nothing about the evidence.
            continue
        if kind in PUBLIC_CAVEATS:
            lines.append(PUBLIC_CAVEATS[kind])
        elif description:
            lines.append(_shorten(description.split("Reviewer:", 1)[0]))
    return list(dict.fromkeys(lines))


def _shorten(text: str, limit: int = 160) -> str:
    text = " ".join(text.split()).rstrip(" .")
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
