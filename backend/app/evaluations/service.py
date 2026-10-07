"""Run a small, reproducible baseline-versus-guarded evaluation without a UI."""

import asyncio
import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

import anthropic
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.loop import estimate_cost, execute_run
from app.agent.runs import create_run
from app.config import get_settings
from app.database import session_factory
from app.evaluations import scoring
from app.models import (
    AgentRun,
    EvaluationCase,
    EvaluationJudgment,
    EvaluationOutput,
    EvaluationRun,
    EvaluationScore,
    Workspace,
)
from app.schemas import AgentRunCreate
from app.services.claude_call import ClaudeCallFailed, get_client, new_tally, structured_call
from app.services.judge import Judge
from app.services.source_store import NewSource, store_source
from app.services.text_ingestion import parse_structured_text

# Europe PMC packets reserve a short truncation marker after their 80,000-character article body.
MAX_PACKET_CHARS = 81_000
JUDGE_ATTEMPTS = 3


class GeneratedCase(BaseModel):
    question: str = Field(min_length=10, max_length=700)
    completion_criteria: list[str] = Field(min_length=2, max_length=5)
    rubric: list[str] = Field(min_length=2, max_length=6)
    trap: str = Field(min_length=10, max_length=500)


class GeneratedCases(BaseModel):
    cases: list[GeneratedCase] = Field(min_length=1, max_length=5)


class PairwiseVerdict(BaseModel):
    answer_a_score: int = Field(ge=0, le=4)
    answer_b_score: int = Field(ge=0, le=4)
    winner: Literal["A", "B", "tie"]
    critical_errors_a: list[str] = Field(default_factory=list)
    critical_errors_b: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)


CASE_SYSTEM = """You design difficult, answerable scientific-reasoning evaluation cases from \
supplied
full-text source material. Write questions whose answer requires careful attention to methods,
limitations, study design, scope, contradictory evidence, or uncertainty rather than paraphrasing
an abstract. Do not require information outside the supplied packet. Give a concise hidden rubric
that names the evidence and calibration expected. Each case should hinge on a critical inference
(a tempting but wrong conclusion the source invites), not on recalling details. If
existing_questions_do_not_repeat is given, test different claims and reasoning than those
questions. Do not invent facts or citations. Keep every
question under 700 characters and every rubric/trap item under 500 characters. Return only JSON
without Markdown: {"cases":[{"question":string,"completion_criteria":[string],\
"rubric":[string],"trap":string}]}."""

JUDGE_SYSTEM = """You are a blind evaluator for a scientific-reasoning experiment. Score Answer A \
and
Answer B independently against the supplied question, source packet, and hidden rubric. You do not
know which system produced either answer and must not infer or discuss it. Reward factual accuracy,
use of the supplied evidence, appropriate uncertainty, and direct handling of the trap. Penalize
unsupported claims, causal overreach, ignored limitations, and confident answers where abstention
or qualification is warranted. Treat facts stated in the question as claims to assess, not source
evidence: reward them only when the supplied packet supports them. A score of 4 is fully \
correct and well calibrated; 0 is materially
wrong or unsupported. Choose the higher score as winner, or tie only when equal. Return only JSON
without Markdown: {"answer_a_score":integer,"answer_b_score":integer,"winner":"A"|"B"|"tie",\
"critical_errors_a":[string],"critical_errors_b":[string],"rationale":string}."""


def packet_hash(packet: dict) -> str:
    return hashlib.sha256(
        json.dumps(packet, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def generate_cases(
    packet: dict, count: int, *, client: Any = None, avoid: list[str] | None = None
) -> list[dict]:
    """Use the stronger model to make cases; callers persist the resulting immutable manifest."""
    settings = get_settings()
    content = {"packet": packet, "requested_cases": count}
    if avoid:
        content["existing_questions_do_not_repeat"] = avoid
    output = await structured_call(
        client or get_client(),
        model=settings.eval_judge_model,
        system=CASE_SYSTEM,
        content=content,
        tool_name="submit_evaluation_cases",
        description="Return difficult source-grounded questions and their hidden scoring rubrics.",
        schema=GeneratedCases,
        task="evaluation-case generation",
        force_tool=False,
        allow_text_json=True,
        use_tools=False,
    )
    return [case.model_dump() for case in output.cases]


async def create_evaluation(
    name: str,
    cases: list[dict],
    *,
    sessions: async_sessionmaker[AsyncSession] = session_factory,
    extra_config: dict | None = None,
) -> uuid.UUID:
    """Persist cases before any paid run so the exact experiment is always reproducible."""
    settings = get_settings()
    async with sessions() as session:
        evaluation = EvaluationRun(
            name=name,
            config={
                "arm_model": settings.eval_generator_model,
                "evaluation_judge_model": settings.eval_judge_model,
                "guard_judge_model": settings.agent_judge_model,
                "baseline_max_turns": settings.eval_max_turns,
                "guarded_max_turns": settings.eval_guarded_max_turns,
                "max_web_searches": settings.eval_max_web_searches,
                "turn_max_tokens": settings.agent_turn_max_tokens,
                "effort": settings.agent_effort,
                "max_cost_usd": settings.eval_max_cost_usd,
                "source_access": "packet only; web search disabled",
                "method_note": (
                    "Synthetic cases and blind LLM judging are a hackathon signal, "
                    "not independent validation."
                ),
                "scoring": scoring.SCORER,
                **(extra_config or {}),
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
        if len(text) > MAX_PACKET_CHARS:
            raise ValueError(
                f"Source {position} exceeds the {MAX_PACKET_CHARS}-character evaluation limit"
            )
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
    config: dict,
    *,
    sessions: async_sessionmaker[AsyncSession],
    client: Any,
) -> EvaluationOutput:
    async with sessions() as session:
        workspace = await _materialize_workspace(
            session, case.packet, f"Evaluation {case.id} {arm}"
        )
        payload = _arm_payload(case, arm, config)
        run = await create_run(session, workspace.id, payload)
        run.budgets = {
            **run.budgets,
            "turn_max_tokens": config.get("turn_max_tokens"),
            "effort": config.get("effort"),
        }
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
    guard_judge = Judge(client, model=config.get("guard_judge_model")) if arm == "guarded" else None
    await execute_run(
        run.id, sessions=sessions, client=client, amass=None, judge=guard_judge, literature=False
    )
    async with sessions() as session:
        run = await session.get(AgentRun, run.id)
        output = await session.get(EvaluationOutput, output_id)
        output.answer = run.final_report
        output.status = run.status
        output.usage = dict(run.usage)
        output.error = run.error
        await session.commit()
        return output


def _arm_payload(case: EvaluationCase, arm: str, config: dict) -> AgentRunCreate:
    """Build an arm request without exposing the evaluator's answer-specific rubric."""
    return AgentRunCreate(
        idempotency_key=f"evaluation-{case.id}-{arm}",
        question=case.rubric["question"],
        kind="question",
        completion_criteria=[],
        mode=arm,
        model=config.get("arm_model", config.get("generator_model")),
        max_turns=config.get(f"{arm}_max_turns", config.get("max_turns")),
        max_web_searches=config["max_web_searches"],
    )


async def _judge_case(
    case: EvaluationCase, outputs: list[EvaluationOutput], config: dict, *, sessions, client: Any
) -> EvaluationJudgment | None:
    by_arm = {output.arm: output for output in outputs}
    if any(
        by_arm.get(arm) is None
        or by_arm[arm].status != "succeeded"
        or not (by_arm[arm].answer or "").strip()
        for arm in ("baseline", "guarded")
    ):
        return None
    labels = case.blind_labels
    answers = _blind_answers(labels, by_arm)
    material = {
        "packet": case.packet,
        "question": case.rubric["question"],
        "rubric": case.rubric,
        "answers": answers,
    }
    tally = new_tally()
    for attempt in range(JUDGE_ATTEMPTS):
        try:
            verdict = await structured_call(
                client,
                model=config.get("evaluation_judge_model", config.get("judge_model")),
                system=JUDGE_SYSTEM,
                content=material,
                tool_name="submit_blind_evaluation",
                description="Score the two anonymous answers and select a winner.",
                schema=PairwiseVerdict,
                task="blind evaluation",
                tally=tally,
                force_tool=False,
                allow_text_json=True,
                use_tools=False,
            )
            break
        except ClaudeCallFailed as error:
            # A malformed judge reply must not abort the experiment; leave the case unjudged.
            if attempt == JUDGE_ATTEMPTS - 1:
                print(f"case {case.position}: blind evaluation failed: {error}", flush=True)
                return None
    async with sessions() as session:
        judgment = EvaluationJudgment(
            evaluation_case_id=case.id,
            model=config.get("evaluation_judge_model", config.get("judge_model")),
            material=material,
            verdict=verdict.model_dump(),
            usage=tally,
        )
        session.add(judgment)
        await session.commit()
        return judgment


def evaluation_client() -> anthropic.AsyncAnthropic:
    """Long agent turns and many concurrent calls: a generous timeout and patient retries."""
    return anthropic.AsyncAnthropic(
        api_key=get_settings().claude_api_key, timeout=600, max_retries=8
    )


async def run_evaluation(
    evaluation_id: uuid.UUID,
    *,
    sessions: async_sessionmaker[AsyncSession] = session_factory,
    client: Any = None,
    concurrency: int = 5,
    retry_failed: bool = False,
) -> None:
    """Execute missing arms and scores, several cases at once; safe to resume.

    The cost cap is checked before each case starts, so with N cases in flight it can be
    exceeded by at most N cases' cost.
    """
    client = client or evaluation_client()
    async with sessions() as session:
        evaluation = await session.get(EvaluationRun, evaluation_id)
        if evaluation is None:
            raise ValueError(f"Unknown evaluation {evaluation_id}")
        evaluation.status = "running"
        await session.commit()
        cases = list(
            await session.scalars(
                select(EvaluationCase)
                .where(EvaluationCase.evaluation_run_id == evaluation_id)
                .order_by(EvaluationCase.position)
            )
        )
        config = dict(evaluation.config)
    gate = asyncio.Semaphore(concurrency)
    over_budget = False

    async def one(case: EvaluationCase) -> None:
        nonlocal over_budget
        async with gate:
            if (
                over_budget
                or await _spent(evaluation_id, sessions=sessions) >= config["max_cost_usd"]
            ):
                over_budget = True
                return
            await _run_case(
                case, config, sessions=sessions, client=client, retry_failed=retry_failed
            )

    results = await asyncio.gather(*(one(case) for case in cases), return_exceptions=True)
    for case, result in zip(cases, results):
        if isinstance(result, Exception):
            print(f"case {case.position} failed: {type(result).__name__}: {result}", flush=True)
    await render_report(
        evaluation_id, sessions=sessions, status="budget_exhausted" if over_budget else "completed"
    )


async def _run_case(case, config, *, sessions, client, retry_failed: bool) -> None:
    async with sessions() as session:
        existing = list(
            await session.scalars(
                select(EvaluationOutput).where(EvaluationOutput.evaluation_case_id == case.id)
            )
        )
        if retry_failed:
            for output in [o for o in existing if o.status != "succeeded"]:
                await session.delete(output)
                existing.remove(output)
            await session.commit()
    by_arm = {output.arm: output for output in existing}
    missing = [arm for arm in ("baseline", "guarded") if arm not in by_arm]
    ran = await asyncio.gather(
        *(_run_arm(case, arm, config, sessions=sessions, client=client) for arm in missing)
    )
    by_arm.update({output.arm: output for output in ran})
    if config.get("scoring") == scoring.SCORER:
        done = await _score_case(
            case, list(by_arm.values()), config, sessions=sessions, client=client
        )
    else:
        async with sessions() as session:
            done = await session.scalar(
                select(EvaluationJudgment).where(EvaluationJudgment.evaluation_case_id == case.id)
            )
        if done is None:
            done = await _judge_case(
                case, list(by_arm.values()), config, sessions=sessions, client=client
            )
    async with sessions() as session:
        row = await session.get(EvaluationCase, case.id)
        row.status = "completed" if done else "incomplete"
        await session.commit()


async def _score_case(case, outputs, config, *, sessions, client) -> bool:
    """Derive the case's key once, then score each successful answer alone."""
    model = config.get("evaluation_judge_model", config.get("judge_model"))
    tally = scoring_tally = new_tally()
    key = (case.rubric or {}).get("scoring_key")
    if key is None:
        key = await scoring.derive_key(client, model, case.rubric, case.packet, scoring_tally)
        async with sessions() as session:
            row = await session.get(EvaluationCase, case.id)
            row.rubric = {**row.rubric, "scoring_key": key, "scoring_key_usage": dict(tally)}
            await session.commit()
        case.rubric = {**case.rubric, "scoring_key": key}
    scored = 0
    for output in outputs:
        if output.status != "succeeded" or not (output.answer or "").strip():
            continue
        async with sessions() as session:
            exists = await session.scalar(
                select(EvaluationScore).where(
                    EvaluationScore.evaluation_output_id == output.id,
                    EvaluationScore.scorer == scoring.SCORER,
                )
            )
        if exists:
            scored += 1
            continue
        usage = new_tally()
        material, result = await scoring.score_answer(
            client,
            model,
            case.rubric["question"],
            case.packet,
            key,
            _normalize_answer(output.answer),
            usage,
        )
        async with sessions() as session:
            session.add(
                EvaluationScore(
                    evaluation_output_id=output.id,
                    scorer=scoring.SCORER,
                    model=model,
                    material={k: v for k, v in material.items() if k != "packet"},
                    score=result,
                    usage=usage,
                )
            )
            await session.commit()
        scored += 1
    return scored == 2


async def score_evaluation(
    evaluation_id: uuid.UUID,
    *,
    sessions: async_sessionmaker[AsyncSession] = session_factory,
    client: Any = None,
    concurrency: int = 6,
) -> None:
    """Score stored answers (for example from an earlier run) without re-running any agent."""
    client = client or evaluation_client()
    async with sessions() as session:
        evaluation = await session.get(EvaluationRun, evaluation_id)
        config = dict(evaluation.config)
        cases = list(
            await session.scalars(
                select(EvaluationCase).where(EvaluationCase.evaluation_run_id == evaluation_id)
            )
        )
    gate = asyncio.Semaphore(concurrency)

    async def one(case):
        async with gate:
            async with sessions() as session:
                outputs = list(
                    await session.scalars(
                        select(EvaluationOutput).where(
                            EvaluationOutput.evaluation_case_id == case.id
                        )
                    )
                )
            await _score_case(case, outputs, config, sessions=sessions, client=client)

    results = await asyncio.gather(*(one(case) for case in cases), return_exceptions=True)
    for case, result in zip(cases, results):
        if isinstance(result, Exception):
            print(
                f"case {case.position} scoring failed: {type(result).__name__}: {result}",
                flush=True,
            )
    await render_report(evaluation_id, sessions=sessions)


def _normalize_answer(answer: str | None) -> str:
    """Remove system-authored arm markers without rewriting substantive answer content."""
    text = (answer or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    marker = "\n\n[Verifier record] Final certainty:"
    if marker in text:
        text = text.split(marker, 1)[0].rstrip()
    return text


def _blind_answers(labels: dict, by_arm: dict[str, EvaluationOutput]) -> dict[str, str]:
    labeled = {labels[arm]: _normalize_answer(by_arm[arm].answer) for arm in labels}
    return {label: labeled[label] for label in ("A", "B")}


async def _spent(evaluation_id: uuid.UUID, *, sessions: async_sessionmaker[AsyncSession]) -> float:
    """Use persisted usage only, so a resumed run cannot silently exceed its run budget."""
    async with sessions() as session:
        cases = list(
            await session.scalars(
                select(EvaluationCase).where(EvaluationCase.evaluation_run_id == evaluation_id)
            )
        )
        case_ids = [case.id for case in cases]
        outputs = (
            list(
                await session.scalars(
                    select(EvaluationOutput).where(
                        EvaluationOutput.evaluation_case_id.in_(case_ids)
                    )
                )
            )
            if case_ids
            else []
        )
        judgments = (
            list(
                await session.scalars(
                    select(EvaluationJudgment).where(
                        EvaluationJudgment.evaluation_case_id.in_(case_ids)
                    )
                )
            )
            if case_ids
            else []
        )
    # An arm still running has not recorded its cost yet; only a finished one must be priced.
    costs = [output.usage.get("cost_usd") for output in outputs if output.status != "running"]
    if any(cost is None for cost in costs):
        raise ValueError("Cannot enforce the evaluation cost cap for an unpriced arm model")
    agent_cost = sum(float(cost) for cost in costs)
    estimated = [estimate_cost(judgment.model, judgment.usage) for judgment in judgments]
    if any(cost is None for cost in estimated):
        raise ValueError("Cannot enforce the evaluation cost cap for an unpriced judge model")
    judge_cost = sum(estimated)
    async with sessions() as session:
        output_ids = [output.id for output in outputs]
        score_rows = (
            list(
                await session.scalars(
                    select(EvaluationScore).where(
                        EvaluationScore.evaluation_output_id.in_(output_ids)
                    )
                )
            )
            if output_ids
            else []
        )
    score_cost = sum(estimate_cost(row.model, row.usage) or 0 for row in score_rows)
    return agent_cost + judge_cost + score_cost


async def render_report(
    evaluation_id: uuid.UUID,
    *,
    sessions: async_sessionmaker[AsyncSession] = session_factory,
    status: str | None = None,
) -> str:
    async with sessions() as session:
        evaluation = await session.get(EvaluationRun, evaluation_id)
        cases = list(
            await session.scalars(
                select(EvaluationCase)
                .where(EvaluationCase.evaluation_run_id == evaluation_id)
                .order_by(EvaluationCase.position)
            )
        )
        lines = [
            f"# {evaluation.name}",
            "",
            "## Configuration",
            "",
            "```json",
            json.dumps(evaluation.config, indent=2),
            "```",
            "",
        ]
        agent_cost = {"baseline": 0.0, "guarded": 0.0}
        statuses = {"baseline": {}, "guarded": {}}
        scoring_cost = 0.0
        pairs, per_case = [], []
        wins = {"baseline": 0, "guarded": 0, "tie": 0}
        legacy = {"baseline": [], "guarded": []}
        legacy_lines, judge_cost = [], 0.0
        for case in cases:
            outputs = {
                o.arm: o
                for o in await session.scalars(
                    select(EvaluationOutput).where(EvaluationOutput.evaluation_case_id == case.id)
                )
            }
            scores = {}
            for arm, output in outputs.items():
                agent_cost[arm] += float(output.usage.get("cost_usd") or 0)
                statuses[arm][output.status] = statuses[arm].get(output.status, 0) + 1
                row = await session.scalar(
                    select(EvaluationScore).where(
                        EvaluationScore.evaluation_output_id == output.id,
                        EvaluationScore.scorer == scoring.SCORER,
                    )
                )
                if row:
                    scoring_cost += estimate_cost(row.model, row.usage) or 0
                    scores[arm] = row.score
            if len(scores) == 2:
                pairs.append((scores["baseline"], scores["guarded"]))
            per_case.append((case, scores))
            judgment = await session.scalar(
                select(EvaluationJudgment).where(EvaluationJudgment.evaluation_case_id == case.id)
            )
            if judgment:
                judge_cost += estimate_cost(judgment.model, judgment.usage) or 0
                winner = judgment.verdict["winner"]
                arm = next(
                    (name for name, label in case.blind_labels.items() if label == winner), winner
                )
                wins[arm] += 1
                for arm_name, label in case.blind_labels.items():
                    legacy[arm_name].append(judgment.verdict[f"answer_{label.lower()}_score"])
                legacy_lines.extend(
                    [f"### Case {case.position}: {arm}", "", judgment.verdict["rationale"], ""]
                )

        lines += ["## Run", ""]
        for arm in ("baseline", "guarded"):
            lines.append(f"- {arm}: statuses {statuses[arm]}, agent cost ${agent_cost[arm]:.2f}")
        lines += [f"- Scoring cost: ${scoring_cost:.2f}", ""]

        if pairs:
            lines += [
                f"## Accuracy and reasoning (scorer {scoring.SCORER}; {len(pairs)} paired cases)",
                "",
                "Each answer scored alone against the source packet; omissions are not errors.",
                "Diff = guarded minus baseline, with a paired bootstrap 95% CI over cases.",
                "",
                "| Metric | Baseline | Guarded | Diff [95% CI] | Better |",
                "|---|---:|---:|---|---|",
            ]
            for row in scoring.paired_summary(pairs):
                lines.append(
                    f"| {row['label']} | {row['baseline']:.2f} | {row['guarded']:.2f} | "
                    f"{row['diff']:+.2f} [{row['ci_low']:+.2f}, {row['ci_high']:+.2f}] | "
                    f"{row['better'] or '-'} |"
                )
            lines += [
                "",
                "### Per case",
                "",
                "| # | Expected | Baseline verdict / errors / deductions "
                "| Guarded verdict / errors / deductions |",
                "|---|---|---|---|",
            ]
            for case, scores in per_case:

                def cell(arm):
                    sc = scores.get(arm)
                    if not sc:
                        return "not scored"
                    mark = "ok" if sc["verdict_correct"] else "wrong"
                    ded = (
                        f"{sc['deductions_valid']}/{sc['deductions_required']}"
                        if sc["deductions_required"]
                        else "-"
                    )
                    return f"{sc['verdict_given']} ({mark}) / {sc['errors']} / {ded}"

                expected = (case.rubric.get("scoring_key") or {}).get("expected_verdict", "?")
                lines.append(
                    f"| {case.position} | {expected} | {cell('baseline')} | {cell('guarded')} |"
                )
            lines.append("")

        if legacy_lines:
            judged = sum(wins.values())
            lines += [
                "## Legacy pairwise judgment (coverage-weighted; superseded)",
                "",
                f"- Cases judged: {judged}/{len(cases)}; guarded wins {wins['guarded']}, "
                f"baseline wins {wins['baseline']}, ties {wins['tie']}",
                f"- Mean score: guarded {sum(legacy['guarded']) / len(legacy['guarded']):.2f}, "
                f"baseline {sum(legacy['baseline']) / len(legacy['baseline']):.2f}",
                f"- Judge cost: ${judge_cost:.2f}",
                "",
                *legacy_lines,
            ]
        lines.append(
            "Limitation: LLM-derived keys and LLM scoring (except SciFact gold labels); "
            "a hackathon signal, not independent validation."
        )
        report = "\n".join(lines)
        evaluation.report = report
        if status is not None:
            evaluation.status = status
            evaluation.completed_at = datetime.now(UTC)
        await session.commit()
        return report
