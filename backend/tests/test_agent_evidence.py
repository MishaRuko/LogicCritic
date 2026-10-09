"""Certainty is earned: the evidence a conclusion rests on and the search behind it cap it."""

import itertools
import uuid

import pytest
from sqlalchemy import update

from app.agent import evidence
from app.agent.evidence import EvidenceSource, ceiling
from app.agent.toolbox import Toolbox
from app.database import engine, session_factory
from app.models import AgentEvent, AgentRun, Source
from tests.agent_helpers import make_world
from tests.test_agent_toolbox import box, call, check, reason, record

SOURCES = {
    "trial": ["Drug X reduced 28-day mortality by 30% in a randomised trial of adults."],
    "cohort": ["Adults taking drug X had lower mortality over ten years of follow-up."],
    "review": ["Pooled across eight trials, drug X lowered mortality (RR 0.75)."],
    "preprint": ["Drug X reduced 28-day mortality by 30% (preprint of the trial)."],
    "unrelated": ["Drug X is sold as a tablet."],
}
_seq = itertools.count(1000)


@pytest.fixture(autouse=True)
async def fresh_engine():
    await engine.dispose()
    yield
    await engine.dispose()


def study(n: int, kind: str = "scholarly", given: bool = False) -> EvidenceSource:
    return EvidenceSource(str(n), f"Study {n}", kind, given)


# -- the ceiling -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sources", "searches", "level"),
    [
        ([], 0, "speculative"),  # nothing cited: reasoning only
        ([study(1)], 0, "tentative"),
        ([study(1)], 9, "tentative"),  # searching widely does not make one source more
        ([study(1), study(2)], 1, "tentative"),  # one search is not a survey
        ([study(1), study(2)], 2, "supported"),
        ([study(1), study(2), study(3)], 3, "supported"),
        ([study(1), study(2), study(3)], 4, "established"),
        ([study(1, "systematic_review"), study(2)], 4, "established"),
        # Searching only for support is not a survey: Established needs a search against.
        ([study(1), study(2), study(3)], (9, False), "supported"),
        ([study(1, "web"), study(2, "web"), study(3, "web")], 4, "supported"),
        # Material a person supplied counts as looked-for, like a search.
        ([study(1, given=True), study(2, given=True)], 0, "supported"),
    ],
)
def test_the_ceiling_is_the_lowest_of_soundness_evidence_and_breadth(
    sources, searches, level
) -> None:
    count, against = searches if isinstance(searches, tuple) else (searches, True)
    assert ceiling("established", sources, count, against).level == level


def test_soundness_still_caps_it_and_a_retracted_source_allows_nothing() -> None:
    ample = [study(1), study(2), study(3)]
    assert ceiling("tentative", ample, 9).allowed() == ["tentative", "speculative"]
    assert ceiling("none", ample, 9).allowed() == []


def test_each_dimension_holding_it_down_says_what_would_raise_it() -> None:
    thin = ceiling("tentative", [study(1)], 1)
    assert thin.by == {
        "soundness": "tentative",
        "evidence": "tentative",
        "breadth": "tentative",
        "quality": "established",
    }
    advice = " ".join(thin.to_raise)
    assert "obligations" in advice and "second independent source" in advice
    assert "1 more search" in advice
    ample = [study(1), study(2), study(3)]
    assert ceiling("established", ample, 4, against=True).to_raise == []
    unopposed = " ".join(ceiling("established", ample, 4, against=False).to_raise)
    assert "purpose 'against'" in unopposed and "more search" not in unopposed


def test_older_runs_certainties_read_as_the_nearest_new_level() -> None:
    assert evidence.readable("conditional") == "Tentative"
    assert evidence.readable("hypothesis") == "Speculative"
    assert evidence.readable("supported") == "Supported"


# -- read off the graph ------------------------------------------------------------------------


async def describe(world, name: str, **fields) -> None:
    async with session_factory() as session:
        await session.execute(
            update(Source).where(Source.id == world.sources[name]).values(**fields)
        )
        await session.commit()


async def searched(
    world,
    papers: int,
    web: int = 0,
    run_id: uuid.UUID | None = None,
    purpose: str | None = None,
    tool: str = "search_papers",
) -> None:
    async with session_factory() as session:
        for n in range(papers):
            session.add(
                AgentEvent(
                    run_id=run_id or world.run_id,
                    seq=next(_seq),
                    type="tool_call",
                    payload={"name": tool, "input": {"query": f"drug X {n}", "purpose": purpose}},
                )
            )
        for n in range(web):
            session.add(
                AgentEvent(
                    run_id=run_id or world.run_id,
                    seq=next(_seq),
                    type="web_search",
                    payload={"query": f"drug X web {n}"},
                )
            )
        await session.commit()


async def test_one_source_and_no_search_caps_a_sound_conclusion_at_tentative() -> None:
    world = await make_world(sources=SOURCES)
    toolbox = box(world)
    claim = await record(toolbox, world, "Drug X reduced mortality in a randomised trial.", "trial")

    packet = await check(toolbox, claim)
    assert packet["obligations"] == []  # nothing wrong with it: it is just thin
    assert packet["can_finalize_as"] == ["tentative", "speculative"]
    assert packet["certainty_ceiling"]["by"] == {
        "soundness": "established",
        "evidence": "tentative",
        "breadth": "tentative",
        "quality": "established",
    }
    assert packet["assurance"]["level"] == "provisional"
    assert packet["assurance"]["holding_back"][0]["kind"] == "thin_evidence"

    refused = await call(toolbox, "finalize_conclusion", statement_id=claim, certainty="supported")
    assert refused["accepted"] is False
    assert any("second independent source" in item for item in refused["to_raise"])
    accepted = await call(toolbox, "finalize_conclusion", statement_id=claim, certainty="tentative")
    assert accepted["accepted"] is True
    assert accepted["evidence"] == "1 independent source, no review among them"


async def test_independent_sources_connected_by_reasoning_and_a_broad_search_earn_established() -> (
    None
):
    world = await make_world(sources=SOURCES)
    await describe(world, "trial", origin="amass", external_ids={"doi": "10.1/trial"})
    await describe(world, "preprint", origin="semantic_scholar", external_ids={"doi": "10.1/TRIAL"})
    await describe(world, "cohort", origin="amass", external_ids={"pmid": "42"})
    await describe(
        world,
        "review",
        title="Drug X and mortality: a systematic review and meta-analysis",
        origin="amass",
        external_ids={"doi": "10.1/review"},
    )
    toolbox = box(world)
    trial = await record(toolbox, world, "Drug X cut mortality by 30% in a trial.", "trial")
    preprint = await record(toolbox, world, "The trial's preprint reported the same.", "preprint")
    cohort = await record(toolbox, world, "Users of drug X died less often.", "cohort")
    await record(toolbox, world, "Drug X is a tablet.", "unrelated")  # read, never connected
    review = await record(toolbox, world, "Pooled trials show lower mortality.", "review")

    # Two connected sources, and the preprint is the trial again: Supported at most.
    await reason(toolbox, [trial, preprint], cohort)
    await searched(world, papers=2)
    packet = await check(toolbox, cohort)
    assert packet["certainty_ceiling"]["evidence"] == (
        "2 independent sources, no review among them, 2 searches"
    )
    assert packet["can_finalize_as"][0] == "supported"
    assert packet["assurance"]["level"] == "well_supported"

    # Connect the systematic review and search further, but only for support: not yet.
    await reason(toolbox, [cohort], review)
    await searched(world, papers=1, web=1)
    packet = await check(toolbox, review)
    assert packet["can_finalize_as"][0] == "supported"
    assert "against" in packet["certainty_ceiling"]["to_raise"][0]

    # Look for evidence against it: Established.
    await searched(world, papers=1, purpose="against")
    packet = await check(toolbox, review)
    assert packet["can_finalize_as"][0] == "established", packet["certainty_ceiling"]
    assert packet["certainty_ceiling"]["to_raise"] == []
    final = await call(toolbox, "finalize_conclusion", statement_id=review, certainty="established")
    assert final["accepted"] is True


async def test_a_retracted_source_and_the_agents_own_protocol_are_not_evidence() -> None:
    world = await make_world(sources=SOURCES, retracted=("cohort",))
    await describe(world, "review", metadata_={"parser": "agent_protocol_v1"})
    toolbox = box(world)
    trial = await record(toolbox, world, "Drug X cut mortality in a trial.", "trial")
    cohort = await record(toolbox, world, "Users of drug X died less often.", "cohort")
    protocol = await record(toolbox, world, "Give drug X daily.", "review")

    async with session_factory() as session:
        statements = {uuid.UUID(trial), uuid.UUID(cohort), uuid.UUID(protocol)}
        found = await evidence.cone_sources(session, statements)
    assert [s.source_id for s in found] == [str(world.sources["trial"])]


async def test_searches_from_earlier_runs_in_the_workspace_count_and_other_workspaces_do_not() -> (
    None
):
    world = await make_world(sources=SOURCES)
    other = await make_world(sources=SOURCES)
    async with session_factory() as session:
        earlier = AgentRun(
            workspace_id=world.workspace_id,
            goal_id=world.goal_id,
            idempotency_key=str(uuid.uuid4()),
            mode="guarded",
            model="claude-sonnet-5-5",
            status="succeeded",
            budgets={},
            usage={},
        )
        session.add(earlier)
        await session.commit()
    await searched(world, papers=1)
    await searched(world, papers=1, web=1, run_id=earlier.id)
    await searched(world, papers=1, tool="follow_citations")  # following citations is looking
    await searched(other, papers=5, purpose="against")
    async with session_factory() as session:
        effort = await evidence.search_effort(session, world.workspace_id)
    assert (effort.searches, effort.against) == (4, False)


@pytest.mark.parametrize(
    ("grade", "level"),
    [
        ("high", "established"),
        ("moderate", "supported"),
        ("low", "tentative"),
        ("very_low", "speculative"),
    ],
)
def test_the_quality_of_the_evidence_caps_the_certainty(grade, level) -> None:
    ample = [study(1), study(2), study(3)]
    rated = ceiling("established", ample, 4, against=True, quality=(grade, "high risk of bias"))
    assert rated.level == level and rated.by["quality"] == level
    if grade != "high":
        assert "high risk of bias" in rated.to_raise[0]


async def test_official_sources_count_as_strong_evidence() -> None:
    world = await make_world(sources=SOURCES)
    await describe(
        world, "trial", external_ids={"url": "https://www.who.int/publications/i/item/x"}
    )
    await describe(world, "cohort", external_ids={"url": "https://www.cdc.gov/x"})
    await describe(world, "unrelated", external_ids={"url": "https://someblog.example/x"})
    toolbox = box(world)
    ids = [
        await record(toolbox, world, f"Finding from {name}.", name)
        for name in ("trial", "cohort", "unrelated")
    ]
    async with session_factory() as session:
        found = await evidence.cone_sources(session, {uuid.UUID(i) for i in ids})
    kinds = sorted(s.kind for s in found)
    assert kinds == ["official", "official", "web"]
    assert "2 official sources" in ceiling("established", found, 4, True).summary()


@pytest.mark.usefixtures("ample_evidence")
@pytest.mark.parametrize(("grade", "level"), [("moderate", "supported"), ("high", "established")])
async def test_the_judge_rates_the_quality_of_the_evidence(grade, level) -> None:
    from app.services.judge import CriterionVerdict, JudgeOutput
    from tests.agent_helpers import FakeJudge

    def mixed(material):
        claim = material["claims"][0]["statement_id"]
        return JudgeOutput(
            criteria=[
                CriterionVerdict(index=0, met=True, rationale="", supporting_statement_ids=[claim])
            ],
            designs=[],
            evidence_certainty=grade,
            certainty_rationale="Starts high (trials); down one for imprecision.",
        )

    world = await make_world(sources=SOURCES, criteria=["Evidence on mortality"])
    judge = FakeJudge(verdict=mixed)
    toolbox = Toolbox(session_factory, world.run_id, None, judge)
    claim = await record(toolbox, world, "Drug X reduced mortality in a trial.", "trial")
    packet = await check(toolbox, claim)
    assert judge.materials[0]["conclusion"] == "Drug X reduced mortality in a trial."
    assert packet["certainty_ceiling"]["by"]["quality"] == level
    assert packet["can_finalize_as"][0] == level
