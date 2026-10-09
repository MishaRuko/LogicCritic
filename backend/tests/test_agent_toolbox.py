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
from app.services.literature import LiteratureError, Paper
from tests.agent_helpers import claim_args, make_world

# These test the verifier's rules; the evidence ceiling is tested in test_agent_evidence.py.
pytestmark = pytest.mark.usefixtures("ample_evidence")


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


async def test_a_page_with_nul_characters_is_saved_without_them(monkeypatch) -> None:
    async def fetch(url, **kwargs):
        return FetchedPage(
            url,
            url,
            200,
            "text/plain",
            b"Mortality\x00 fell by 30% in the treated group over the 28 days of follow-up.",
        )

    monkeypatch.setattr("app.agent.toolbox.fetch_public", fetch)
    toolbox = box(await make_world())
    result = await call(toolbox, "fetch_url", url="https://nul.example/a.txt")
    page = await call(toolbox, "read_source", source_id=result["source_id"], offset=0)
    assert [e["text"] for e in page["excerpts"]] == [
        "Mortality fell by 30% in the treated group over the 28 days of follow-up."
    ]


async def test_an_unexpected_failure_is_a_tool_error_not_a_crashed_run(monkeypatch) -> None:
    async def broken_store(*args, **kwargs):
        raise RuntimeError("database refused the row")

    async def fetch(url, **kwargs):
        return FetchedPage(
            url,
            url,
            200,
            "text/plain",
            b"Some text that is long enough to be a real page, so it gets as far as saving.",
        )

    monkeypatch.setattr("app.agent.toolbox.fetch_public", fetch)
    monkeypatch.setattr("app.agent.toolbox.store_source", broken_store)
    toolbox = box(await make_world())
    result = await call(toolbox, "fetch_url", url="https://broken.example/")
    assert result == {"error": "fetch_url failed unexpectedly (RuntimeError); try another way."}
    # The toolbox still works afterwards.
    assert "error" not in await call(toolbox, "graph_overview")


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
        found["results"][0]["amass_id"] == "AMBC_1" and len(found["results"][0]["abstract"]) == 600
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


class FakeLiterature:
    """Semantic Scholar and arXiv stand-ins. `papers` maps a lookup id to the paper found."""

    def __init__(self, papers: dict[str, Paper] | None = None, arxiv_down: bool = False) -> None:
        self.papers = papers or {}
        self.arxiv_down = arxiv_down
        self.lookups = []

    async def search_semantic_scholar(self, query, limit, published_after=None):
        return [
            Paper(["semantic_scholar"], "Trial of X", ids={"doi": "10.1/X", "pmid": "1"}),
            Paper(["semantic_scholar"], "Cohort of X", ids={"semantic_scholar_id": "s2b"}),
        ]

    async def search_arxiv(self, query, limit, published_after=None):
        if self.arxiv_down:
            raise LiteratureError("arXiv returned 503.")
        return [Paper(["arxiv"], "A model of X", ids={"arxiv_id": "2601.00001"})]

    async def lookup_semantic_scholar(self, paper_id):
        self.lookups.append(paper_id)
        return self.papers.get(paper_id)

    async def citations(self, paper_id, direction, limit, about=None):
        self.lookups.append((paper_id, direction, limit, about))
        return [Paper(["semantic_scholar"], f"{direction} of {paper_id}", ids={"pmid": "7"})]


async def test_following_citations_normalises_the_id_and_returns_readable_rows() -> None:
    world = await make_world()
    literature = FakeLiterature()
    toolbox = Toolbox(session_factory, world.run_id, None, None, literature)
    result = await call(
        toolbox,
        "follow_citations",
        paper="doi: 10.1/review",
        direction="references",
        about="drug X trial",
        limit=50,
    )
    assert literature.lookups == [("DOI:10.1/review", "references", 20, "drug X trial")]
    assert result["direction"] == "references"
    assert result["results"][0]["title"] == "references of DOI:10.1/review"
    assert result["results"][0]["pmid"] == "7"  # an id read_paper accepts


async def test_paper_search_merges_the_indexes_and_reports_one_that_failed() -> None:
    world = await make_world()
    toolbox = Toolbox(
        session_factory, world.run_id, FakeAmass(), None, FakeLiterature(arxiv_down=True)
    )
    found = await call(
        toolbox, "search_papers", query="x", limit=10, published_after=None,
        exclude_retracted=False, sources=None,
    )  # fmt: skip
    # Amass's "Trial of X" and Semantic Scholar's (same title) are one result found in both.
    assert [(r["title"], r["found_in"]) for r in found["results"]] == [
        ("Trial of X", ["amass", "semantic_scholar"]),
        ("Cohort of X", ["semantic_scholar"]),
    ]
    assert found["results"][0]["amass_id"] == "AMBC_1" and found["results"][0]["doi"] == "10.1/X"
    assert found["unavailable"] == {"arxiv": "arXiv returned 503."}

    only_arxiv = await call(
        Toolbox(session_factory, world.run_id, None, None, FakeLiterature()), "search_papers",
        query="x", limit=10, published_after=None, exclude_retracted=False, sources=["arxiv"],
    )  # fmt: skip
    assert [r["arxiv_id"] for r in only_arxiv["results"]] == ["2601.00001"]


def paper_ids(**given) -> dict:
    """read_paper arguments: every id null except the ones given."""
    return {"amass_id": None, "pmid": None, "doi": None, **given}


def _page(title: str, body: str):
    def html(url: str) -> bytes:
        return f"<html><head><title>{title}</title></head><body><p>{body}</p><p>{url}</p>".encode()

    return lambda url, **kwargs: _async(FetchedPage(url, url, 200, "text/html", html(url)))


async def _async(value):
    return value


async def test_a_paper_amass_lacks_is_read_from_its_open_access_copy(monkeypatch) -> None:
    world = await make_world()
    fetched = []
    page = _page("Cohort", "Mortality was unchanged in the cohort.")
    monkeypatch.setattr(
        "app.agent.toolbox.fetch_public", lambda url, **kw: fetched.append(url) or page(url, **kw)
    )
    paper = Paper(
        ["semantic_scholar"], "Cohort of X", open_access_pdf="https://oa.example/c.pdf",
        ids={"semantic_scholar_id": "s2b", "doi": "10.2/c"},
    )  # fmt: skip
    literature = FakeLiterature({"DOI:10.2/c": paper})
    toolbox = Toolbox(session_factory, world.run_id, None, None, literature)

    result = await call(toolbox, "read_paper", **paper_ids(doi="10.2/c"))
    assert fetched == ["https://oa.example/c.pdf"] and result["title"] == "Cohort of X"
    assert result["ids"]["doi"] == "10.2/c" and "abstract_only" not in result
    text = await call(toolbox, "read_source", source_id=result["source_id"], offset=0)
    assert any("unchanged" in e["text"] for e in text["excerpts"])

    arxiv = await call(toolbox, "read_paper", **paper_ids(arxiv_id="2601.00001"))
    assert fetched[-1] == "https://arxiv.org/pdf/2601.00001" and arxiv["ids"]["arxiv_id"]


async def test_a_paper_with_no_open_full_text_is_saved_as_its_abstract() -> None:
    world = await make_world()
    paper = Paper(
        ["semantic_scholar"], "Paywalled trial", abstract="Drug X lowered mortality by 12%.",
        ids={"semantic_scholar_id": "s2c", "pmid": "77"},
    )  # fmt: skip
    toolbox = Toolbox(session_factory, world.run_id, None, None, FakeLiterature({"PMID:77": paper}))
    result = await call(toolbox, "read_paper", **paper_ids(pmid="77"))
    assert "abstract" in result["abstract_only"]
    async with session_factory() as session:
        source = await session.get(Source, uuid.UUID(result["source_id"]))
    assert (source.kind, source.origin) == ("paper_abstract", "semantic_scholar")
    text = await call(toolbox, "read_source", source_id=result["source_id"], offset=0)
    assert [e["text"] for e in text["excerpts"]] == [
        "Paywalled trial",
        "Drug X lowered mortality by 12%.",
    ]

    missing = await call(toolbox, "read_paper", **paper_ids(semantic_scholar_id="nope"))
    assert "No index has a paper" in missing["error"]


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
        "supported",
        "tentative",
        "speculative",
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


async def test_no_conclusion_may_rest_on_a_retracted_source_not_even_a_speculative_one() -> None:
    world = await make_world(sources=SOURCES, retracted=("trial",))
    toolbox = box(world)
    claim = await record(toolbox, world, "Drug X reduced mortality.", "trial")

    packet = await check(toolbox, claim)
    assert kinds(packet) == ["invalidated_source"]
    assert packet["obligations"][0]["severity"] == "critical"
    assert packet["evidence_chain"][0]["from_sources"][0]["retracted"] is True
    assert packet["can_finalize_as"] == []

    for certainty in ("established", "supported", "tentative", "speculative"):
        refused = await call(
            toolbox, "finalize_conclusion", statement_id=claim, certainty=certainty
        )
        assert (
            refused["accepted"] is False
            and refused["obligations"][0]["kind"] == "invalidated_source"
        )
        assert "abstain" in refused["reason"]


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
    # the agent is told what the critic found missing, and how to supply it
    assert "Needs a human trial." in blocked["obligations"][0]["description"]
    assert "revises_step_id" in blocked["obligations"][0]["required_condition"]
    assert "weighing field" in blocked["obligations"][0]["required_condition"]

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


async def test_bot_checks_and_empty_pages_are_refused_not_saved(monkeypatch) -> None:
    pages = {
        "https://journal.example/captcha": (
            b"<html><head><title>Checking your browser - reCAPTCHA</title></head>"
            b"<body><p>Checking your browser before accessing journal.example ...</p></body></html>"
        ),
        "https://journal.example/shell": (
            b"<html><head><title>App</title></head><body></body></html>"
        ),
    }

    async def fetch(url, **kwargs):
        return FetchedPage(url, url, 200, "text/html", pages[url])

    monkeypatch.setattr("app.agent.toolbox.fetch_public", fetch)
    toolbox = box(await make_world())
    blocked = await call(toolbox, "fetch_url", url="https://journal.example/captcha")
    assert "bot check" in blocked["error"] and "read_paper" in blocked["error"]
    empty = await call(toolbox, "fetch_url", url="https://journal.example/shell")
    assert "almost no readable text" in empty["error"]


def test_a_pdf_without_a_real_title_is_named_from_its_opening_text() -> None:
    from app.agent.toolbox import _opening_title
    from app.services.pdf_ingestion import JUNK_TITLE
    from app.services.text_ingestion import ParsedExcerpt

    assert JUNK_TITLE.search("Template for Electronic Submission to ACS Journals")
    assert JUNK_TITLE.search("Microsoft Word - draft3.docx")
    assert not JUNK_TITLE.search("Long-Term Employment Effects of the Minimum Wage in Germany")
    opening = ParsedExcerpt(
        text="Long-Term Employment Effects of the Minimum Wage in Germany: New Data and "
        "Estimators Marco Caliendo University of Potsdam, CEPA, IZA, BSE, DIW, IAB",
        sequence=0,
        locator={},
    )
    title = _opening_title([opening])
    assert title.startswith("Long-Term Employment Effects of the Minimum Wage in Germany")
    assert title.endswith("…") and len(title) <= 141


async def reasoned_causal_conclusion(world, toolbox, premise_design: str | None) -> dict:
    extra = (
        {
            "claim_strength": "causal",
            "causal_support": {"design": premise_design, "justification": "as the text says"},
        }
        if premise_design
        else {"claim_strength": "associative"}
    )
    premise = await record(
        toolbox, world, "Drug X was followed by lower mortality.", "trial", **extra
    )
    conclusion = await call(
        toolbox,
        "record_claim",
        **claim_args(
            world,
            "Drug X lowers mortality in adults.",
            "trial",
            assertion_mode="asserted",
            role="conclusion",
            claim_strength="causal",
            causal_support={
                "design": "meta_analysis_of_randomised_trials",
                "justification": "from the premises",
            },
        )
        | {"excerpt_ids": []},
    )
    await reason(toolbox, [premise], conclusion["statement_id"])
    return await check(toolbox, conclusion["statement_id"])


async def test_a_reasoned_causal_conclusion_takes_its_design_from_what_it_rests_on() -> None:
    world = await make_world(sources=SOURCES)
    toolbox = box(world)
    # Rests on a randomised trial: nothing to show in cited text, nothing missing.
    packet = await reasoned_causal_conclusion(world, toolbox, "randomised_trial")
    assert "causal_design_not_shown" not in kinds(packet)

    world = await make_world(sources=SOURCES)
    toolbox = box(world)
    # Rests only on an association: the causal wording has no design behind it.
    packet = await reasoned_causal_conclusion(world, toolbox, None)
    blocked = [o for o in packet["obligations"] if o["kind"] == "causal_design_not_shown"]
    assert len(blocked) == 1 and "association" in blocked[0]["required_condition"]


def test_the_critic_lets_a_synthesis_state_conflict_and_caveats_without_more_support() -> None:
    from app.services.judge import STEP_AUDIT_SYSTEM

    assert "hedges and caveats only weaken a claim" in STEP_AUDIT_SYSTEM
    assert "resolve a conflict it reports as unresolved" in STEP_AUDIT_SYSTEM


async def test_reading_a_review_points_to_its_citation_network() -> None:
    world = await make_world()
    toolbox = Toolbox(session_factory, world.run_id, None, None, FakeLiterature())

    review = {"title": "Drug X: a systematic review and meta-analysis", "ids": {"doi": "10.1/r"}}
    trial = {"title": "A trial of drug X", "ids": {"pmid": "5"}}
    other = {"title": "Another trial of drug X", "ids": {"pmid": "6"}}
    assert "direction='references'" in toolbox._citation_hint(review)
    assert "DOI:10.1/r" in toolbox._citation_hint(review)
    assert "PMID:5" in toolbox._citation_hint(trial)
    assert toolbox._citation_hint(other) is None  # once for ordinary papers
    assert "review" in toolbox._citation_hint(review)  # always for reviews


async def test_an_evaluations_reference_review_is_kept_out_of_reach() -> None:
    from app.agent.toolbox import hidden_source

    world = await make_world()
    async with session_factory() as session:
        run = await session.get(AgentRun, world.run_id)
        run.budgets = {
            **run.budgets,
            "hidden_source": hidden_source({"title": "Cohort of X", "doi": "10.1000/REVIEW"}),
        }
        await session.commit()
    literature = FakeLiterature()
    toolbox = Toolbox(session_factory, world.run_id, None, None, literature)

    found = await call(
        toolbox, "search_papers", query="x", limit=5, published_after=None, exclude_retracted=False
    )
    titles = [row["title"] for row in found["results"]]
    assert "Cohort of X" not in titles and "Trial of X" in titles  # hidden by its title
    refused = await call(
        toolbox,
        "read_paper",
        amass_id=None,
        pmid=None,
        doi="10.1000/review",
        arxiv_id=None,
        semantic_scholar_id=None,
    )
    assert "withheld in this evaluation" in refused["error"]
    # Fetched by URL, it is refused before anything is stored.
    from app.services.source_store import NewSource

    page = NewSource("web_page", "agent", "Some page", "text/plain", "p.txt", b"x", [])
    with pytest.raises(Exception, match="withheld"):
        await toolbox._store_fetched(page, "https://doi.org/10.1000/review")
    async with session_factory() as session:
        stored = await session.scalars(
            select(Source).where(Source.workspace_id == world.workspace_id)
        )
        assert list(stored) == []


async def test_when_paper_indexes_refuse_the_agent_is_pointed_at_the_web() -> None:
    world = await make_world()
    toolbox = Toolbox(session_factory, world.run_id, None, None, FakeLiterature(arxiv_down=True))
    found = await call(
        toolbox, "search_papers", query="x", limit=5, published_after=None, exclude_retracted=False
    )
    assert found["unavailable"] and "web_search" in found["note"]


async def test_a_hidden_reference_read_by_another_id_is_removed_and_refused(monkeypatch) -> None:
    from app.agent.toolbox import hidden_source

    world = await make_world(sources=SOURCES)
    async with session_factory() as session:
        run = await session.get(AgentRun, world.run_id)
        run.budgets = {**run.budgets, "hidden_source": hidden_source({"doi": "10.1000/review"})}
        await session.commit()
    toolbox = box(world)
    imported = str(world.sources["trial"])

    async def via_amass(args, tool_id):  # Amass returns it, by PMID, with its DOI
        return {"source_id": imported, "title": "Some review", "ids": {"doi": "10.1000/REVIEW"}}

    monkeypatch.setattr(toolbox, "_read_paper", via_amass)
    refused = await call(
        toolbox,
        "read_paper",
        amass_id=None,
        pmid="123",
        doi=None,
        arxiv_id=None,
        semantic_scholar_id=None,
    )
    assert "withheld in this evaluation" in refused["error"]
    async with session_factory() as session:
        assert await session.get(Source, uuid.UUID(imported)) is None
