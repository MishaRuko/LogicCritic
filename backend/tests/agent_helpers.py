"""Shared fixtures for the research-agent tests: a scripted fake Claude and a prepared workspace."""

import inspect
import itertools
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace

from anthropic.types import (
    Message,
    ServerToolUsage,
    ServerToolUseBlock,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    Usage,
    WebSearchResultBlock,
    WebSearchToolResultBlock,
)

from app.database import session_factory
from app.models import (
    AgentRun,
    Excerpt,
    ResearchGoal,
    Source,
    SourceValidity,
    Workspace,
)

_ids = itertools.count(1)


# -- scripted Claude ---------------------------------------------------------------------------


def text(value: str) -> TextBlock:
    return TextBlock(type="text", text=value, citations=None)


def thinking(value: str) -> ThinkingBlock:
    return ThinkingBlock(type="thinking", thinking=value, signature="sig")


def tool(name: str, **arguments) -> ToolUseBlock:
    return ToolUseBlock(type="tool_use", id=f"toolu_{next(_ids)}", name=name, input=arguments)


def web_search(query: str, *urls: str) -> list:
    call_id = f"srvtoolu_{next(_ids)}"
    return [
        ServerToolUseBlock(
            type="server_tool_use", id=call_id, name="web_search", input={"query": query}
        ),
        WebSearchToolResultBlock(
            type="web_search_tool_result",
            tool_use_id=call_id,
            content=[
                WebSearchResultBlock(
                    type="web_search_result",
                    encrypted_content="x",
                    page_age=None,
                    title=f"Page at {url}",
                    url=url,
                )
                for url in urls
            ],
        ),
    ]


def reply(*blocks, stop="end_turn", tokens_in=1000, tokens_out=200, cache_read=0) -> Message:
    flat = [b for item in blocks for b in (item if isinstance(item, list) else [item])]
    searches = sum(1 for b in flat if b.type == "server_tool_use" and b.name == "web_search")
    return Message(
        id=f"msg_{next(_ids)}",
        type="message",
        role="assistant",
        model="claude-sonnet-5-5",
        stop_reason=stop,
        stop_sequence=None,
        content=flat,
        usage=Usage(
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=cache_read,
            server_tool_use=ServerToolUsage(web_search_requests=searches, web_fetch_requests=0)
            if searches
            else None,
        ),
    )


class FakeClaude:
    """Plays back prepared turns. An item may be a callable that sees the live request."""

    def __init__(self, *turns) -> None:
        self.turns = list(turns)
        self.requests: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **request):
        # The loop keeps appending to one list; keep a snapshot of what each turn was sent.
        self.requests.append({**request, "messages": list(request["messages"])})
        if not self.turns:
            raise AssertionError("the agent asked for more turns than the script has")
        turn = self.turns.pop(0)
        out = turn(request) if callable(turn) else turn
        return await out if inspect.isawaitable(out) else out

    def tool_results(self, request_index: int) -> list[dict]:
        """The tool_result blocks the agent sent back in a given request."""
        messages = self.requests[request_index]["messages"]
        last = messages[-1]
        return (
            last["content"] if last["role"] == "user" and isinstance(last["content"], list) else []
        )


# -- a prepared workspace ----------------------------------------------------------------------


@dataclass
class World:
    workspace_id: uuid.UUID
    goal_id: uuid.UUID
    run_id: uuid.UUID
    sources: dict[str, uuid.UUID] = field(default_factory=dict)
    excerpts: dict[str, list[uuid.UUID]] = field(default_factory=dict)

    def excerpt(self, source: str, index: int = 0) -> str:
        return str(self.excerpts[source][index])


async def make_world(
    *,
    mode: str = "guarded",
    criteria: list[str] | None = None,
    sources: dict[str, list[str]] | None = None,
    retracted: tuple[str, ...] = (),
    budgets: dict | None = None,
) -> World:
    """A workspace with a goal, a running agent run, and sources made of the given paragraphs."""
    async with session_factory() as session:
        workspace = Workspace(title="agent test")
        session.add(workspace)
        await session.flush()
        goal = ResearchGoal(
            workspace_id=workspace.id,
            question="Does drug X reduce mortality in adults?",
            completion_criteria=criteria or [],
            falsifiers=[],
        )
        session.add(goal)
        await session.flush()
        run = AgentRun(
            workspace_id=workspace.id,
            goal_id=goal.id,
            idempotency_key=str(uuid.uuid4()),
            mode=mode,
            model="claude-sonnet-5-5",
            status="running",
            budgets=budgets
            or {"max_turns": 12, "max_web_searches": 0, "max_total_output_tokens": 100_000},
            usage={"input_tokens": 0, "output_tokens": 0, "turns": 0, "web_searches": 0},
        )
        session.add(run)
        world = World(workspace.id, goal.id, run.id)
        await session.flush()
        world.run_id = run.id
        for name, paragraphs in (sources or {}).items():
            source = Source(
                workspace_id=workspace.id,
                kind="document",
                origin="fixture",
                title=name,
                mime_type="text/plain",
                original_filename=f"{name}.txt",
                storage_key=f"test/{uuid.uuid4()}",
                content_hash=uuid.uuid4().hex,
                external_ids={},
                metadata_={},
            )
            session.add(source)
            await session.flush()
            world.sources[name] = source.id
            world.excerpts[name] = []
            for number, body in enumerate(paragraphs):
                excerpt = Excerpt(
                    source_id=source.id, text=body, locator={"sequence": number}, sequence=number
                )
                session.add(excerpt)
                await session.flush()
                world.excerpts[name].append(excerpt.id)
            if name in retracted:
                session.add(
                    SourceValidity(
                        source_id=source.id,
                        status="invalidated",
                        reason="Retracted.",
                        provenance={"actor_type": "integration", "actor_id": "amass"},
                    )
                )
        await session.commit()
    return world


def claim_args(world: World, text_: str, source: str, *, index: int = 0, **extra) -> dict:
    """Arguments for a record_claim call with every required field present."""
    return {
        "text": text_,
        "excerpt_ids": [world.excerpt(source, index)],
        "assertion_mode": "reported",
        "role": None,
        "claim_strength": None,
        "causal_support": None,
        "scope": None,
        "criteria_satisfied": [],
        **extra,
    }


# -- a scripted judge --------------------------------------------------------------------------


class FakeJudge:
    """Stands in for the independent judge. By default it believes the agent's own tags.

    Tests that care about the judge pass `verdict` (a function of the material) or `down=True`.
    """

    model = "claude-sonnet-5"

    def __init__(self, client=None, *, verdict=None, down=False, proposed=None) -> None:
        self.verdict, self.down, self.proposed = verdict, down, proposed
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
        self.materials: list[dict] = []

    async def assess(self, material: dict):
        from app.services.claude_call import ClaudeCallFailed
        from app.services.judge import (
            CriterionVerdict,
            DesignVerdict,
            JudgeOutput,
            _tidy,
        )

        self.materials.append(material)
        if self.down:
            raise ClaudeCallFailed("the judge's model call failed (test)")
        self.usage["calls"] += 1
        self.usage["input_tokens"] += 100
        self.usage["output_tokens"] += 50
        if self.verdict:
            return _tidy(self.verdict(material), material)
        claims = material["claims"]
        criteria = [
            CriterionVerdict(
                index=i,
                met=any(i in c["criteria_hint"] for c in claims),
                rationale="Tagged by the agent.",
                supporting_statement_ids=[
                    c["statement_id"] for c in claims if i in c["criteria_hint"]
                ],
            )
            for i in range(len(material["criteria"]))
        ]
        designs = [
            DesignVerdict(statement_id=c["statement_id"], design_shown=True, rationale="ok")
            for c in claims
            if c["declared_design"]
        ]
        return JudgeOutput(criteria=criteria, designs=designs)

    async def propose_criteria(self, question: str, kind: str):
        from app.services.claude_call import ClaudeCallFailed
        from app.services.judge import ProposedCriteria

        if self.down:
            raise ClaudeCallFailed("down")
        self.usage["calls"] += 1
        self.usage["input_tokens"] += 100
        self.usage["output_tokens"] += 50
        return self.proposed or ProposedCriteria(
            completion_criteria=["Direct randomised evidence", "Opposing evidence searched for"],
            falsifiers=["A large trial showing the opposite"],
        )
