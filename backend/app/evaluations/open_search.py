"""Open-search evaluation: can the agent find, read and weigh the evidence itself?

The packet evaluation (`service.py`) hands each arm the source text and switches search off, so
it measures honest reasoning over given material. This one starts every arm in an empty
workspace with paper and web search on, and asks a question answered by a published review.

What is measured, per arm (for example quick against thorough research):

- **Key papers** (free): the review's most cited references are the papers an expert would
  expect the answer to rest on. Did the agent's searches find them, and did it read them?
- **Agreement** (one judge call per answer): does the answer's conclusion agree with the
  review's? The judge sees the question, the review's conclusion and the answer, nothing else.
- **Effort**: sources read, searches, turns, cost and time.
- **Calibration**: agreement within each certainty level the agent gave.
- **Consistency**: with repeats, how often the same arm reaches the same agreement.
"""

import asyncio
import re
import statistics
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent import evidence
from app.agent.loop import estimate_cost, execute_run
from app.agent.runs import create_run
from app.agent.toolbox import hidden_source
from app.config import get_settings
from app.database import session_factory
from app.evaluations import scoring
from app.evaluations.service import _spent, evaluation_client, packet_hash
from app.models import (
    AgentEvent,
    AgentRun,
    EvaluationCase,
    EvaluationOutput,
    EvaluationRun,
    EvaluationScore,
    Source,
    Workspace,
)
from app.schemas import AgentRunCreate
from app.services.claude_call import new_tally
from app.services.judge import Judge

SCORER = "open-v1"
PAPER_SEARCHES = ("search_papers", "follow_citations")  # results count as found
DEFAULT_ARMS = {
    "quick": {"mode": "guarded", "depth": "quick"},
    "thorough": {"mode": "guarded", "depth": "thorough"},
}
AGREEMENT = {"agrees": 1.0, "partly": 0.5, "disagrees": 0.0, "no_conclusion": 0.0}


class OpenJudgment(BaseModel):
    agreement: Literal["agrees", "partly", "disagrees", "no_conclusion"]
    rationale: str = Field(min_length=1)


JUDGE_SYSTEM = """You compare one research answer with the conclusion of a published review of the \
same question. Decide whether the answer's conclusion agrees with the review's: "agrees" (same \
conclusion, allowing different wording and reasonable extra caution), "partly" (agrees on some \
of it, or hedges where the review is clear), "disagrees" (reaches a conflicting conclusion), or \
"no_conclusion" (the answer does not reach one). Judge the conclusion, not the length, style or \
sources of the answer. The review can be dated; if the answer gives newer evidence for a \
different conclusion, say so in the rationale but still classify against the review. Return only \
JSON: {"agreement": string, "rationale": string}."""


def slots(config: dict) -> list[str]:
    """Each arm once per repeat: quick, quick#2, thorough, thorough#2 ..."""
    repeats = int(config.get("repeats", 1))
    return [
        name if repeat == 1 else f"{name}#{repeat}"
        for name in config["arms"]
        for repeat in range(1, repeats + 1)
    ]


def arm_of(slot: str) -> str:
    return slot.split("#", 1)[0]


async def create_open_evaluation(
    name: str,
    cases: list[dict],
    *,
    arms: dict | None = None,
    repeats: int = 1,
    sessions: async_sessionmaker[AsyncSession] = session_factory,
) -> uuid.UUID:
    """Persist the cases first, so the exact experiment is reproducible before anything is paid."""
    arms = arms or DEFAULT_ARMS
    if any(len(f"{arm}#{repeats}") > 16 for arm in arms):
        raise ValueError("Arm names must be short enough to store with their repeat number")
    settings = get_settings()
    async with sessions() as session:
        evaluation = EvaluationRun(
            name=name,
            config={
                "kind": "open",
                "arms": arms,
                "repeats": repeats,
                "arm_model": settings.eval_generator_model,
                "evaluation_judge_model": settings.eval_judge_model,
                "guard_judge_model": settings.agent_judge_model,
                "max_cost_usd": settings.eval_max_cost_usd,
                "source_access": "open: paper search and web search on, empty workspace",
                "scoring": SCORER,
            },
        )
        session.add(evaluation)
        await session.flush()
        for position, case in enumerate(cases, start=1):
            if not case.get("question") or not case.get("reference", {}).get("conclusion"):
                raise ValueError(f"Case {position} needs a question and a reference conclusion")
            session.add(
                EvaluationCase(
                    evaluation_run_id=evaluation.id,
                    position=position,
                    packet={"sources": []},
                    packet_hash=packet_hash({"question": case["question"]}),
                    rubric=case,
                    blind_labels={},
                )
            )
        await session.commit()
        return evaluation.id


async def _run_slot(
    case: EvaluationCase, slot: str, config: dict, *, sessions, client
) -> EvaluationOutput:
    spec = config["arms"][arm_of(slot)]
    async with sessions() as session:
        workspace = Workspace(title=f"Open evaluation {case.position} {slot}")
        session.add(workspace)
        await session.commit()
        payload = AgentRunCreate(
            idempotency_key=f"open-{case.id}-{slot}",
            question=case.rubric["question"],
            kind="question",
            completion_criteria=[],
            mode=spec["mode"],
            model=config.get("arm_model"),
            depth=spec.get("depth", "thorough"),
            max_turns=spec.get("max_turns"),
            max_web_searches=spec.get("max_web_searches"),
        )
        run = await create_run(session, workspace.id, payload)
        # The review the answer is judged against is kept out of the agent's reach: reading it
        # would measure copying, not research.
        run.budgets = {
            **run.budgets,
            "hidden_source": hidden_source(case.rubric.get("reference") or {}),
        }
        run.status = "running"
        # A running run with no heartbeat looks abandoned to stale-run recovery.
        run.started_at = run.heartbeat_at = datetime.now(UTC)
        await session.commit()
        output = EvaluationOutput(
            evaluation_case_id=case.id,
            arm=slot,
            workspace_id=workspace.id,
            agent_run_id=run.id,
            status="running",
        )
        session.add(output)
        await session.commit()
        output_id = output.id
    guard = (
        Judge(client, model=config.get("guard_judge_model")) if spec["mode"] == "guarded" else None
    )
    # Paper search and web search stay on: finding the evidence is what is measured.
    await execute_run(run.id, sessions=sessions, client=client, judge=guard)
    async with sessions() as session:
        run = await session.get(AgentRun, run.id)
        output = await session.get(EvaluationOutput, output_id)
        output.answer = run.final_report
        output.status = run.status
        output.usage = {
            **dict(run.usage),
            "certainty": run.certainty,
            "seconds": (run.completed_at - run.started_at).total_seconds()
            if run.completed_at and run.started_at
            else None,
        }
        output.error = run.error
        await session.commit()
        return output


def _norm_title(title: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (title or "").lower())


def _keys(item: dict) -> set[str]:
    """What identifies a paper: DOI, PubMed, arXiv ID or title, normalised."""
    keys = set()
    for name in ("doi", "pmid", "arxiv_id"):
        if item.get(name):
            keys.add(f"{name}:{str(item[name]).lower().removeprefix('https://doi.org/')}")
    if len(_norm_title(item.get("title"))) >= 20:
        keys.add(f"title:{_norm_title(item.get('title'))}")
    return keys


REVIEW_TITLE = re.compile(
    r"review|meta-?analys|systematic|survey|overview|state of the art|umbrella", re.IGNORECASE
)


async def discovery(
    session: AsyncSession,
    output: EvaluationOutput,
    expected: list[dict],
    reference: dict | None = None,
) -> dict:
    """Which key papers the agent's searches surfaced, and which it brought in to read. Free."""
    sources = [
        source
        for source in await session.scalars(
            select(Source).where(Source.workspace_id == output.workspace_id)
        )
        # The agent's own protocol notes are sources in the graph, but not evidence it read.
        if (source.metadata_ or {}).get("parser") != "agent_protocol_v1"
    ]
    read_keys = set()
    for source in sources:
        read_keys |= _keys({**(source.external_ids or {}), "title": source.title})
        url = (source.external_ids or {}).get("url") or ""
        if match := re.search(r"10\.\d{4,9}/[^\s?#]+", url):
            read_keys.add(f"doi:{match.group(0).lower()}")
        if match := re.search(r"arxiv\.org/(?:abs|pdf)/([^\s?#v]+)", url):
            read_keys.add(f"arxiv_id:{match.group(1).lower()}")
    events = list(
        await session.scalars(
            select(AgentEvent).where(
                AgentEvent.run_id == output.agent_run_id,
                AgentEvent.type.in_(["tool_result", "web_results"]),
            )
        )
    )
    found_keys = set(read_keys)
    searches = 0
    for event in events:
        payload = event.payload or {}
        if event.type == "tool_result" and payload.get("name") in PAPER_SEARCHES:
            searches += payload.get("name") == "search_papers"
            for row in (payload.get("result") or {}).get("results", []):
                found_keys |= _keys(row)
        elif event.type == "web_results":
            for row in payload.get("results", []):
                found_keys |= _keys({"title": row.get("title")})
                if match := re.search(r"10\.\d{4,9}/[^\s?#]+", row.get("url") or ""):
                    found_keys.add(f"doi:{match.group(0).lower()}")
    found = [paper for paper in expected if _keys(paper) & found_keys]
    read = [paper for paper in expected if _keys(paper) & read_keys]
    return {
        "expected": len(expected),
        "found": len(found),
        "read": len(read),
        "sources_read": len(sources),
        "paper_searches": searches,
        # Reading a review is what a careful researcher does first; reading the very review this
        # case is judged against makes agreement easy, so the report shows it.
        "read_a_review": any(REVIEW_TITLE.search(source.title or "") for source in sources),
        "read_the_reference": bool(reference and _keys(reference) & read_keys),
    }


async def _score_slot(case, output, config, *, sessions, client) -> None:
    async with sessions() as session:
        exists = await session.scalar(
            select(EvaluationScore).where(
                EvaluationScore.evaluation_output_id == output.id,
                EvaluationScore.scorer == SCORER,
            )
        )
        if exists:
            return
        found = await discovery(
            session, output, case.rubric.get("expected_sources", []), case.rubric.get("reference")
        )
    usage = new_tally()
    judged = None
    if output.status == "succeeded" and (output.answer or "").strip():
        material = {
            "question": case.rubric["question"],
            "review_conclusion": case.rubric["reference"]["conclusion"],
            "answer": output.answer,
        }
        result = await scoring._ask(
            client,
            config["evaluation_judge_model"],
            JUDGE_SYSTEM,
            material,
            OpenJudgment,
            "open-search agreement",
            usage,
        )
        judged = result.model_dump()
    async with sessions() as session:
        session.add(
            EvaluationScore(
                evaluation_output_id=output.id,
                scorer=SCORER,
                model=config["evaluation_judge_model"],
                material={"reference": case.rubric["reference"]},
                score={"discovery": found, "judgment": judged},
                usage=usage,
            )
        )
        await session.commit()


async def run_open_evaluation(
    evaluation_id: uuid.UUID,
    *,
    sessions: async_sessionmaker[AsyncSession] = session_factory,
    client: Any = None,
    concurrency: int = 3,
    retry_failed: bool = False,
) -> str:
    """Run every missing slot of every case, score it, and write the report. Resumable."""
    client = client or evaluation_client()
    async with sessions() as session:
        evaluation = await session.get(EvaluationRun, evaluation_id)
        if evaluation is None or evaluation.config.get("kind") != "open":
            raise ValueError(f"{evaluation_id} is not an open-search evaluation")
        evaluation.status = "running"
        await session.commit()
        config = dict(evaluation.config)
        cases = list(
            await session.scalars(
                select(EvaluationCase)
                .where(EvaluationCase.evaluation_run_id == evaluation_id)
                .order_by(EvaluationCase.position)
            )
        )
    gate = asyncio.Semaphore(concurrency)
    over_budget = False

    async def one(case: EvaluationCase, slot: str) -> None:
        nonlocal over_budget
        async with gate:
            async with sessions() as session:
                output = await session.scalar(
                    select(EvaluationOutput).where(
                        EvaluationOutput.evaluation_case_id == case.id,
                        EvaluationOutput.arm == slot,
                    )
                )
                if output is not None and retry_failed and output.status != "succeeded":
                    await session.delete(output)
                    await session.commit()
                    output = None
            if output is None:
                if (
                    over_budget
                    or await _spent(evaluation_id, sessions=sessions) >= config["max_cost_usd"]
                ):
                    over_budget = True
                    return
                output = await _run_slot(case, slot, config, sessions=sessions, client=client)
            await _score_slot(case, output, config, sessions=sessions, client=client)

    work = [(case, slot) for case in cases for slot in slots(config)]
    results = await asyncio.gather(*(one(c, s) for c, s in work), return_exceptions=True)
    for (case, slot), result in zip(work, results, strict=True):
        if isinstance(result, Exception):
            print(f"case {case.position} {slot} failed: {type(result).__name__}: {result}")
    return await render_open_report(
        evaluation_id, sessions=sessions, status="budget_exhausted" if over_budget else "completed"
    )


def _median(values: list[float]) -> float | None:
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def _mean(values: list[float]) -> float | None:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _fmt(value, spec: str = ".2f") -> str:
    return "-" if value is None else format(value, spec)


def grade_section(rows: dict) -> list[str]:
    """How the agent's certainty compares with the reviewers' own GRADE rating of the evidence.

    This measures calibration directly: agreement with a review says whether the answer is
    right, the GRADE comparison whether its certainty is. Positive differences mean the agent
    claims more certainty than the reviewers found."""
    lines = ["", "## Certainty against the review's GRADE rating", ""]
    lines += [
        "| Arm | Review rating | Answers | The agent said | Same level |",
        "|---|---|---|---|---|",
    ]
    summary = []
    for arm, facts in rows.items():
        rated = [
            f
            for f in facts
            if f["expected_certainty"] in evidence.GRADE
            and evidence.LEGACY.get(f["certainty"], f["certainty"]) in evidence.LEVELS
        ]
        diffs = []
        for grade, level in evidence.GRADE.items():
            group = [f for f in rated if f["expected_certainty"] == grade]
            if not group:
                continue
            said = Counter(evidence.LEGACY.get(f["certainty"], f["certainty"]) for f in group)
            same = sum(evidence.LEGACY.get(f["certainty"], f["certainty"]) == level for f in group)
            lines.append(
                f"| {arm} | {grade.replace('_', ' ')} | {len(group)} | "
                + ", ".join(f"{name} {n}" for name, n in said.most_common())
                + f" | {same}/{len(group)} |"
            )
            for f in group:
                diffs.append(
                    evidence.LEVELS.index(evidence.LEGACY.get(f["certainty"], f["certainty"]))
                    - evidence.LEVELS.index(level)
                )
        if diffs:
            summary.append(
                f"- {arm}: the same level as the review for {sum(d == 0 for d in diffs)}/"
                f"{len(diffs)}, within one level for {sum(abs(d) <= 1 for d in diffs)}/"
                f"{len(diffs)}; mean difference {_mean(diffs):+.1f} levels (positive: claims "
                "more than the review)"
            )
    return lines + [""] + summary


async def render_open_report(
    evaluation_id: uuid.UUID,
    *,
    sessions: async_sessionmaker[AsyncSession] = session_factory,
    status: str | None = None,
) -> str:
    async with sessions() as session:
        evaluation = await session.get(EvaluationRun, evaluation_id)
        cases = {
            case.id: case
            for case in await session.scalars(
                select(EvaluationCase).where(EvaluationCase.evaluation_run_id == evaluation_id)
            )
        }
        outputs = list(
            await session.scalars(
                select(EvaluationOutput).where(EvaluationOutput.evaluation_case_id.in_(cases))
            )
        )
        scores = {
            row.evaluation_output_id: row
            for row in await session.scalars(
                select(EvaluationScore).where(
                    EvaluationScore.evaluation_output_id.in_([o.id for o in outputs]),
                    EvaluationScore.scorer == SCORER,
                )
            )
        }
    rows = defaultdict(list)  # arm -> per-output facts
    for output in outputs:
        score = scores.get(output.id)
        judged = (score.score.get("judgment") if score else None) or {}
        found = (score.score.get("discovery") if score else None) or {}
        expected = found.get("expected") or 0
        rows[arm_of(output.arm)].append(
            {
                "case": output.evaluation_case_id,
                "slot": output.arm,
                "succeeded": output.status == "succeeded",
                "agreement": judged.get("agreement"),
                "certainty": output.usage.get("certainty"),
                "expected_certainty": cases[output.evaluation_case_id].rubric.get(
                    "expected_certainty"
                ),
                "found": found.get("found", 0) / expected if expected else None,
                "read": found.get("read", 0) / expected if expected else None,
                "sources_read": found.get("sources_read"),
                "read_a_review": found.get("read_a_review"),
                "read_the_reference": found.get("read_the_reference"),
                "searches": (found.get("paper_searches") or 0)
                + (output.usage.get("web_searches") or 0),
                "turns": output.usage.get("turns"),
                "cost": output.usage.get("cost_usd"),
                "judge_cost": estimate_cost(score.model, score.usage) if score else 0,
                "seconds": output.usage.get("seconds"),
            }
        )
    lines = [
        f"# {evaluation.name}",
        "",
        f"Open-search evaluation, {len(cases)} questions, arms: "
        + ", ".join(f"{name} ({spec})" for name, spec in evaluation.config["arms"].items())
        + f", {evaluation.config.get('repeats', 1)} run(s) each.",
        "",
        "| Arm | Answered | Agrees with review | Read a review | Read the reference review | "
        "Key papers found | Key papers read | Sources read (median) | Searches (median) | "
        "Turns (median) | Cost per answer | Minutes (median) |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for arm, facts in rows.items():
        judged = [AGREEMENT[f["agreement"]] for f in facts if f["agreement"]]
        seconds = _median([f["seconds"] for f in facts])
        lines.append(
            f"| {arm} | {sum(f['succeeded'] for f in facts)}/{len(facts)} "
            f"| {_fmt(_mean(judged), '.0%')} "
            f"| {sum(bool(f['read_a_review']) for f in facts)}/{len(facts)} "
            f"| {sum(bool(f['read_the_reference']) for f in facts)}/{len(facts)} "
            f"| {_fmt(_mean([f['found'] for f in facts]), '.0%')} "
            f"| {_fmt(_mean([f['read'] for f in facts]), '.0%')} "
            f"| {_fmt(_median([f['sources_read'] for f in facts]), '.0f')} "
            f"| {_fmt(_median([f['searches'] for f in facts]), '.0f')} "
            f"| {_fmt(_median([f['turns'] for f in facts]), '.0f')} "
            f"| ${_fmt(_mean([f['cost'] for f in facts]))} "
            f"| {_fmt(seconds / 60 if seconds else None, '.1f')} |"
        )
    lines += ["", "## Calibration: agreement by the certainty the agent gave", ""]
    lines += ["| Arm | Certainty | Answers | Agrees with review |", "|---|---|---|---|"]
    for arm, facts in rows.items():
        by_level = defaultdict(list)
        for f in facts:
            if f["agreement"]:
                by_level[f["certainty"] or "none"].append(AGREEMENT[f["agreement"]])
        for level, values in sorted(by_level.items()):
            lines.append(f"| {arm} | {level} | {len(values)} | {_fmt(_mean(values), '.0%')} |")
    if any(f["expected_certainty"] for fs in rows.values() for f in fs):
        lines += grade_section(rows)
    if int(evaluation.config.get("repeats", 1)) > 1:
        lines += ["", "## Consistency across repeats", ""]
        for arm, facts in rows.items():
            per_case = defaultdict(list)
            for f in facts:
                if f["agreement"]:
                    per_case[f["case"]].append(f["agreement"])
            same = [len(set(v)) == 1 for v in per_case.values() if len(v) > 1]
            lines.append(
                f"- {arm}: the same agreement in every repeat for {sum(same)}/{len(same)} questions"
            )
    total = sum((f["cost"] or 0) + (f["judge_cost"] or 0) for fs in rows.values() for f in fs)
    lines += [
        "",
        f"Total cost: ${total:.2f}. Agreement is judged by a model against one review's "
        "conclusion; key papers are the review's most cited on-topic references, which can be "
        "older founding work rather than the evidence a good answer needs. Both are proxies, "
        "not ground truth. Agreement means less where the agent read the reference review.",
    ]
    report = "\n".join(lines)
    async with sessions() as session:
        evaluation = await session.get(EvaluationRun, evaluation_id)
        evaluation.report = report
        if status:
            evaluation.status = status
            evaluation.completed_at = datetime.now(UTC)
        await session.commit()
    return report
