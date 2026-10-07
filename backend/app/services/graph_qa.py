"""Answer questions about a workspace's argument graph by querying it.

Claude gets read-only graph tools and must end with `answer`, naming the claims and reasoning
steps its answer rests on so the interface can highlight that part of the logical chain. It
answers from the graph alone: it does not research and never writes to the graph.
"""

import json
import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.loop import estimate_cost
from app.agent.tool_models import GRAPH_TOOLS
from app.config import get_settings
from app.services import graph_query
from app.services.claude_tools import strict_tool

MAX_TURNS = 8
RESULT_CHARS = 12_000


class GraphAnswer(BaseModel):
    answer: str = Field(
        description="The answer for the user, in plain words. Refer to claims by what they say, "
        "never by id. Say plainly when the graph does not contain what the question needs."
    )
    relevant_statement_ids: list[str] = Field(
        description="statement_ids of the claims the answer rests on, in chain order."
    )
    relevant_step_ids: list[str] = Field(
        description="step_ids of the reasoning steps that connect those claims."
    )


SYSTEM = """You answer questions about a research argument graph: claims (statements) grounded in
source excerpts, reasoning steps that derive conclusions from premises, cross-source links and open
obligations (gaps a verifier found). Query the graph with the tools; do not rely on memory or \
outside
knowledge, and do not speculate beyond what the graph records. Start with graph_overview or
search_graph, then read claims with get_graph_node and follow the argument with trace_chain.
When asked why something holds or whether it is supported, trace its support chain and report
weak links: unsupported premises, open obligations, rebuttals. Be concise. Write plain text with
short paragraphs or '- ' bullets; no Markdown headings or bold. Finish by calling
`answer` with the claims and steps your answer rests on, so they can be highlighted."""


def _tools() -> list[dict]:
    tools = [
        strict_tool(name, description, model, strict=False)
        for name, (description, model) in GRAPH_TOOLS.items()
    ]
    tools.append(
        strict_tool("answer", "Give the final answer and the graph nodes it rests on.", GraphAnswer)
    )
    return tools


async def ask(
    sessions: async_sessionmaker[AsyncSession],
    workspace_id: uuid.UUID,
    question: str,
    history: list[dict],
    client: Any,
) -> dict:
    settings = get_settings()
    model = settings.agent_model
    messages: list[dict] = []
    for turn in history[-6:]:
        messages += [
            {"role": "user", "content": turn["question"]},
            {"role": "assistant", "content": turn["answer"]},
        ]
    messages.append({"role": "user", "content": question})
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
    inspected: list[str] = []
    tools = _tools()
    final: GraphAnswer | None = None
    for turn in range(MAX_TURNS):
        request: dict[str, Any] = {
            "model": model,
            "max_tokens": 4096,
            "system": SYSTEM,
            "tools": tools,
            "messages": messages,
        }
        if turn == MAX_TURNS - 1:
            request["tool_choice"] = {"type": "tool", "name": "answer"}
        response = await client.messages.create(**request)
        u = response.usage
        usage["input_tokens"] += getattr(u, "input_tokens", 0) or 0
        usage["output_tokens"] += getattr(u, "output_tokens", 0) or 0
        usage["cache_read_tokens"] += getattr(u, "cache_read_input_tokens", 0) or 0
        usage["cache_write_tokens"] += getattr(u, "cache_creation_input_tokens", 0) or 0
        calls = [b for b in response.content if b.type == "tool_use"]
        done = next((b for b in calls if b.name == "answer"), None)
        if done is not None:
            final = GraphAnswer.model_validate(dict(done.input))
            break
        if not calls:
            text = "".join(b.text for b in response.content if b.type == "text").strip()
            final = GraphAnswer(
                answer=text or "I could not answer from the graph.",
                relevant_statement_ids=[],
                relevant_step_ids=[],
            )
            break
        messages.append({"role": "assistant", "content": response.content})
        results = []
        async with sessions() as session:
            for call in calls:
                args = dict(call.input)
                result = await graph_query.run_tool(session, workspace_id, call.name, args)
                for key in ("node_id", "statement_id"):
                    if args.get(key):
                        inspected.append(str(args[key]))
                content = json.dumps(result, ensure_ascii=False, default=str)
                if len(content) > RESULT_CHARS:
                    content = content[:RESULT_CHARS] + '..."truncated"'
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": content,
                        "is_error": "error" in result,
                    }
                )
        messages.append({"role": "user", "content": results})
    if final is None:  # pragma: no cover - the last turn forces `answer`
        final = GraphAnswer(
            answer="I could not answer from the graph.",
            relevant_statement_ids=[],
            relevant_step_ids=[],
        )
    async with sessions() as session:
        claims, steps = await graph_query.known_ids(
            session, workspace_id, [*final.relevant_statement_ids, *final.relevant_step_ids]
        )
        if not claims and not steps and inspected:
            # The model forgot to name its evidence: fall back to what it actually read.
            claims, steps = await graph_query.known_ids(session, workspace_id, inspected)
    return {
        "answer": final.answer.strip(),
        "statement_ids": claims,
        "step_ids": steps,
        "model": model,
        "usage": {**usage, "cost_usd": estimate_cost(model, usage)},
    }
