"""The agent hands over the procedure it found, as a source the experiment tools can follow."""

import uuid

import httpx
import pytest
from sqlalchemy import select

from app.agent.loop import _protocol_event, build_tools, execute_run
from app.agent.prompts import GUARDED_SYSTEM
from app.agent.toolbox import Toolbox
from app.config import get_settings
from app.database import engine, session_factory
from app.main import app
from app.models import AgentEvent, Excerpt, ExperimentProtocol, Source
from tests.agent_helpers import FakeClaude, FakeJudge, make_world, reply, tool

METHODS = {
    "paper": [
        "Thaw the competent cells on ice.",
        "Add 5 µL of plasmid DNA using a pipette.",
        "Heat shock the tube in a water bath set to 42 °C for 45 seconds.",
    ],
    "other": ["A second paper describes something else."],
    "bad": ["A retracted paper's procedure."],
    "results": ["Experiment result."],
}


@pytest.fixture(autouse=True)
async def fresh_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_dir", str(tmp_path))
    monkeypatch.setattr(get_settings(), "claude_api_key", None)
    monkeypatch.setattr("app.agent.loop.Judge", FakeJudge)
    await engine.dispose()
    yield
    await engine.dispose()


def steps_for(world, count=3, source="paper"):
    actions = [
        "Thaw the competent cells on ice.",
        "Add 5 µL of plasmid DNA using a pipette.",
        "Heat shock the tube in a water bath set to 42 °C for 45 seconds.",
    ]
    return [{"action": actions[i], "excerpt_ids": [world.excerpt(source, i)]} for i in range(count)]


async def protocol(toolbox, world, **overrides) -> dict:
    args = {
        "title": "Heat-shock transformation",
        "steps": steps_for(world),
        "basis": "Follows the first paper.",
        **overrides,
    }
    return await toolbox.call("record_protocol", args, f"toolu_{uuid.uuid4().hex[:8]}")


async def source_of(result) -> Source:
    async with session_factory() as session:
        return await session.get(Source, uuid.UUID(result["source_id"]))


# -- recording ---------------------------------------------------------------------------------


async def test_a_protocol_becomes_a_source_with_numbered_steps_and_provenance() -> None:
    world = await make_world(sources=METHODS)
    result = await protocol(Toolbox(session_factory, world.run_id, None), world)
    assert result["steps"] == 3
    source = await source_of(result)
    assert (source.origin, source.title) == ("agent", "Protocol: Heat-shock transformation")
    assert source.metadata_["run_id"] == str(world.run_id)
    assert source.metadata_["basis"] == "Follows the first paper."
    assert [s["n"] for s in source.metadata_["steps"]] == [1, 2, 3]
    assert source.metadata_["steps"][1]["excerpt_ids"] == [world.excerpt("paper", 1)]
    async with session_factory() as session:
        texts = [
            e.text
            for e in await session.scalars(
                select(Excerpt).where(Excerpt.source_id == source.id).order_by(Excerpt.sequence)
            )
        ]
    assert texts[0] == "# Methods" and texts[2].startswith("2. Add 5 µL")


async def test_recording_the_same_protocol_again_changes_nothing() -> None:
    world = await make_world(sources=METHODS)
    toolbox = Toolbox(session_factory, world.run_id, None)
    first, again = await protocol(toolbox, world), await protocol(toolbox, world)
    assert first["source_id"] == again["source_id"]


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"steps": []}, "at least one step"),
        ({"steps": [{"action": "  ", "excerpt_ids": ["x"]}]}, "Step 1 is empty"),
        ({"steps": [{"action": "Do it.", "excerpt_ids": []}]}, "must cite the excerpts"),
        ({"steps": [{"action": "Do it.", "excerpt_ids": ["nope"]}]}, "must be an id"),
    ],
)
async def test_a_protocol_must_be_made_of_cited_steps(override, message) -> None:
    world = await make_world(sources=METHODS)
    result = await protocol(Toolbox(session_factory, world.run_id, None), world, **override)
    assert message in result["error"]


async def test_steps_cannot_cite_excerpts_from_elsewhere() -> None:
    world = await make_world(sources=METHODS)
    stranger = await make_world(sources={"x": ["Not in this workspace."]})
    foreign = [{"action": "Do it.", "excerpt_ids": [stranger.excerpt("x")]}]
    result = await protocol(Toolbox(session_factory, world.run_id, None), world, steps=foreign)
    assert "No such excerpts" in result["error"]


async def test_a_protocol_cannot_rest_on_a_retracted_source() -> None:
    world = await make_world(sources=METHODS, retracted=("bad",))
    steps = [{"action": "Do the retracted thing.", "excerpt_ids": [world.excerpt("bad")]}]
    result = await protocol(Toolbox(session_factory, world.run_id, None), world, steps=steps)
    assert "retracted or invalidated" in result["error"]


async def test_experiment_results_cannot_be_the_basis_of_a_protocol() -> None:
    world = await make_world(sources=METHODS)
    async with session_factory() as session:
        source = await session.get(Source, world.sources["results"])
        source.origin = "lab-vision"
        await session.commit()
    steps = [{"action": "Repeat it.", "excerpt_ids": [world.excerpt("results")]}]
    result = await protocol(Toolbox(session_factory, world.run_id, None), world, steps=steps)
    assert "not experiment results" in result["error"]


# -- the handoff to the experiment tools --------------------------------------------------------


@pytest.fixture
async def api():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def test_the_experiment_tools_can_follow_the_protocol_without_a_model(api) -> None:
    world = await make_world(sources=METHODS)
    result = await protocol(Toolbox(session_factory, world.run_id, None), world)
    base = f"/api/workspaces/{world.workspace_id}"
    assert (await api.post(f"{base}/verify")).status_code == 200
    response = await api.post(f"{base}/protocols", json={"source_id": result["source_id"]})
    assert response.status_code == 201
    body = response.json()
    assert body["extraction_method"] == "numbered_instructions"  # verbatim, nothing invented
    steps = body["protocol"]["steps"]
    assert [s["description"] for s in steps] == [
        "Thaw the competent cells on ice.",
        "Add 5 µL of plasmid DNA using a pipette.",
        "Heat shock the tube in a water bath set to 42 °C for 45 seconds.",
    ]
    assert [(c["expected"], c["unit"]) for c in steps[1]["checks"]] == [(5.0, "µL")]
    assert [(c["expected"], c["unit"]) for c in steps[2]["checks"]] == [(42.0, "°C")]
    async with session_factory() as session:
        assert await session.scalar(select(ExperimentProtocol.id)) is not None


# -- what the run reports -----------------------------------------------------------------------


async def test_the_run_carries_the_protocol_it_recorded(api) -> None:
    world = await make_world(sources=METHODS)
    run_url = f"/api/agent-runs/{world.run_id}"
    assert (await api.get(run_url)).json()["protocol"] is None
    result = await protocol(Toolbox(session_factory, world.run_id, None), world)
    shown = (await api.get(run_url)).json()["protocol"]
    assert shown["source_id"] == result["source_id"] and shown["basis"].startswith("Follows")
    assert [s["n"] for s in shown["steps"]] == [1, 2, 3]
    listed = (await api.get(f"/api/workspaces/{world.workspace_id}/agent-runs")).json()
    assert listed[0]["protocol"]["source_id"] == result["source_id"]


async def test_a_refined_protocol_replaces_the_earlier_one(api) -> None:
    world = await make_world(sources=METHODS)
    toolbox = Toolbox(session_factory, world.run_id, None)
    await protocol(toolbox, world)
    better = await protocol(toolbox, world, steps=steps_for(world, count=2))
    shown = (await api.get(f"/api/agent-runs/{world.run_id}")).json()["protocol"]
    assert shown["source_id"] == better["source_id"] and len(shown["steps"]) == 2


# -- the loop ------------------------------------------------------------------------------------


async def test_a_recorded_protocol_appears_in_the_trace() -> None:
    world = await make_world(sources=METHODS)
    client = FakeClaude(
        reply(
            tool(
                "record_protocol",
                title="Heat-shock transformation",
                steps=steps_for(world),
                basis="Follows the first paper.",
            ),
            stop="tool_use",
        ),
        reply(stop="end_turn"),
        reply(stop="end_turn"),
        reply(stop="end_turn"),
    )
    await execute_run(world.run_id, client=client)
    async with session_factory() as session:
        events = list(
            await session.scalars(
                select(AgentEvent).where(
                    AgentEvent.run_id == world.run_id, AgentEvent.type == "protocol"
                )
            )
        )
    assert len(events) == 1
    assert [s["n"] for s in events[0].payload["steps"]] == [1, 2, 3]
    assert events[0].payload["title"] == "Heat-shock transformation"


def test_the_trace_event_numbers_steps_and_keeps_their_citations() -> None:
    event = _protocol_event(
        {"title": "T", "basis": "B", "steps": [{"action": "Mix.", "excerpt_ids": ["e1"]}]},
        {"source_id": "s"},
    )
    assert event == {
        "source_id": "s",
        "title": "T",
        "basis": "B",
        "steps": [{"n": 1, "action": "Mix.", "excerpt_ids": ["e1"]}],
    }


def test_only_guarded_runs_offer_the_tool_and_the_contract_asks_for_it() -> None:
    assert any(t["name"] == "record_protocol" for t in build_tools("guarded", 0))
    assert not any(t["name"] == "record_protocol" for t in build_tools("baseline", 0))
    assert "whether or not the question asked for one" in GUARDED_SYSTEM
    assert "Never invent" in GUARDED_SYSTEM


# -- the decision to hand a protocol over --------------------------------------------------------


async def conclude(toolbox, world, **extra) -> dict:
    claim = await toolbox.call(
        "record_claim",
        {
            "text": "The answer.",
            "excerpt_ids": [world.excerpt("other")],
            "assertion_mode": "reported",
            "role": "conclusion",
            "claim_strength": None,
            "causal_support": None,
            "scope": None,
            "criteria_satisfied": [],
        },
        f"toolu_{uuid.uuid4().hex[:8]}",
    )
    return await toolbox.call(
        "finalize_conclusion",
        {"statement_id": claim["statement_id"], "certainty": "established", **extra},
        "toolu_final",
    )


async def read_methods(toolbox, world) -> None:
    """The agent reads a paper whose text has a Methods section."""
    async with session_factory() as session:
        for excerpt in await session.scalars(
            select(Excerpt).where(Excerpt.source_id == world.sources["paper"])
        ):
            excerpt.locator = {**excerpt.locator, "section": "Materials and Methods"}
        await session.commit()
    page = await toolbox.call(
        "read_source", {"source_id": str(world.sources["paper"]), "offset": 0}, "toolu_read"
    )
    assert "excerpts" in page, page


async def test_claiming_a_protocol_was_recorded_requires_having_recorded_one() -> None:
    world = await make_world(sources=METHODS)
    toolbox = Toolbox(session_factory, world.run_id, None)
    result = await conclude(toolbox, world, protocol="recorded")
    assert result["accepted"] is False and "have not called record_protocol" in result["reason"]
    await protocol(toolbox, world)
    assert (await conclude(toolbox, world, protocol="recorded"))["accepted"] is True


async def test_no_procedure_in_the_evidence_means_no_protocol_is_asked_for() -> None:
    world = await make_world(sources=METHODS)
    assert (await conclude(Toolbox(session_factory, world.run_id, None), world))["accepted"]


async def test_saying_none_after_reading_a_procedure_gets_one_push_then_goes_through() -> None:
    world = await make_world(sources=METHODS)
    toolbox = Toolbox(session_factory, world.run_id, None)
    await read_methods(toolbox, world)
    first = await conclude(toolbox, world, protocol="none")
    assert first["accepted"] is False
    assert "You read procedures" in first["reason"] and "record_protocol" in first["reason"]
    assert (await conclude(toolbox, world, protocol="none"))["accepted"] is True  # never a loop


async def test_handing_over_the_protocol_after_the_push_is_accepted() -> None:
    world = await make_world(sources=METHODS)
    toolbox = Toolbox(session_factory, world.run_id, None)
    await read_methods(toolbox, world)
    assert (await conclude(toolbox, world, protocol="none"))["accepted"] is False
    await protocol(toolbox, world)
    assert (await conclude(toolbox, world, protocol="recorded"))["accepted"] is True


def test_the_finalize_tool_makes_the_agent_choose() -> None:
    tool_def = next(t for t in build_tools("guarded", 0) if t["name"] == "finalize_conclusion")
    schema = tool_def["input_schema"]
    assert "protocol" in schema["required"]
    assert schema["properties"]["protocol"]["enum"] == ["recorded", "none"]


# -- reading checks out of an agent's protocol ----------------------------------------------------


def checks_of(*lines: str) -> list[list[tuple[float, str]]]:
    from app.services.experiments import numbered_protocol

    text = "\n\n".join(f"{n}. {line}" for n, line in enumerate(lines, 1))
    protocol = numbered_protocol(text, "p", "Test")
    return [[(c.expected, c.unit) for c in step.checks] for step in protocol.steps]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Add 5 µl of DNA with a pipette.", [(5.0, "µL")]),
        ("Add 5 ul of DNA with a pipette.", [(5.0, "uL")]),
        ("Add 250 ml of buffer with a pipette.", [(250.0, "mL")]),
        ("Add 5 µL of DNA with a pipette.", [(5.0, "µL")]),
        ("Heat shock at 42 °c for 30 s.", [(42.0, "°C")]),
    ],
)
def test_units_are_read_however_a_paper_spells_them(line, expected) -> None:
    assert checks_of(line) == [expected]


def test_a_value_mentioned_twice_in_a_step_is_one_check() -> None:
    line = "Add 5 µL of DNA with a pipette set to 5 µL."
    assert checks_of(line) == [[(5.0, "µL")]]
    assert checks_of("Heat to 42 °C, holding the bath at 42 °C.") == [[(42.0, "°C")]]


def test_different_values_in_one_step_each_get_a_check() -> None:
    assert checks_of("Move from 37 °C to 42 °C.") == [[(37.0, "°C"), (42.0, "°C")]]


@pytest.mark.parametrize(
    "line",
    [
        "Add 1–5 µl of DNA with a pipette.",
        "Add 1-5 µl of DNA with a pipette.",
        "Spread 20–200 µl on a plate with a pipette.",
        "Keep the cells at -20 °C.",
        "Keep the cells at −20 °C.",
    ],
)
def test_a_range_or_a_negative_is_not_mistaken_for_a_setting(line) -> None:
    assert checks_of(line) == [[]]


def test_a_volume_still_needs_a_named_instrument_to_become_a_check() -> None:
    assert checks_of("Add 5 µl of DNA to the cells.") == [[]]


def test_the_protocol_tool_asks_for_the_instrument_and_for_single_values() -> None:
    from app.agent.tool_models import ProtocolStepInput

    description = ProtocolStepInput.model_fields["action"].description
    assert "instrument that sets each volume or temperature" in description
    assert "do not write ranges" in description


# -- the protocol is ready for the experiment tools when the run ends -----------------------------


def statement_from(request) -> str:
    import json

    for block in request["messages"][-1]["content"]:
        if block["type"] == "tool_result" and "statement_id" in block["content"]:
            return json.loads(block["content"])["statement_id"]
    raise AssertionError("no statement id in the tool results")


def concluding_run(world, *, with_protocol=True, steps=3):
    """A scripted agent that records a protocol and a conclusion, then finalizes."""
    conclusion = {
        "text": "The answer.",
        "excerpt_ids": [world.excerpt("other")],
        "assertion_mode": "reported",
        "role": "conclusion",
        "claim_strength": None,
        "causal_support": None,
        "scope": None,
        "criteria_satisfied": [],
    }
    calls = [tool("record_claim", **conclusion)]
    if with_protocol:
        calls.insert(
            0,
            tool(
                "record_protocol",
                title="Heat-shock transformation",
                steps=steps_for(world, count=steps),
                basis="Follows the first paper.",
            ),
        )

    def finalize(request):
        return reply(
            tool(
                "finalize_conclusion",
                statement_id=statement_from(request),
                certainty="hypothesis",
                protocol="recorded" if with_protocol else "none",
            ),
            stop="tool_use",
        )

    return FakeClaude(reply(*calls, stop="tool_use"), finalize, reply(stop="end_turn"))


async def events_named(world, kind) -> list[AgentEvent]:
    async with session_factory() as session:
        return list(
            await session.scalars(
                select(AgentEvent).where(AgentEvent.run_id == world.run_id, AgentEvent.type == kind)
            )
        )


async def test_the_handed_over_protocol_is_prepared_for_the_experiment_tools(api) -> None:
    world = await make_world(sources=METHODS)
    await execute_run(world.run_id, client=concluding_run(world))
    ready = await events_named(world, "experiment_ready")
    assert len(ready) == 1 and ready[0].payload["steps"] == 3
    async with session_factory() as session:
        prepared = list(
            await session.scalars(
                select(ExperimentProtocol).where(
                    ExperimentProtocol.workspace_id == world.workspace_id
                )
            )
        )
    assert [str(p.id) for p in prepared] == [ready[0].payload["protocol_id"]]
    assert prepared[0].extraction_method == "numbered_instructions"
    shown = (await api.get(f"/api/agent-runs/{world.run_id}")).json()["protocol"]
    assert shown["experiment_protocol_id"] == str(prepared[0].id)


async def test_an_experiment_started_with_nothing_chosen_uses_the_agents_protocol(api) -> None:
    from app.services.experiments import process_experiment_run

    world = await make_world(sources=METHODS)
    await execute_run(world.run_id, client=concluding_run(world))
    prepared = (await events_named(world, "experiment_ready"))[0].payload["protocol_id"]
    started = await api.post(
        f"/api/workspaces/{world.workspace_id}/experiment-runs", data={"mode": "demo"}
    )
    assert started.status_code == 202 and started.json()["protocol_id"] == prepared
    await process_experiment_run(uuid.UUID(started.json()["id"]))
    experiments = (await api.get(f"/api/workspaces/{world.workspace_id}/experiments")).json()
    run = next(run for run in experiments["runs"] if run["id"] == started.json()["id"])
    assert run["status"] == "succeeded", run
    assessed_steps = {observation["step_id"] for observation in run["result"]["observations"]}
    assessed_steps.update(deviation["step_id"] for deviation in run["result"]["deviations"])
    assert assessed_steps == {"s1", "s2", "s3"}  # synthetic demo intentionally skips step 2
    assert run["result"]["source_id"]
    assert experiments["verified"]
    assert next(p for p in experiments["protocols"] if p["id"] == prepared)["current"]


async def test_a_stale_agent_protocol_is_refreshed_without_manual_verification(api) -> None:
    world = await make_world(sources=METHODS)
    await execute_run(world.run_id, client=concluding_run(world))
    async with session_factory() as session:  # the research changes after the protocol was prepared
        session.add(
            Source(
                workspace_id=world.workspace_id,
                kind="document",
                origin="upload",
                title="Late paper",
                mime_type="text/plain",
                original_filename="late.txt",
                storage_key=f"test/{uuid.uuid4()}",
                content_hash=uuid.uuid4().hex,
                external_ids={},
                metadata_={},
            )
        )
        await session.commit()
    base = f"/api/workspaces/{world.workspace_id}"
    assert not (await api.get(f"{base}/experiments")).json()["verified"]
    started = await api.post(f"{base}/experiment-runs", data={"mode": "demo"})
    assert started.status_code == 202, started.text
    assert (
        started.json()["protocol_id"]
        != (await events_named(world, "experiment_ready"))[0].payload["protocol_id"]
    )


async def test_no_protocol_means_nothing_is_prepared() -> None:
    world = await make_world(sources=METHODS)
    await execute_run(world.run_id, client=concluding_run(world, with_protocol=False))
    assert await events_named(world, "experiment_ready") == []
    assert await events_named(world, "experiment_not_ready") == []


async def test_a_protocol_that_cannot_be_prepared_is_explained_and_does_not_fail_the_run(
    monkeypatch,
) -> None:
    from fastapi import HTTPException

    from app.models import AgentRun

    async def refuse(*args, **kwargs):
        raise HTTPException(409, "Wait for extraction to finish first.")

    monkeypatch.setattr("app.routes.experiments.prepare_protocol", refuse)
    world = await make_world(sources=METHODS)
    await execute_run(world.run_id, client=concluding_run(world))
    (event,) = await events_named(world, "experiment_not_ready")
    assert "extraction" in event.payload["reason"]
    async with session_factory() as session:
        assert (await session.get(AgentRun, world.run_id)).status == "succeeded"


# -- only the right protocol reaches the experiment tools -----------------------------------------


async def later_run(world) -> uuid.UUID:
    """A second run in the same workspace, as a follow-up question creates."""
    from app.models import AgentRun, ResearchGoal

    async with session_factory() as session:
        run = await session.get(AgentRun, world.run_id)
        goal = ResearchGoal(
            workspace_id=run.workspace_id,
            question="And now?",
            completion_criteria=[],
            falsifiers=[],
        )
        session.add(goal)
        await session.flush()
        follow_up = AgentRun(
            workspace_id=run.workspace_id,
            goal_id=goal.id,
            idempotency_key=str(uuid.uuid4()),
            mode="guarded",
            model=run.model,
            status="running",
            budgets=run.budgets,
            usage=run.usage,
        )
        session.add(follow_up)
        await session.commit()
        return follow_up.id


async def experiments_of(api, world) -> dict:
    return (await api.get(f"/api/workspaces/{world.workspace_id}/experiments")).json()


async def test_the_suggestion_is_the_latest_runs_protocol(api) -> None:
    world = await make_world(sources=METHODS)
    await execute_run(world.run_id, client=concluding_run(world))
    first = (await experiments_of(api, world))["suggested"]
    assert first["run_id"] == str(world.run_id) and first["current"] is True

    second_id = await later_run(world)
    await execute_run(second_id, client=concluding_run(world, steps=2))
    second = (await experiments_of(api, world))["suggested"]
    assert second["run_id"] == str(second_id) and second["source_id"] != first["source_id"]
    assert second["current"] is True and second["protocol_id"] != first["protocol_id"]


async def test_an_earlier_runs_protocol_is_not_offered_after_a_later_run_without_one(api) -> None:
    world = await make_world(sources=METHODS)
    await execute_run(world.run_id, client=concluding_run(world))
    assert (await experiments_of(api, world))["suggested"] is not None

    await execute_run(await later_run(world), client=concluding_run(world, with_protocol=False))
    assert (await experiments_of(api, world))["suggested"] is None
    started = await api.post(
        f"/api/workspaces/{world.workspace_id}/experiment-runs", data={"mode": "demo"}
    )
    assert started.status_code == 422  # nothing chosen and nothing current: no silent fallback


async def test_a_later_run_with_the_same_protocol_text_is_still_credited_with_it(api) -> None:
    world = await make_world(sources=METHODS)
    await execute_run(world.run_id, client=concluding_run(world))
    second_id = await later_run(world)
    await execute_run(second_id, client=concluding_run(world))  # identical steps
    suggested = (await experiments_of(api, world))["suggested"]
    assert suggested is not None and suggested["run_id"] == str(second_id)


async def test_the_video_model_is_shown_the_agents_protocol_and_nothing_else(
    api, tmp_path, monkeypatch
) -> None:
    import cv2
    import numpy as np

    from app.models import ExperimentRun
    from app.services import experiments
    from app.services.experiments import process_experiment_run

    seen: list[str] = []

    class RecordingLLM:
        model = "test-vision"
        usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0}

        def generate(self, *, system, content, output):
            seen.append(system)
            seen.extend(item["text"] for item in content if item["type"] == "text")
            return output.model_validate(
                {
                    "observations": [
                        {
                            "step_id": "s1",
                            "status": "performed",
                            "confidence": 0.9,
                            "values": [],
                            "notes": "Visible in the frames.",
                        }
                    ],
                    "unexpected_events": [],
                }
            )

    monkeypatch.setattr(get_settings(), "claude_api_key", "test-key")
    monkeypatch.setattr(get_settings(), "vision_strategy", "windows")
    monkeypatch.setattr(experiments, "make_llm", RecordingLLM)
    video = tmp_path / "recording.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 5, (64, 64))
    for _ in range(10):
        writer.write(np.full((64, 64, 3), 127, dtype=np.uint8))
    writer.release()

    world = await make_world(sources=METHODS)
    await execute_run(world.run_id, client=concluding_run(world))
    started = await api.post(
        f"/api/workspaces/{world.workspace_id}/experiment-runs",
        data={"mode": "video"},
        files={"file": ("recording.avi", video.read_bytes(), "video/x-msvideo")},
    )
    assert started.status_code == 202, started.text  # nothing chosen: the agent's protocol is used
    await process_experiment_run(uuid.UUID(started.json()["id"]))

    async with session_factory() as session:
        run = await session.get(ExperimentRun, uuid.UUID(started.json()["id"]))
        stored = await session.get(ExperimentProtocol, run.protocol_id)
    assert run.status == "succeeded", run.error
    shown = "\n".join(seen)
    for step in steps_for(world):
        assert step["action"] in shown  # the model was told to look for the agent's steps
    assert [s["description"] for s in stored.protocol["steps"]] == [
        s["action"] for s in steps_for(world)
    ]
    for stray in ("A second paper", "retracted paper", "Set the pipette to 50 uL"):
        assert stray not in shown  # no other protocol, paper or fixture leaks in


# -- the demo switch -----------------------------------------------------------------------------


async def test_the_demo_switch_prepares_the_lab_protocol_whatever_source_is_chosen(
    api, monkeypatch
) -> None:
    monkeypatch.setattr(get_settings(), "demo_protocol", True)
    world = await make_world(sources=METHODS)
    base = f"/api/workspaces/{world.workspace_id}"
    await api.post(f"{base}/verify")
    # "other" has no methods section at all, and would normally be refused
    response = await api.post(f"{base}/protocols", json={"source_id": str(world.sources["other"])})
    assert response.status_code == 201
    body = response.json()
    assert body["extraction_method"] == "demo_fixture"
    steps = body["protocol"]["steps"]
    assert len(steps) == 7 and steps[0]["description"].startswith("Using a pipette set to 5 μL")
    assert [(c["expected"], c["unit"]) for s in steps for c in s["checks"]] == [
        (5.0, "μL"),
        (42.0, "°C"),
        (50.0, "μL"),
    ]


async def test_without_the_switch_the_protocol_comes_from_the_source(api) -> None:
    world = await make_world(sources=METHODS)
    base = f"/api/workspaces/{world.workspace_id}"
    await api.post(f"{base}/verify")
    refused = await api.post(f"{base}/protocols", json={"source_id": str(world.sources["other"])})
    assert refused.status_code == 422  # no methodology section: nothing is invented

