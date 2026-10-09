"""Creating, claiming and recording agent runs."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.database import session_factory
from app.models import AgentEvent, AgentRun, ResearchGoal, Workspace
from app.schemas import AgentRunCreate

MAX_PAYLOAD_CHARS = 20_000
MAX_TOTAL_OUTPUT_TOKENS = 150_000
STALE_AFTER = timedelta(minutes=5)


def default_budgets(payload: AgentRunCreate) -> dict:
    settings = get_settings()
    thorough = payload.depth == "thorough"
    return {
        "depth": payload.depth,
        "max_turns": payload.max_turns
        or (settings.agent_thorough_max_turns if thorough else settings.agent_max_turns),
        "max_web_searches": (
            (
                settings.agent_thorough_max_web_searches
                if thorough
                else settings.agent_max_web_searches
            )
            if payload.max_web_searches is None
            else payload.max_web_searches
        ),
        "max_total_output_tokens": MAX_TOTAL_OUTPUT_TOKENS * (2 if thorough else 1),
        # What the guard asks for once before a thorough answer is final.
        "min_sources": settings.agent_thorough_min_sources if thorough else 0,
        "min_searches": settings.agent_thorough_min_searches if thorough else 0,
        "min_web_searches": settings.agent_thorough_min_web_searches if thorough else 1,
    }


async def create_run(
    session: AsyncSession, workspace_id: uuid.UUID, payload: AgentRunCreate
) -> AgentRun:
    """Create a goal and a queued run. The same idempotency key returns the original run."""
    existing = await _by_key(session, workspace_id, payload.idempotency_key)
    if existing is not None:
        return existing
    goal = ResearchGoal(
        workspace_id=workspace_id,
        question=payload.question.strip(),
        kind=payload.kind,
        completion_criteria=[c.strip() for c in payload.completion_criteria if c.strip()],
        falsifiers=[f.strip() for f in payload.falsifiers if f.strip()],
    )
    session.add(goal)
    await session.flush()
    run = AgentRun(
        workspace_id=workspace_id,
        goal_id=goal.id,
        idempotency_key=payload.idempotency_key,
        mode=payload.mode,
        model=payload.model or get_settings().agent_model,
        budgets=default_budgets(payload),
        usage={"input_tokens": 0, "output_tokens": 0, "turns": 0, "web_searches": 0},
    )
    session.add(run)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await _by_key(session, workspace_id, payload.idempotency_key)
        if existing is None:
            raise
        return existing
    await session.refresh(run)
    return run


async def _by_key(session: AsyncSession, workspace_id: uuid.UUID, key: str) -> AgentRun | None:
    return await session.scalar(
        select(AgentRun).where(
            AgentRun.workspace_id == workspace_id, AgentRun.idempotency_key == key
        )
    )


async def claim_next_agent_run() -> uuid.UUID | None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        async with session.begin():
            queued = (
                select(AgentRun.created_at)
                .where(AgentRun.workspace_id == Workspace.id, AgentRun.status == "queued")
                .order_by(AgentRun.created_at, AgentRun.id)
                .limit(1)
                .correlate(Workspace)
                .scalar_subquery()
            )
            running = (
                select(AgentRun.id)
                .where(AgentRun.workspace_id == Workspace.id, AgentRun.status == "running")
                .correlate(Workspace)
                .exists()
            )
            # Lock the workspace so two workers cannot race to claim separate follow-ups.
            workspace_id = await session.scalar(
                select(Workspace.id)
                .where(queued.is_not(None), ~running)
                .order_by(queued, Workspace.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if workspace_id is None:
                return None
            if await session.scalar(
                select(AgentRun.id)
                .where(AgentRun.workspace_id == workspace_id, AgentRun.status == "running")
                .limit(1)
            ):
                return None
            run = await session.scalar(
                select(AgentRun)
                .where(AgentRun.status == "queued", AgentRun.workspace_id == workspace_id)
                .order_by(AgentRun.created_at, AgentRun.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if run is None:
                return None
            run.status = "running"
            run.started_at = now
            run.heartbeat_at = now
            return run.id


async def recover_stale_agent_runs() -> int:
    """A run whose worker disappeared cannot be resumed (its conversation lived in memory)."""
    cutoff = datetime.now(UTC) - STALE_AFTER
    async with session_factory() as session:
        stale = list(
            await session.scalars(
                select(AgentRun)
                .where(
                    AgentRun.status == "running",
                    or_(AgentRun.heartbeat_at < cutoff, AgentRun.heartbeat_at.is_(None)),
                )
                .with_for_update(skip_locked=True)
            )
        )
        for run in stale:
            run.status = "failed"
            run.error = "The worker running this was lost before it finished."
            run.completed_at = datetime.now(UTC)
        if stale:
            await session.commit()
        return len(stale)


async def cancel_run(session: AsyncSession, run: AgentRun) -> AgentRun:
    if run.status in {"queued", "running"}:
        run.status = "cancelled"
        run.completed_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(run)
    return run


class EventLog:
    """Appends numbered events to a run's trace. One writer per run."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], run_id: uuid.UUID) -> None:
        self._sessions = sessions
        self._run_id = run_id
        self._seq = 0

    async def start(self) -> None:
        async with self._sessions() as session:
            self._seq = (
                await session.scalar(
                    select(func.max(AgentEvent.seq)).where(AgentEvent.run_id == self._run_id)
                )
                or 0
            )

    async def add(self, type_: str, payload: dict[str, Any]) -> int:
        self._seq += 1
        async with self._sessions() as session:
            session.add(
                AgentEvent(
                    run_id=self._run_id, seq=self._seq, type=type_, payload=_bounded(payload)
                )
            )
            await session.commit()
        return self._seq


def _bounded(value: Any, limit: int = MAX_PAYLOAD_CHARS) -> Any:
    """Keep stored payloads small: long strings are cut and marked."""
    if isinstance(value, str):
        return (
            value
            if len(value) <= limit
            else value[:limit] + f"... [{len(value) - limit} more characters]"
        )
    if isinstance(value, list):
        return [_bounded(item, limit) for item in value[:200]]
    if isinstance(value, dict):
        return {key: _bounded(item, limit) for key, item in value.items()}
    return value
