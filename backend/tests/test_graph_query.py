import json

import pytest

from app.agent.loop import build_tools
from app.database import engine, session_factory
from app.models import (
    GraphEdge,
    ProofObligation,
    ReasoningPremise,
    ReasoningStep,
    Statement,
    StatementExcerpt,
)
from app.services import graph_qa, graph_query
from tests.agent_helpers import FakeClaude, make_world, reply, text, tool

PROVENANCE = {"actor_type": "user", "actor_id": "test"}


@pytest.fixture(autouse=True)
async def fresh_engine():
    await engine.dispose()
    yield
    await engine.dispose()


async def chain_world():
    """trial result + safety finding -> 'Drug X is effective and safe' (with an open obligation),
    and an unrelated claim about weather."""
    world = await make_world(sources={"trial": ["Drug X cut mortality by 30% in a randomised trial."]})
    async with session_factory() as session:
        def claim(text_, role=None):
            item = Statement(workspace_id=world.workspace_id, text=text_, assertion_mode="asserted", role=role,
                             salience="core", lifecycle="accepted", provenance=PROVENANCE)
            session.add(item)
            return item

        trial = claim("Drug X reduced mortality by 30% in a randomised trial.")
        safety = claim("No serious adverse events were reported for Drug X.")
        conclusion = claim("Drug X is effective and safe.", role="conclusion")
        weather = claim("It rained in Leeds on Tuesday.")
        rebuttal = claim("A later cohort found liver toxicity with Drug X.")
        await session.flush()
        session.add(StatementExcerpt(statement_id=trial.id, excerpt_id=world.excerpts["trial"][0]))
        step = ReasoningStep(workspace_id=world.workspace_id, conclusion_id=conclusion.id,
                             explanation="Efficacy from the trial plus a clean safety record.",
                             lifecycle="accepted", provenance=PROVENANCE)
        session.add(step)
        await session.flush()
        session.add_all([
            ReasoningPremise(reasoning_step_id=step.id, statement_id=trial.id, position=0),
            ReasoningPremise(reasoning_step_id=step.id, statement_id=safety.id, position=1),
            GraphEdge(workspace_id=world.workspace_id, source_node_kind="statement", source_node_id=rebuttal.id,
                      relation="rebuts", target_node_kind="statement", target_node_id=safety.id,
                      metadata_={"audit_verdict": "supported"}),
            ProofObligation(workspace_id=world.workspace_id, kind="missing_premise",
                            description="Safety rests on one trial's short follow-up.",
                            required_condition="Long-term safety data", blocks_statement_id=conclusion.id),
        ])
        await session.commit()
        ids = {name: str(item.id) for name, item in
               [("trial", trial), ("safety", safety), ("conclusion", conclusion), ("weather", weather), ("rebuttal", rebuttal)]}
        ids["step"] = str(step.id)
    return world, ids


async def test_search_ranks_matching_claims_and_falls_back_to_substrings() -> None:
    world, ids = await chain_world()
    async with session_factory() as session:
        found = await graph_query.search(session, world.workspace_id, "mortality randomised trial", 5)
        partial = await graph_query.search(session, world.workspace_id, "toxicity unicorns", 5)

    assert found["claims"][0]["statement_id"] == ids["trial"]
    assert ids["weather"] not in [c["statement_id"] for c in found["claims"]]
    assert [c["statement_id"] for c in partial["claims"]] == [ids["rebuttal"]]


async def test_a_claim_shows_its_evidence_derivation_links_and_obligations() -> None:
    world, ids = await chain_world()
    async with session_factory() as session:
        conclusion = await graph_query.get_node(session, world.workspace_id, ids["conclusion"])
        safety = await graph_query.get_node(session, world.workspace_id, ids["safety"])
        trial = await graph_query.get_node(session, world.workspace_id, ids["trial"])
        step = await graph_query.get_node(session, world.workspace_id, ids["step"])

    premises = [p["statement_id"] for p in conclusion["derived_by"][0]["premises"]]
    assert premises == [ids["trial"], ids["safety"]]
    assert conclusion["open_obligations"][0]["kind"] == "missing_premise"
    assert safety["links"][0]["relation"] == "rebuts" and safety["links"][0]["direction"] == "incoming"
    assert safety["supports_conclusions"][0]["conclusion"]["statement_id"] == ids["conclusion"]
    assert "30% in a randomised trial" in trial["evidence"][0]["text"]
    assert step["kind"] == "reasoning_step" and step["conclusion"]["statement_id"] == ids["conclusion"]


async def test_trace_chain_follows_support_and_consequences() -> None:
    world, ids = await chain_world()
    async with session_factory() as session:
        support = await graph_query.trace_chain(session, world.workspace_id, ids["conclusion"], "support", 3)
        onward = await graph_query.trace_chain(session, world.workspace_id, ids["trial"], "consequences", 3)

    assert {c["statement_id"] for c in support["claims"]} == {ids["conclusion"], ids["trial"], ids["safety"]}
    assert support["reasoning_steps"][0]["step_id"] == ids["step"]
    assert {c["statement_id"] for c in onward["claims"]} == {ids["trial"], ids["conclusion"]}


async def test_queries_never_cross_workspaces_and_bad_ids_are_readable_errors() -> None:
    world, ids = await chain_world()
    other = await make_world()
    async with session_factory() as session:
        assert "error" in await graph_query.run_tool(session, other.workspace_id, "get_graph_node", {"node_id": ids["trial"]})
        assert "not an id" in (await graph_query.run_tool(session, world.workspace_id, "get_graph_node", {"node_id": "S1"}))["error"]
        claims, steps = await graph_query.known_ids(session, other.workspace_id, [ids["trial"], ids["step"]])
    assert claims == [] and steps == []


async def test_ask_queries_the_graph_and_returns_only_real_nodes_to_highlight() -> None:
    world, ids = await chain_world()
    client = FakeClaude(
        reply(tool("search_graph", query="Drug X safe", limit=5), stop="tool_use"),
        reply(tool("trace_chain", statement_id=ids["conclusion"], direction="support", depth=3), stop="tool_use"),
        reply(tool("answer", answer="It rests on one trial and a safety record that a later cohort rebuts.",
                   relevant_statement_ids=[ids["conclusion"], ids["safety"], "not-a-node"],
                   relevant_step_ids=[ids["step"]]), stop="tool_use"),
    )

    result = await graph_qa.ask(session_factory, world.workspace_id, "Is Drug X safe?", [], client)

    assert result["answer"].startswith("It rests on one trial")
    assert set(result["statement_ids"]) == {ids["conclusion"], ids["safety"]}
    assert result["step_ids"] == [ids["step"]]
    traced = json.loads(client.tool_results(2)[0]["content"])
    assert ids["trial"] in {c["statement_id"] for c in traced["claims"]}
    assert {t["name"] for t in client.requests[0]["tools"]} == {
        "graph_overview", "search_graph", "get_graph_node", "trace_chain", "answer"
    }


async def test_ask_falls_back_to_inspected_nodes_and_forces_an_answer_on_the_last_turn() -> None:
    world, ids = await chain_world()
    spin = lambda request: reply(tool("get_graph_node", node_id=ids["trial"]), stop="tool_use")  # noqa: E731
    client = FakeClaude(*[spin] * (graph_qa.MAX_TURNS - 1),
                        reply(tool("answer", answer="The trial.", relevant_statement_ids=[], relevant_step_ids=[]),
                              stop="tool_use"))

    result = await graph_qa.ask(session_factory, world.workspace_id, "What is the evidence?", [], client)

    assert client.requests[-1]["tool_choice"] == {"type": "tool", "name": "answer"}
    assert result["statement_ids"] == [ids["trial"]]


async def test_a_plain_text_reply_is_still_an_answer() -> None:
    world, _ = await chain_world()
    result = await graph_qa.ask(session_factory, world.workspace_id, "Hi?", [], FakeClaude(reply(text("No claims match."))))
    assert result["answer"] == "No claims match." and result["statement_ids"] == []


def test_both_agent_modes_can_query_the_graph() -> None:
    for mode in ("baseline", "guarded"):
        names = {t["name"] for t in build_tools(mode, 0)}
        assert {"graph_overview", "search_graph", "get_graph_node", "trace_chain"} <= names


async def test_the_agent_toolbox_answers_graph_queries_for_its_own_workspace() -> None:
    from app.agent.toolbox import Toolbox

    world, ids = await chain_world()
    box = Toolbox(session_factory, world.run_id, None, None)
    result = await box.call("trace_chain", {"statement_id": ids["conclusion"], "direction": "support", "depth": 2}, "t1")
    assert ids["trial"] in {c["statement_id"] for c in result["claims"]}


async def test_the_endpoint_needs_claude(monkeypatch) -> None:
    from httpx import ASGITransport, AsyncClient

    from app.config import get_settings
    from app.main import app

    world, _ = await chain_world()
    monkeypatch.setattr(get_settings(), "claude_api_key", None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.post(f"/api/workspaces/{world.workspace_id}/graph-questions", json={"question": "Is it safe?"})
    assert response.status_code == 503


# -- additive updates --------------------------------------------------------------------------


class AuditingJudge:
    def __init__(self, verdict="supported"):
        self.verdict, self.calls = verdict, []

    async def audit_links(self, links, model=None):
        from app.services.judge import LinkAudit, LinkAudits

        self.calls.append(links)
        return LinkAudits(audits=[LinkAudit(source_statement_id=links[0]["source_statement_id"],
                                            target_statement_id=links[0]["target_statement_id"],
                                            relation=links[0]["relation"], verdict=self.verdict, rationale="checked")])


async def test_link_claims_appends_an_audited_edge_and_changes_nothing_else() -> None:
    from sqlalchemy import select

    from app.agent.toolbox import Toolbox

    world, ids = await chain_world()
    judge = AuditingJudge()
    box = Toolbox(session_factory, world.run_id, None, judge)
    async with session_factory() as session:
        before = {s.id: (s.text, s.lifecycle) for s in await session.scalars(select(Statement).where(Statement.workspace_id == world.workspace_id))}

    linked = await box.call("link_claims", {"source_statement_id": ids["rebuttal"], "target_statement_id": ids["conclusion"],
                                            "relation": "rebuts", "rationale": "Toxicity contradicts safety."}, "t1")
    again = await box.call("link_claims", {"source_statement_id": ids["rebuttal"], "target_statement_id": ids["conclusion"],
                                           "relation": "rebuts", "rationale": "Same."}, "t2")

    assert linked["linked"] and linked["audit_verdict"] == "supported"
    assert again == {"linked": False, "already_linked": True, "relation_id": linked["relation_id"]}
    assert "liver toxicity" in judge.calls[0][0]["source"]["text"] and len(judge.calls) == 1
    async with session_factory() as session:
        edge = await session.get(GraphEdge, __import__("uuid").UUID(linked["relation_id"]))
        after = {s.id: (s.text, s.lifecycle) for s in await session.scalars(select(Statement).where(Statement.workspace_id == world.workspace_id))}
    assert edge.metadata_["lifecycle"] == "proposed" and edge.metadata_["run_id"] == str(world.run_id)
    assert after == before


async def test_link_claims_rejects_self_links_and_other_workspaces() -> None:
    from app.agent.toolbox import Toolbox

    world, ids = await chain_world()
    other, other_ids = await chain_world()
    box = Toolbox(session_factory, world.run_id, None, None)
    same = await box.call("link_claims", {"source_statement_id": ids["trial"], "target_statement_id": ids["trial"], "relation": "supports", "rationale": "x"}, "t")
    foreign = await box.call("link_claims", {"source_statement_id": ids["trial"], "target_statement_id": other_ids["trial"], "relation": "supports", "rationale": "x"}, "t")
    unaudited = await box.call("link_claims", {"source_statement_id": ids["trial"], "target_statement_id": ids["safety"], "relation": "supports", "rationale": "x"}, "t")
    assert "itself" in same["error"] and "this workspace" in foreign["error"]
    assert unaudited["audit_verdict"] == "needs_review"


async def test_link_claims_is_a_guarded_recording_tool() -> None:
    assert "link_claims" in {t["name"] for t in build_tools("guarded", 0)}
    assert "link_claims" not in {t["name"] for t in build_tools("baseline", 0)}


async def test_new_material_is_connected_only_when_there_is_something_to_connect(monkeypatch) -> None:
    from app.config import get_settings
    from app.models import ExtractionJob
    from app.services import extraction_jobs

    calls = []

    async def fake_synthesize(session, workspace_id, request, judge=None):
        calls.append((workspace_id, request.idempotency_key))

    monkeypatch.setattr(extraction_jobs, "synthesize_workspace", fake_synthesize)
    monkeypatch.setattr(get_settings(), "claude_api_key", "test-key")
    world = await make_world(sources={"one": ["Claim A."], "two": ["Claim B."]})
    async with session_factory() as session:
        job = ExtractionJob(workspace_id=world.workspace_id, source_id=world.sources["two"], idempotency_key="k",
                            model="m", status="succeeded")
        first = Statement(workspace_id=world.workspace_id, text="A", assertion_mode="asserted", salience="core",
                          lifecycle="proposed", provenance=PROVENANCE)
        session.add_all([job, first])
        await session.flush()
        session.add(StatementExcerpt(statement_id=first.id, excerpt_id=world.excerpts["one"][0]))
        await session.commit()
        job_id = job.id

    await extraction_jobs.connect_new_material(job_id)
    assert calls == []  # only one source has claims: nothing to connect to yet

    async with session_factory() as session:
        second = Statement(workspace_id=world.workspace_id, text="B", assertion_mode="asserted", salience="core",
                           lifecycle="proposed", provenance=PROVENANCE)
        session.add(second)
        await session.flush()
        session.add(StatementExcerpt(statement_id=second.id, excerpt_id=world.excerpts["two"][0]))
        await session.commit()

    await extraction_jobs.connect_new_material(job_id)
    assert calls == [(world.workspace_id, f"auto-synthesis:{job_id}")]
