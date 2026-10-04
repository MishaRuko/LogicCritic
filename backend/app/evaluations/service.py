"""Run a small, reproducible baseline-versus-guarded evaluation without a UI."""

import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.loop import estimate_cost, execute_run
from app.agent.runs import create_run
from app.config import get_settings
from app.database import session_factory
from app.models import (
    AgentRun,
    EvaluationCase,
    EvaluationJudgment,
    EvaluationOutput,
    EvaluationRun,
    Workspace,
)
from app.schemas import AgentRunCreate
from app.services.claude_call import get_client, new_tally, structured_call
from app.services.source_store import NewSource, store_source
from app.services.text_ingestion import parse_structured_text


class GeneratedCase(BaseModel):
    question: str = Field(min_length=10)
    completion_criteria: list[str] = Field(min_length=2, max_length=5)
    rubric: list[str] = Field(min_length=2, max_length=6)
    trap: str = Field(min_length=10)


class GeneratedCases(BaseModel):
    cases: list[GeneratedCase] = Field(min_length=1, max_length=5)


class PairwiseVerdict(BaseModel):
    answer_a_score: int = Field(ge=0, le=4)
    answer_b_score: int = Field(ge=0, le=4)
    winner: Literal["A", "B", "tie"]
    critical_errors_a: list[str] = Field(default_factory=list)
    critical_errors_b: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)


CASE_SYSTEM = """You design difficult, answerable scientific-reasoning evaluation cases from supplied
full-text source material. Write questions whose answer requires careful attention to methods,
limitations, study design, scope, contradictory evidence, or uncertainty rather than paraphrasing
an abstract. Do not require information outside the supplied packet. Give a concise hidden rubric
that names the evidence and calibration expected. Do not invent facts or citations."""

JUDGE_SYSTEM = """You are a blind evaluator for a scientific-reasoning experiment. Score Answer A and
Answer B independently against the supplied question, source packet, and hidden rubric. You do not
know which system produced either answer and must not infer or discuss it. Reward factual accuracy,
use of the supplied evidence, appropriate uncertainty, and direct handling of the trap. Penalize
unsupported claims, causal overreach, ignored limitations, and confident answers where abstention
or qualification is warranted. A score of 4 is fully correct and well calibrated; 0 is materially
wrong or unsupported. Choose the higher score as winner, or tie only when equal."""


def packet_hash(packet: dict) -> str:
    return hashlib.sha256(json.dumps(packet, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def generate_cases(packet: dict, count: int, *, client: Any = None) -> list[dict]:
    """Use the stronger model to make cases; callers persist the resulting immutable manifest."""
    settings = get_settings()
    output = await structured_call(
        client or get_client(),
        model=settings.eval_judge_model,
        system=CASE_SYSTEM,
        content={"packet": packet, "requested_cases": count},
        tool_name="submit_evaluation_cases",
        description="Return difficult source-grounded questions and their hidden scoring rubrics.",
        schema=GeneratedCases,
        task="evaluation-case generation",
    )
    return [case.model_dump() for case in output.cases]


async def create_evaluation(
    name: str, cases: list[dict], *, sessions: async_sessionmaker[AsyncSession] = session_factory
) -> uuid.UUID:
    """Persist cases before any paid run so the exact experiment is always reproducible."""
    settings = get_settings()
    async with sessions() as session:
        evaluation = EvaluationRun(
            name=name,
            config={
                "generator_model": settings.eval_generator_model,
                "judge_model": settings.eval_judge_model,
                "max_turns": settings.eval_max_turns,
                "max_web_searches": settings.eval_max_web_searches,
                "source_access": "packet only; web search disabled",
                "method_note": "Synthetic cases and blind LLM judging are a hackathon signal, not independent validation.",
            },
        )
        session.add(evaluation)
        await session.flush()
        for position, case in enumerate(cases, start=1):
            packet = case["packet"]
            labels = {"baseline": "A", "guarded": "B"}
            if secrets.randbelow(2):
                labels = {"baseline": "B", "guarded": "A"}
            session.add(
                EvaluationCase(
                    evaluation_run_id=evaluation.id,
                    position=position,
                    packet=packet,
                    packet_hash=packet_hash(packet),
                    rubric={key: value for key, value in case.items() if key != "packet"},
                    blind_labels=labels,
                )
            )
        await session.commit()
        return evaluation.id


async def _materialize_workspace(session: AsyncSession, packet: dict, title: str) -> Workspace:
    workspace = Workspace(title=title)
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    for position, source in enumerate(packet["sources"], start=1):
        text = source["text"]
        await store_source(
            session,
            workspace.id,
            NewSource(
                kind="evaluation_packet",
                origin="evaluation",
                title=source.get("title") or f"Evaluation source {position}",
                mime_type="text/markdown",
                filename=f"evaluation-source-{position}.md",
                content=text.encode(),
                excerpts=parse_structured_text(text),
                metadata={"evaluation": True},
            ),
        )
    return workspace


async def _run_arm(
    case: EvaluationCase,
    arm: Literal["baseline", "guarded"],
    *,
    sessions: async_sessionmaker[AsyncSession],
    client: Any,
) -> EvaluationOutput:
    settings = get_settings()
    async with sessions() as session:
        workspace = await _materialize_workspace(session, case.packet, f"Evaluation {case.id} {arm}")
        rubric = case.rubric
        payload = AgentRunCreate(
            idempotency_key=f"evaluation-{case.id}-{arm}",
            question=rubric["question"],
            kind="question",
            completion_criteria=rubric.get("completion_criteria", []),
            mode=arm,
            model=settings.eval_generator_model,
            max_turns=settings.eval_max_turns,
            max_web_searches=settings.eval_max_web_searches,
        )
        run = await create_run(session, workspace.id, payload)
        run.status = "running"
        await session.commit()
        output = EvaluationOutput(
            evaluation_case_id=case.id,
            arm=arm,
            workspace_id=workspace.id,
            agent_run_id=run.id,
            status="running",
        )
        session.add(output)
        await session.commit()
        output_id = output.id
    await execute_run(run.id, sessions=sessions, client=client, amass=None)
    async with sessions() as session:
        run = await session.get(AgentRun, run.id)
        output = await session.get(EvaluationOutput, output_id)
        output.answer = run.final_report
        output.status = run.status
        output.usage = dict(run.usage)
        output.error = run.error
        await session.commit()
        return output


async def _judge_case(
    case: EvaluationCase, outputs: list[EvaluationOutput], *, sessions, client: Any
) -> EvaluationJudgment | None:
    by_arm = {output.arm: output for output in outputs}
    if any(by_arm.get(arm) is None or by_arm[arm].status != "succeeded" for arm in ("baseline", "guarded")):
        return None
    labels = case.blind_labels
    answers = {labels[arm]: by_arm[arm].answer for arm in ("baseline", "guarded")}
    material = {"packet": case.packet, "question": case.rubric["question"], "rubric": case.rubric, "answers": answers}
    settings = get_settings()
    tally = new_tally()
    verdict = await structured_call(
        client,
        model=settings.eval_judge_model,
        system=JUDGE_SYSTEM,
        content=material,
        tool_name="submit_blind_evaluation",
        description="Score the two anonymous answers and select a winner.",
        schema=PairwiseVerdict,
        task="blind evaluation",
        tally=tally,
    )
    async with sessions() as session:
        judgment = EvaluationJudgment(
            evaluation_case_id=case.id,
            model=settings.eval_judge_model,
            material=material,
            verdict=verdict.model_dump(),
            usage=tally,
        )
        session.add(judgment)
        await session.commit()
        return judgment


async def run_evaluation(
    evaluation_id: uuid.UUID, *, sessions: async_sessionmaker[AsyncSession] = session_factory, client: Any = None
) -> None:
    """Execute missing arms and judgments; safe to resume after a partial paid run."""
    client = client or get_client()
    async with sessions() as session:
        evaluation = await session.get(EvaluationRun, evaluation_id)
        if evaluation is None:
            raise ValueError(f"Unknown evaluation {evaluation_id}")
        evaluation.status = "running"
        await session.commit()
        cases = list(await session.scalars(select(EvaluationCase).where(EvaluationCase.evaluation_run_id == evaluation_id).order_by(EvaluationCase.position)))
    for case in cases:
        async with sessions() as session:
            existing = list(await session.scalars(select(EvaluationOutput).where(EvaluationOutput.evaluation_case_id == case.id)))
        by_arm = {output.arm: output for output in existing}
        for arm in ("baseline", "guarded"):
            if arm not in by_arm:
                by_arm[arm] = await _run_arm(case, arm, sessions=sessions, client=client)
        async with sessions() as session:
            already_judged = await session.scalar(select(EvaluationJudgment).where(EvaluationJudgment.evaluation_case_id == case.id))
        if already_judged is None:
            await _judge_case(case, list(by_arm.values()), sessions=sessions, client=client)
        async with sessions() as session:
            row = await session.get(EvaluationCase, case.id)
            row.status = "completed"
            await session.commit()
    await render_report(evaluation_id, sessions=sessions)


async def render_report(
    evaluation_id: uuid.UUID, *, sessions: async_sessionmaker[AsyncSession] = session_factory
) -> str:
    async with sessions() as session:
        evaluation = await session.get(EvaluationRun, evaluation_id)
        cases = list(await session.scalars(select(EvaluationCase).where(EvaluationCase.evaluation_run_id == evaluation_id).order_by(EvaluationCase.position)))
        wins = {"baseline": 0, "guarded": 0, "tie": 0}
        scores = {"baseline": [], "guarded": []}
        total_cost = 0.0
        evaluation_judge_cost = 0.0
        lines = [f"# {evaluation.name}", "", "## Configuration", "", "```json", json.dumps(evaluation.config, indent=2), "```", "", "## Results", ""]
        for case in cases:
            outputs = list(await session.scalars(select(EvaluationOutput).where(EvaluationOutput.evaluation_case_id == case.id)))
            judgment = await session.scalar(select(EvaluationJudgment).where(EvaluationJudgment.evaluation_case_id == case.id))
            for output in outputs:
                total_cost += float(output.usage.get("cost_usd") or 0)
            if judgment:
                evaluation_judge_cost += estimate_cost(judgment.model, judgment.usage) or 0
                winner = judgment.verdict["winner"]
                arm = next((name for name, label in case.blind_labels.items() if label == winner), winner)
                wins[arm] += 1
                for arm_name, label in case.blind_labels.items():
                    scores[arm_name].append(judgment.verdict[f"answer_{label.lower()}_score"])
                lines.extend([f"### Case {case.position}: {arm}", "", judgment.verdict["rationale"], ""])
            else:
                lines.extend([f"### Case {case.position}: not judged", ""])
        judged = sum(wins.values())
        lines[lines.index("## Results") + 2:lines.index("## Results") + 2] = [
            f"- Cases judged: {judged}/{len(cases)}",
            f"- Guarded wins: {wins['guarded']}",
            f"- Baseline wins: {wins['baseline']}",
            f"- Ties: {wins['tie']}",
            f"- Mean blind score: guarded {sum(scores['guarded']) / len(scores['guarded']):.2f}, baseline {sum(scores['baseline']) / len(scores['baseline']):.2f}" if scores["guarded"] else "- Mean blind score: no completed judgments",
            f"- Agent cost recorded: ${total_cost:.4f}",
            f"- Evaluation-judge cost recorded: ${evaluation_judge_cost:.4f}",
            "- Limitation: cases and judging use an LLM; this is a blinded hackathon evaluation, not independent validation.",
            "",
        ]
        report = "\n".join(lines)
        evaluation.report = report
        evaluation.status = "completed"
        evaluation.completed_at = datetime.now(UTC)
        await session.commit()
        return report
