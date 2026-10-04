import json
import uuid

import pytest
from sqlalchemy import delete, select

from app.agent import guardrail
from app.agent.toolbox import Toolbox
from app.agent.web import FetchedPage
from app.config import get_settings
from app.database import engine, session_factory
from app.models import (
    AgentRun,
    AmassCacheEntry,
    Annotation,
    GraphEdge,
    ProofObligation,
    ResearchGoal,
    Source,
    Statement,
)
from app.services.amass import BiomedRecord
from tests.agent_helpers import claim_args, make_world


@pytest.fixture(autouse=True)
async def fresh_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path))
    await engine.dispose()
    async with session_factory() as session:  # the Amass cache is global by design
        await session.execute(delete(AmassCacheEntry))
        await session.commit()
    yield
    await engine.dispose()


def box(world, amass=None) -> Toolbox:
    return Toolbox(session_factory, world.run_id, amass)


async def call(toolbox: Toolbox, name: str, tool_id: str | None = None, **arguments) -> dict:
    return await toolbox.call(name, arguments, tool_id or f"toolu_{uuid.uuid4().hex[:8]}")


async def record(toolbox, world, text, source, index=0, **extra) -> str:
    result = await call(
        toolbox, "record_claim", **claim_args(world, text, source, index=index, **extra)
    )
    assert "statement_id" in result, result
    return result["statement_id"]


async def reason(toolbox, premises: list[str], conclusion: str, **extra) -> str:
    arguments = {
        "premise_ids": premises,
        "conclusion_id": conclusion,
        "explanation": "Because the premises establish it.",
        "scope_change": None,
        "revises_step_id": None,
        **extra,
    }
    result = await call(toolbox, "record_reasoning", **arguments)
    assert "step_id" in result, result
    return result["step_id"]


SOURCES = {
    "trial": ["Drug X reduced 28-day mortality by 30% in a randomised trial of adults."],
    "mouse": ["Drug X shrank tumours in mice."],
}


# -- recording ---------------------------------------------------------------------------------


async def test_a_claim_is_recorded_with_agent_provenance_and_annotations() -> None:
    world = await make_world(criteria=["human RCT evidence"], sources=SOURCES)
    statement_id = await record(
        box(world),
        world,
        "Drug X lowered 28-day mortality in adults.",
        "trial",
        claim_strength="causal",
        causal_support={"design": "randomised_trial", "justification": "randomly assigned"},
        scope={"population": "adults", "model": "human", "endpoint": "28-day mortality"},
        criteria_satisfied=[0],
    )
    async with session_factory() as session:
        statement = await session.get(Statement, uuid.UUID(statement_id))
        annotations = {
            a.type: a.value
            for a in await session.scalars(
                select(Annotation).where(Annotation.subject_id == statement.id)
            )
        }
    assert statement.provenance["actor_type"] == "agent"
    assert statement.provenance["run_id"] == str(world.run_id)
    assert statement.lifecycle == "proposed"
    assert annotations["claim_strength"] == {"value": "causal"}
    assert annotations["causal_support"] == {
        "supported": True,
        "design": "randomised_trial",
        "justification": "randomly assigned",
    }
    assert annotations["custom:scope"]["model"] == "human"
    assert annotations["custom:satisfies_criteria"] == {"indexes": [0]}


async def test_bad_claims_come_back_as_errors_the_agent_can_read() -> None:
    world = await make_world(criteria=["a criterion"], sources=SOURCES)
    toolbox = box(world)

    not_an_id = await call(
        toolbox, "record_claim", **claim_args(world, "x", "trial", excerpt_ids=["nope"])
    )
    assert "excerpt_ids must be an id returned by an earlier tool call" in not_an_id["error"]

    unknown = await call(
        toolbox, "record_claim", **claim_args(world, "x", "trial", excerpt_ids=[str(uuid.uuid4())])
    )
    assert "do not exist in this workspace" in unknown["error"]

    bad_criterion = await call(
        toolbox, "record_claim", **claim_args(world, "x", "trial", criteria_satisfied=[5])
    )
    assert "no completion criterion 5" in bad_criterion["error"]

    missing_field = await toolbox.call("record_claim", {"text": "x"}, "t1")
    assert "Invalid arguments" in missing_field["error"]


async def test_a_causal_claim_must_say_what_makes_it_causal() -> None:
    world = await make_world(sources=SOURCES)
    result = await call(
        box(world),
        "record_claim",
        **claim_args(world, "Drug X reduced mortality.", "trial", claim_strength="causal"),
    )
    assert "needs causal_support" in result["error"] and "associative" in result["error"]


async def test_a_randomised_trial_can_support_a_causal_claim_through_to_established() -> None:
    # The first live run: two large RCTs were capped at 'conditional' because the agent had no
    # way to say why its causal wording was warranted.
    world = await make_world(sources=SOURCES)
    toolbox = box(world)
    claim = await record(
        toolbox,
        world,
        "Drug X reduced 28-day mortality.",
        "trial",
        claim_strength="causal",
        causal_support={"design": "randomised_trial", "justification": "randomised trial"},
    )
    packet = await check(toolbox, claim)
    assert packet["obligations"] == [] and "established" in packet["can_finalize_as"]


@pytest.mark.parametrize("design", ["observational", "animal_or_in_vitro", "other"])
async def test_non_causal_designs_still_cannot_carry_a_causal_claim(design) -> None:
    world = await make_world(sources=SOURCES)
    toolbox = box(world)
    claim = await record(
        toolbox,
        world,
        "Drug X lowers mortality.",
        "trial",
        claim_strength="causal",
        causal_support={"design": design, "justification": "a cohort of patients"},
    )
    packet = await check(toolbox, claim)
    assert kinds(packet) == ["causality_overclaim"]
    assert "established" not in packet["can_finalize_as"]


async def test_replaying_the_same_tool_call_does_not_duplicate_the_claim() -> None:
    world = await make_world(sources=SOURCES)
    toolbox = box(world)
    args = claim_args(world, "Drug X works.", "trial")
    first = await toolbox.call("record_claim", args, "toolu_same")
    again = await toolbox.call("record_claim", args, "toolu_same")
    assert first["statement_id"] == again["statement_id"]
    async with session_factory() as session:
        count = len(
            list(
                await session.scalars(
                    select(Statement).where(Statement.workspace_id == world.workspace_id)
                )
            )
        )
    assert count == 1


async def test_reasoning_records_scope_changes_and_revisions() -> None:
    world = await make_world(sources=SOURCES)
    toolbox = box(world)
    premise = await record(toolbox, world, "Drug X shrank tumours in mice.", "mouse")
    conclusion = await record(
        toolbox, world, "Drug X will help patients.", "mouse", role="conclusion"
    )

    first = await call(
        toolbox,
        "record_reasoning",
        premise_ids=[premise],
        conclusion_id=conclusion,
        explanation="Mice results carry over.",
        scope_change={
            "from_scope": "mouse, tumour size",
            "to_scope": "human, survival",
            "justified": False,
        },
        revises_step_id=None,
    )
    second = await reason(toolbox, [premise], conclusion, revises_step_id=first["step_id"])

    async with session_factory() as session:
        scope = await session.scalar(
            select(Annotation).where(
                Annotation.type == "scope_transition", Annotation.workspace_id == world.workspace_id
            )
        )
        revision = await session.scalar(
            select(GraphEdge).where(
                GraphEdge.relation == "revises", GraphEdge.workspace_id == world.workspace_id
            )
        )
    assert scope.value == {
        "from": "mouse, tumour size",
        "to": "human, survival",
        "justified": False,
    }
    assert (
        str(revision.source_node_id) == second and str(revision.target_node_id) == first["step_id"]
    )


async def test_a_step_cannot_conclude_its_own_premise() -> None:
    world = await make_world(sources=SOURCES)
    toolbox = box(world)
    claim = await record(toolbox, world, "Drug X works.", "trial")
    result = await call(
        toolbox,
        "record_reasoning",
        premise_ids=[claim],
        conclusion_id=claim,
        explanation="Circular.",
        scope_change=None,
        revises_step_id=None,
    )
    assert "conclusion as a premise" in result["error"]


# -- reading -----------------------------------------------------------------------------------


async def test_read_source_pages_through_long_sources_and_refuses_foreign_ones() -> None:
    world = await make_world(
        sources={"long": [f"Paragraph {n}. " + "word " * 600 for n in range(12)]}
    )
    other = await make_world(sources={"theirs": ["Secret."]})
    toolbox = box(world)

    page = await call(toolbox, "read_source", source_id=str(world.sources["long"]), offset=0)
    assert 0 < len(page["excerpts"]) < 12 and page["next_offset"] == len(page["excerpts"])
    assert page["excerpts"][0]["excerpt_id"] == str(world.excerpts["long"][0])

    rest = await call(
        toolbox, "read_source", source_id=str(world.sources["long"]), offset=page["next_offset"]
    )
    assert rest["excerpts"][0]["n"] == page["next_offset"]

    foreign = await call(toolbox, "read_source", source_id=str(other.sources["theirs"]), offset=0)
    assert foreign["error"] == "No such source in this workspace."
    assert (await call(toolbox, "read_source", source_id="nope", offset=0))["error"].startswith(
        "source_id must be"
    )


async def test_fetch_url_saves_a_web_page_as_a_source_once(monkeypatch) -> None:
    world = await make_world()
    html = (
        b"<html><head><title>WHO guidance</title></head><body><h1>Recommendation</h1>"
        b"<p>The panel recommends against the drug in hospitalised patients.</p></body></html>"
    )
    calls = []

    async def fake_fetch(url, **kwargs):
        calls.append(kwargs)
        return FetchedPage(url, "https://who.example/guidance", 200, "text/html", html)

    monkeypatch.setattr("app.agent.toolbox.fetch_public", fake_fetch)
    toolbox = box(world)

    first = await call(toolbox, "fetch_url", url="https://who.example/guidance")
    again = await call(toolbox, "fetch_url", url="https://who.example/guidance")

    assert first["title"] == "WHO guidance" and first["already_fetched"] is False
    assert again["already_fetched"] is True and again["source_id"] == first["source_id"]
    assert calls[0]["user_agent"] == get_settings().agent_user_agent
    page = await call(toolbox, "read_source", source_id=first["source_id"], offset=0)
    assert [e["text"] for e in page["excerpts"]] == [
        "# Recommendation",
        "The panel recommends against the drug in hospitalised patients.",
    ]
    async with session_factory() as session:
        source = await session.get(Source, uuid.UUID(first["source_id"]))
    assert (source.kind, source.origin) == ("web_page", "agent")
    assert source.external_ids == {"url": "https://who.example/guidance"}


async def test_fetch_failures_are_reported_to_the_agent(monkeypatch) -> None:
    from app.agent.web import FetchError

    async def refuse(url, **kwargs):
        raise FetchError("localhost is not a public internet address, so it was not fetched.")

    monkeypatch.setattr("app.agent.toolbox.fetch_public", refuse)
    result = await call(box(await make_world()), "fetch_url", url="http://localhost/")
    assert "not a public internet address" in result["error"]


class FakeAmass:
    def __init__(self, retracted: bool = False) -> None:
        self.retracted = retracted
        self.searches = []

    async def search_biomedcore(self, query, limit=10, **filters):
        self.searches.append((query, limit, filters))
        return [
            BiomedRecord.model_validate(
                {
                    "amassId": "AMBC_1",
                    "title": "Trial of X",
                    "abstract": "a" * 900,
                    "isRetracted": self.retracted,
                }
            )
        ]

    async def lookup_biomedcore(self, *, pmid=None, doi=None):
        return ["AMBC_1"]

    async def get_biomedcore(self, amass_id, *, fulltext=False):
        return {
            "amassId": amass_id, "pmid": "1", "doi": "10.1/x", "title": "Trial of X",
            "abstract": "Drug X reduced mortality.", "isRetracted": self.retracted,
            "publicationDate": "2020-01-01", "fulltext": "# Results\n\nMortality fell.",
        }  # fmt: skip


async def test_paper_search_and_reading_through_amass() -> None:
    world = await make_world()
    amass = FakeAmass()
    toolbox = box(world, amass)

    found = await call(
        toolbox,
        "search_papers",
        query="drug x",
        limit=99,
        published_after=None,
        exclude_retracted=True,
    )
    assert amass.searches[0] == (
        "drug x",
        15,
        {"min_publication_date": None, "is_retracted": False},
    )
    assert (
        found["results"][0]["amass_id"] == "AMBC_1" and len(found["results"][0]["abstract"]) == 700
    )

    paper = await call(toolbox, "read_paper", amass_id="AMBC_1", pmid=None, doi=None)
    assert (
        paper["retracted"] is False
        and paper["retraction_warning"] is None
        and paper["excerpts"] >= 3
    )
    page = await call(toolbox, "read_source", source_id=paper["source_id"], offset=0)
    assert any("Mortality fell." in e["text"] for e in page["excerpts"])


async def test_a_retracted_paper_is_flagged_when_read() -> None:
    world = await make_world()
    paper = await call(
        box(world, FakeAmass(retracted=True)), "read_paper", amass_id="AMBC_1", pmid=None, doi=None
    )
    assert paper["retracted"] is True and "retracted" in paper["retraction_warning"]


async def test_paper_tools_explain_when_amass_is_not_configured() -> None:
    world = await make_world()
    result = await call(
        box(world),
        "search_papers",
        query="x",
        limit=3,
        published_after=None,
        exclude_retracted=False,
    )
    assert "no Amass API key" in result["error"]
    both = await call(box(world, FakeAmass()), "read_paper", amass_id="AMBC_1", pmid="1", doi=None)
    assert "exactly one" in both["error"]


async def test_an_unknown_tool_is_an_error_not_a_crash() -> None:
    assert (await call(box(await make_world()), "delete_everything"))[
        "error"
    ] == "Unknown tool 'delete_everything'."


# -- the guardrail -----------------------------------------------------------------------------


async def check(toolbox, statement_id) -> dict:
    return await call(toolbox, "check_conclusion", statement_id=statement_id)


def kinds(packet: dict) -> list[str]:
    return sorted(o["kind"] for o in packet["obligations"])


async def test_a_well_supported_conclusion_can_be_established() -> None:
    world = await make_world(sources=SOURCES)
    toolbox = box(world)
    claim = await record(toolbox, world, "Drug X reduced mortality in a randomised trial.", "trial")

    packet = await check(toolbox, claim)
    assert packet["obligations"] == [] and packet["can_finalize_as"] == [
        "established",
        "conditional",
        "hypothesis",
    ]
    assert packet["evidence_chain"][0]["from_sources"][0]["title"] == "trial"

    final = await call(toolbox, "finalize_conclusion", statement_id=claim, certainty="established")
    assert final["accepted"] is True and final["caveats"] == []
    async with session_factory() as session:
        run = await session.get(AgentRun, world.run_id)
        goal = await session.get(ResearchGoal, world.goal_id)
    assert (run.final_statement_id, run.certainty, goal.status) == (
        uuid.UUID(claim),
        "established",
        "answered",
    )


async def test_a_conclusion_on_a_retracted_source_cannot_be_established_or_conditional() -> None:
    world = await make_world(sources=SOURCES, retracted=("trial",))
    toolbox = box(world)
    claim = await record(toolbox, world, "Drug X reduced mortality.", "trial")

    packet = await check(toolbox, claim)
    assert kinds(packet) == ["invalidated_source"]
    assert packet["obligations"][0]["severity"] == "critical"
    assert packet["evidence_chain"][0]["from_sources"][0]["retracted"] is True
    assert packet["can_finalize_as"] == ["hypothesis"]

    for certainty in ("established", "conditional"):
        refused = await call(
            toolbox, "finalize_conclusion", statement_id=claim, certainty=certainty
        )
        assert (
            refused["accepted"] is False
            and refused["obligations"][0]["kind"] == "invalidated_source"
        )
        assert "hypothesis" in refused["reason"]
    allowed = await call(toolbox, "finalize_conclusion", statement_id=claim, certainty="hypothesis")
    assert allowed["accepted"] is True and allowed["caveats"][0]["kind"] == "invalidated_source"


async def add_rebuttal(world, opposing: str, supported: bool, concluded: str) -> None:
    async with session_factory() as session:
        session.add(
            GraphEdge(
                workspace_id=world.workspace_id,
                source_node_kind="statement",
                source_node_id=uuid.UUID(opposing),
                relation="rebuts",
                target_node_kind="statement",
                target_node_id=uuid.UUID(concluded),
                metadata_={"audit_verdict": "supported" if supported else "needs_review"},
            )
        )
        await session.commit()


async def test_opposing_evidence_blocks_until_the_agent_engages_with_it() -> None:
    world = await make_world(
        sources={
            "for": ["Drug X lowered viral load."],
            "against": ["Drug X did not reduce mortality."],
        }
    )
    toolbox = box(world)
    claim = await record(toolbox, world, "Drug X is effective.", "for")
    opposing = await record(toolbox, world, "Drug X did not reduce mortality.", "against")
    await add_rebuttal(world, opposing, supported=True, concluded=claim)

    blocked = await check(toolbox, claim)
    assert (
        kinds(blocked) == ["unresolved_conflict"]
        and "established" not in blocked["can_finalize_as"]
    )
    assert "did not reduce mortality" in blocked["obligations"][0]["description"]
    assert (
        "does not" not in blocked["obligations"][0]["required_condition"].split(":")[0]
    )  # says what, not how

    narrowed = await record(
        toolbox, world, "Drug X lowered viral load, but not mortality.", "for", role="conclusion"
    )
    await reason(toolbox, [claim, opposing], narrowed)
    cleared = await check(toolbox, narrowed)
    assert cleared["obligations"] == [] and "established" in cleared["can_finalize_as"]


async def test_a_conflict_the_audit_doubts_is_advisory_only() -> None:
    world = await make_world(sources={"for": ["A."], "against": ["B."]})
    toolbox = box(world)
    claim = await record(toolbox, world, "Drug X is effective.", "for")
    opposing = await record(toolbox, world, "Drug X may not be.", "against")
    await add_rebuttal(world, opposing, supported=False, concluded=claim)

    packet = await check(toolbox, claim)
    assert [(o["kind"], o["severity"]) for o in packet["obligations"]] == [
        ("possible_conflict", "advisory")
    ]
    assert "established" in packet["can_finalize_as"]


async def test_completion_criteria_must_be_met_by_a_sound_claim() -> None:
    world = await make_world(
        criteria=["Evidence from a human randomised trial", "Evidence on safety"],
        sources={"trial": ["Mortality fell."], "retracted": ["Safe."]},
        retracted=("retracted",),
    )
    toolbox = box(world)
    efficacy = await record(
        toolbox, world, "Mortality fell in a human RCT.", "trial", criteria_satisfied=[0]
    )
    packet = await check(toolbox, efficacy)
    assert kinds(packet) == [
        "unmet_criteria",
    ]
    assert "criterion 1" in packet["obligations"][0]["description"]

    safety = await record(toolbox, world, "The drug is safe.", "retracted", criteria_satisfied=[1])
    conclusion = await record(
        toolbox, world, "Drug X works and is safe.", "trial", role="conclusion"
    )
    await reason(toolbox, [efficacy, safety], conclusion)
    packet = await check(toolbox, conclusion)
    # the safety claim is tagged for criterion 1 but rests on a retracted source: it does not count
    assert "unmet_criteria" in kinds(packet) and "invalidated_source" in kinds(packet)


async def test_agent_obligations_are_stored_and_close_when_met() -> None:
    world = await make_world(criteria=["human RCT"], sources=SOURCES)
    toolbox = box(world)
    claim = await record(toolbox, world, "Drug X works.", "trial", role="conclusion")
    await check(toolbox, claim)

    async def states():
        async with session_factory() as session:
            rows = await session.scalars(
                select(ProofObligation).where(ProofObligation.workspace_id == world.workspace_id)
            )
            return sorted((r.kind, r.status, str(r.blocks_statement_id) == claim) for r in rows)

    assert await states() == [("unmet_criteria", "open", True)]

    evidence = await record(
        toolbox, world, "Drug X works in a human RCT.", "trial", criteria_satisfied=[0]
    )
    await reason(toolbox, [evidence], claim)
    cleared = await check(toolbox, claim)

    assert cleared["obligations"] == []
    assert await states() == [("unmet_criteria", "resolved", True)]


async def test_a_flagged_reasoning_step_blocks_until_it_is_replaced(monkeypatch) -> None:
    world = await make_world(sources=SOURCES)
    monkeypatch.setattr(get_settings(), "claude_api_key", "test-key")
    flagged: list[uuid.UUID] = []

    async def fake_critic(session, workspace_id, request, step_ids=None, judge=None):
        # A critic that objects to the first step it sees (the one with a single premise).
        for step_id in step_ids if not flagged else []:
            session.add(Annotation(
                workspace_id=workspace_id, subject_type="reasoning_step", subject_id=step_id,
                type="required_premise",
                value={"description": "Needs a human trial.", "satisfied": False},
                provenance={"actor_type": "critic", "actor_id": "fake"}, status="proposed",
            ))  # fmt: skip
            flagged.append(step_id)
        await session.commit()

    async def no_synthesis(*args, **kwargs):
        from fastapi import HTTPException

        raise HTTPException(
            status_code=422, detail="Workspace needs statements from at least two sources"
        )

    monkeypatch.setattr(guardrail, "check_arguments", fake_critic)
    monkeypatch.setattr(guardrail, "synthesize_workspace", no_synthesis)
    toolbox = box(world)

    premise = await record(toolbox, world, "Drug X shrank tumours in mice.", "mouse")
    conclusion = await record(
        toolbox, world, "Drug X will help patients.", "mouse", role="conclusion"
    )
    weak = await reason(toolbox, [premise], conclusion)

    blocked = await check(toolbox, conclusion)
    assert kinds(blocked) == ["missing_premise"] and blocked["obligations"][0]["applies_to"] == weak

    human = await record(toolbox, world, "Drug X reduced mortality in a human trial.", "trial")
    await reason(toolbox, [premise, human], conclusion, revises_step_id=weak)
    cleared = await check(toolbox, conclusion)
    assert "missing_premise" not in kinds(cleared)
    assert len(flagged) == 1  # it audited the first step once and never re-audited it


async def test_checking_an_unknown_or_foreign_statement_is_a_readable_error() -> None:
    world = await make_world(sources=SOURCES)
    other = await make_world(sources={"x": ["y"]})
    foreign = await record(box(other), other, "Theirs.", "x")
    for statement_id in (str(uuid.uuid4()), foreign):
        result = await check(box(world), statement_id)
        assert "No recorded statement" in result["error"]
    assert "must be an id" in (await check(box(world), "nope"))["error"]


def test_tool_results_are_plain_json() -> None:
    from app.agent.toolbox import tool_result_text

    text = tool_result_text({"a": [1, {"b": uuid.UUID(int=5)}]})
    assert json.loads(text)["a"][1]["b"] == str(uuid.UUID(int=5))
    assert tool_result_text({"x": "y" * 100}, limit=20).endswith('"truncated"')
