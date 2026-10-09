import pytest
from sqlalchemy import select

from app.database import engine, session_factory
from app.evaluations import open_search
from app.evaluations.open_search import (
    OpenJudgment,
    create_open_evaluation,
    discovery,
    render_open_report,
    run_open_evaluation,
    slots,
)
from app.models import AgentEvent, EvaluationOutput, EvaluationScore, Source
from tests.agent_helpers import make_world

KEY_PAPERS = [
    {"title": "A large randomised trial of drug X in adults", "doi": "10.1000/trial"},
    {"title": "Long-term follow-up of drug X: a cohort study", "pmid": "123"},
    {"title": "A paper the agent never sees at all in any search", "doi": "10.1000/unseen"},
]
CASE = {
    "question": "Does drug X reduce mortality?",
    "domain": "medicine",
    "reference": {"title": "Review", "conclusion": "Drug X does not reduce mortality."},
    "expected_sources": KEY_PAPERS,
}


@pytest.fixture(autouse=True)
async def fresh_engine():
    await engine.dispose()
    yield
    await engine.dispose()


def test_each_arm_runs_once_per_repeat() -> None:
    config = {"arms": {"quick": {}, "thorough": {}}, "repeats": 2}
    assert slots(config) == ["quick", "quick#2", "thorough", "thorough#2"]


async def test_cases_need_a_question_and_the_review_conclusion() -> None:
    with pytest.raises(ValueError, match="reference conclusion"):
        await create_open_evaluation("t", [{"question": "q?", "reference": {}}])


async def test_key_papers_count_as_found_from_search_results_and_read_from_sources() -> None:
    world = await make_world()
    async with session_factory() as session:
        # Read: a paper the agent imported (matched by DOI in its ids).
        session.add(
            Source(
                workspace_id=world.workspace_id,
                kind="web_page",
                origin="agent",
                title="Some page",
                mime_type="text/plain",
                original_filename="trial.pdf",
                storage_key="x",
                content_hash="h-read",
                external_ids={"doi": "10.1000/TRIAL"},
            )
        )
        # The agent's own protocol note is a source, but not evidence it read.
        session.add(
            Source(
                workspace_id=world.workspace_id,
                kind="agent_protocol",
                origin="agent",
                title="Protocol: give drug X",
                mime_type="text/markdown",
                original_filename="protocol.md",
                storage_key="y",
                content_hash="h-protocol",
                metadata_={"parser": "agent_protocol_v1"},
            )
        )
        # The review this case is judged against, read by the agent.
        session.add(
            Source(
                workspace_id=world.workspace_id,
                kind="amass_record",
                origin="amass",
                title="Drug X and mortality: a systematic review",
                mime_type="application/json",
                original_filename="review.json",
                storage_key="z",
                content_hash="h-review",
                external_ids={"doi": "10.1000/review"},
            )
        )
        # Found but not read: the cohort study appeared in a paper search.
        session.add(
            AgentEvent(
                run_id=world.run_id,
                seq=1,
                type="tool_result",
                payload={
                    "name": "search_papers",
                    "result": {"results": [{"title": KEY_PAPERS[1]["title"], "pmid": "123"}]},
                },
            )
        )
        await session.commit()
        output = EvaluationOutput(workspace_id=world.workspace_id, agent_run_id=world.run_id)
        found = await discovery(session, output, KEY_PAPERS, {"doi": "10.1000/review"})
    assert found == {
        "expected": 3,
        "found": 2,
        "read": 1,
        "sources_read": 2 + len(world.sources),  # the protocol note is not counted
        "paper_searches": 1,
        "read_a_review": True,
        "read_the_reference": True,
    }


async def test_a_run_answers_scores_and_reports_each_arm_and_resumes_without_rework(
    monkeypatch,
) -> None:
    world = await make_world()
    runs = []

    async def fake_slot(case, slot, config, *, sessions, client):
        runs.append(slot)
        async with sessions() as session:
            output = EvaluationOutput(
                evaluation_case_id=case.id,
                arm=slot,
                workspace_id=world.workspace_id,
                agent_run_id=world.run_id,
                status="succeeded",
                answer="Drug X does not reduce mortality.",
                usage={
                    "cost_usd": 1.0 if slot.startswith("thorough") else 0.25,
                    "turns": 12,
                    "certainty": "established" if slot.startswith("thorough") else "conditional",
                    "seconds": 300,
                },
            )
            session.add(output)
            await session.commit()
            return output

    async def fake_judge(client, model, system, material, schema, task, tally):
        assert set(material) == {"question", "review_conclusion", "answer"}  # nothing else
        return OpenJudgment(agreement="agrees", rationale="Same conclusion.")

    monkeypatch.setattr(open_search, "_run_slot", fake_slot)
    monkeypatch.setattr(open_search.scoring, "_ask", fake_judge)
    evaluation_id = await create_open_evaluation("pilot", [CASE], repeats=2)

    report = await run_open_evaluation(evaluation_id, client=object())
    assert sorted(runs) == ["quick", "quick#2", "thorough", "thorough#2"]
    assert "| quick | 2/2 | 100% |" in report and "| thorough | 2/2 | 100% |" in report
    assert "| thorough | established | 2 | 100% |" in report
    assert "same agreement in every repeat for 1/1 questions" in report

    # Resuming does no new agent runs and no new scoring.
    await run_open_evaluation(evaluation_id, client=object())
    assert len(runs) == 4
    async with session_factory() as session:
        assert len(list(await session.scalars(select(EvaluationScore)))) >= 4
    assert (await render_open_report(evaluation_id)).startswith("# pilot")


async def test_a_review_becomes_a_case_with_its_most_cited_references_as_key_papers() -> None:
    import json

    import httpx

    from app.evaluations.review_cases import review_case

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/doi:10.1/review"):
            return httpx.Response(
                200,
                text=json.dumps(
                    {
                        "display_name": "A review",
                        "doi": "https://doi.org/10.1/review",
                        "publication_year": 2024,
                        "abstract_inverted_index": {"No": [0], "effect.": [1]},
                        "referenced_works": ["https://openalex.org/W1", "https://openalex.org/W2"],
                    }
                ),
            )
        assert request.url.params["filter"] == "openalex:W1|W2"
        results = [
            {
                "display_name": "Less cited",
                "doi": None,
                "cited_by_count": 3,
                "ids": {},
                "type": "article",
            },
            {
                "display_name": "The PRISMA statement for reporting systematic reviews",
                "cited_by_count": 99999,
                "type": "article",
            },
            {
                "display_name": "Most cited",
                "doi": "https://doi.org/10.1/top",
                "cited_by_count": 900,
                "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/42"},
                "type": "article",
            },
        ]
        return httpx.Response(200, text=json.dumps({"results": results}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        case = await review_case(client, "10.1/review", key_papers=1)
    assert case["question"] is None  # written by a person, never invented here
    assert case["usable"] is False  # "No effect." is too short to be a stated conclusion
    assert case["reference"]["conclusion"] == "No effect."
    assert case["expected_sources"] == [
        {"title": "Most cited", "doi": "10.1/top", "pmid": "42", "year": None}
    ]


async def test_a_slot_is_not_mistaken_for_an_abandoned_run_while_it_starts(monkeypatch) -> None:
    """The worker's stale-run recovery runs every second; a just-started evaluation run must
    carry a heartbeat already, or recovery fails it while it is still researching."""
    from app.agent.runs import recover_stale_agent_runs
    from app.models import AgentRun, EvaluationCase

    recovered, hidden = [], []

    async def fake_execute(run_id, *, sessions, client, judge):
        await recover_stale_agent_runs()  # other tests' leftovers may be recovered; not this run
        async with sessions() as session:
            run = await session.get(AgentRun, run_id)
            recovered.append(run.status)
            hidden.append(run.budgets["hidden_source"])
            run.status, run.final_report = "succeeded", "Answer."
            await session.commit()

    monkeypatch.setattr(open_search, "execute_run", fake_execute)
    evaluation_id = await create_open_evaluation("start", [CASE])
    async with session_factory() as session:
        case = await session.scalar(
            select(EvaluationCase).where(EvaluationCase.evaluation_run_id == evaluation_id)
        )
    config = {"arms": {"quick": {"mode": "baseline", "depth": "quick"}}, "arm_model": None}
    output = await open_search._run_slot(
        case, "quick", config, sessions=session_factory, client=None
    )
    assert recovered == ["running"] and output.status == "succeeded"
    assert hidden == [{"title": "review"}]  # the reference review is kept out of reach


def test_certainty_is_compared_with_the_reviews_grade_rating() -> None:
    from app.evaluations.open_search import grade_section

    def fact(expected, said):
        return {"expected_certainty": expected, "certainty": said}

    rows = {
        "thorough": [
            fact("high", "established"),
            fact("high", "supported"),
            fact("low", "supported"),  # claims more than the review
            fact("very_low", "hypothesis"),  # an older run's label reads as speculative
            fact("moderate", "abstained"),  # no certainty to compare
        ]
    }
    text = "\n".join(grade_section(rows))
    assert "| thorough | high | 2 | established 1, supported 1 | 1/2 |" in text
    assert "| thorough | very low | 1 | speculative 1 | 1/1 |" in text
    assert "the same level as the review for 2/4, within one level for 4/4" in text
    assert "mean difference +0.0 levels" in text
