import json
from copy import deepcopy
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from lab_vision.models import Protocol, ProtocolStep
from lab_vision.perception.experiment_agent import (
    Findings,
    inspect_video,
    present_findings,
    run_video_agent,
    unverifiable_reason,
    visual_method,
)

PROTOCOL = Protocol(
    id="p",
    title="Cell preparation",
    steps=[
        ProtocolStep(
            id="s1", description="Add 5 µL to the tube.", source_text="Add 5 µL to the tube."
        ),
        ProtocolStep(id="s2", description="Invert the tube.", source_text="Invert the tube."),
    ],
)


def finding(step="s1", found=True):
    return {
        "step_id": step,
        "found": found,
        "absence": "n/a" if found else "not_visible",
        "start_seconds": 0.25 if found else 0,
        "end_seconds": 1.5 if found else 0,
        "summary": "Liquid dispensed into the tube." if found else "Outside this excerpt.",
        "check_results": [{"check_index": 0, "result": "confirmed", "note": "Visible."}],
        "evidence": [{"seconds": 0.4, "description": "Tip enters the tube."}] if found else [],
        "uncertainties": [],
        "confidence": 0.9,
    }


class FakeLLM:
    model = "test-video-agent"
    usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0}

    def __init__(self, turns=None):
        self.requests = []
        self.turns = iter(
            turns
            or [
                {
                    "type": "tool_use",
                    "id": "inspect",
                    "name": "view_frames",
                    "input": {"start_seconds": 0, "end_seconds": 1.6, "count": 2},
                },
                {
                    "type": "tool_use",
                    "id": "submit",
                    "name": "submit_findings",
                    "input": {"steps": [finding(), finding("s2", False)]},
                },
            ]
        )

    def generate(self, *, system, content, output):
        assert "source_step_id" in content[0]["text"]
        return output.model_validate(
            {
                "title": "Prep",
                "summary": "Two source-grounded steps.",
                "steps": [
                    {
                        "source_step_id": s.id,
                        "title": "Add" if s.id == "s1" else "Invert",
                        "description": s.description,
                        "defining_action": s.description,
                        "visual_group": "",
                        "checks": ["Tip enters the tube", "Pipette volume reads 5 µL"]
                        if s.id == "s1"
                        else ["Tube is inverted"],
                        "detail_checks": ["Tube is held level"],
                        "caveats": ["Sterility"],
                        "criticality": "important",
                        "category": "action",
                    }
                    for s in PROTOCOL.steps
                ],
            }
        )

    def call_tools(self, *, system, messages, tools):
        self.requests.append(deepcopy(messages))
        self.usage["requests"] += 1
        return SimpleNamespace(content=[next(self.turns)], stop_reason="tool_use")


def test_real_video_is_sampled_and_requested_closeups_reach_the_agent(tmp_path):
    video = tmp_path / "recording.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10, (64, 64))
    for i in range(20):
        writer.write(np.full((64, 64, 3), 80 + i, dtype=np.uint8))
    writer.release()
    llm, progress = FakeLLM(), []
    result = run_video_agent(PROTOCOL, video, llm, "run", "source.md", progress.append)
    assert result["duration"] == 2
    assert len(result["overview"]) == 8
    assert any(item.get("type") == "image" for item in llm.requests[0][0]["content"])
    assert "image" in str(llm.requests[1][-1])
    assert result["agent_results"]["s1"]["status"] == "verified"  # unreadable details do not block
    assert result["agent_results"]["s2"]["status"] == "unverifiable"
    assert result["agent_method"]["requirements"][0]["quote"] == PROTOCOL.steps[0].source_text
    assert "Pipette volume reads 5 µL" not in result["agent_method"]["requirements"][0]["checks"]
    assert result["observations"][0]["produced_by"]["model"] == llm.model
    assert [p.get("analysis_stage") for p in progress if "analysis_stage" in p] == [
        "method",
        "frames",
        "inspection",
        "verification",
    ]
    assert progress[1]["agent_method"] == result["agent_method"]
    assert result["agent_events"][-1] == {"kind": "done", "steps": 2}


def test_streamed_methodology_previews_only_valid_source_grounded_steps():
    class StreamingLLM(FakeLLM):
        def generate_streamed(self, *, on_text, **kwargs):
            result = self.generate(**kwargs)
            data = result.model_dump()
            on_text('{"steps": [{"source_step_id": "s1"')  # incomplete
            wrong = deepcopy(data)
            wrong["steps"][0]["source_step_id"] = "invented"
            on_text(json.dumps(wrong))
            first = {**data, "steps": data["steps"][:1]}
            on_text(json.dumps(first)[:-2])  # valid first step, unfinished enclosing JSON
            on_text(json.dumps(data))
            return result

    updates = []
    method = visual_method(PROTOCOL, StreamingLLM(), "source.md", updates.append)
    assert [len(update["agent_method"]["requirements"]) for update in updates] == [1, 2]
    first = updates[0]["agent_method"]["requirements"][0]
    assert first["description"] == PROTOCOL.steps[0].description
    assert first["quote"] == PROTOCOL.steps[0].source_text
    assert "Pipette volume reads 5 µL" not in first["checks"]
    assert any("Pipette volume" in caveat for caveat in first["caveats"])
    assert updates[-1]["agent_method"] == method


class Sampler:
    duration = 10

    def __init__(self):
        self.counts = []

    def frames(self, start, end, count, width=1024):
        self.counts.append(count)
        return [{"t": 0.4, "data": "AAAA"}] * count


def test_incomplete_findings_retry_and_frame_budget_is_enforced():
    method = visual_method(PROTOCOL, FakeLLM(), "source.md")
    sampler = Sampler()
    llm = FakeLLM(
        [
            {
                "type": "tool_use",
                "id": "frames",
                "name": "view_frames",
                "input": {"start_seconds": -1, "end_seconds": 20, "count": 20},
            },
            {
                "type": "tool_use",
                "id": "missing",
                "name": "submit_findings",
                "input": {"steps": [finding()]},
            },
            {
                "type": "tool_use",
                "id": "duplicate",
                "name": "submit_findings",
                "input": {"steps": [finding(), finding()]},
            },
            {
                "type": "tool_use",
                "id": "done",
                "name": "submit_findings",
                "input": {"steps": [finding(), finding("s2", False)]},
            },
        ]
    )
    _, _, events = inspect_video(method, sampler, llm, lambda _: None, frame_budget=3)
    assert sampler.counts == [8, 3]
    assert len([e for e in events if e["kind"] == "retry"]) == 2


def test_details_do_not_block_but_conflicts_and_low_confidence_do():
    method = visual_method(PROTOCOL, FakeLLM(), "source.md")
    f = finding()
    f["check_results"].append({"check_index": 1, "result": "not_visible", "note": "Occluded."})
    result = present_findings(method, Findings(steps=[f, finding("s2", False)]), "r", "test")
    assert result["agent_results"]["s1"]["status"] == "verified"
    assert result["observations"][0]["status"] == "performed"
    f["check_results"][1]["result"] = "contradicted"
    result = present_findings(method, Findings(steps=[f, finding("s2", False)]), "r", "test")
    assert result["agent_results"]["s1"]["status"] == "contradicted"
    assert not result["deviations"][0]["needs_review"]
    f["confidence"] = 0.5
    result = present_findings(method, Findings(steps=[f, finding("s2", False)]), "r", "test")
    assert result["agent_results"]["s1"]["status"] == "unverifiable"
    assert result["deviations"][0]["needs_review"]


def test_unsupported_measurements_are_caveats_but_visible_actions_remain_checks():
    assert unverifiable_reason("The tube stays on ice for 20 minutes")
    assert unverifiable_reason("All reagents are wiped down")
    assert unverifiable_reason("The tube label is legible")
    assert unverifiable_reason("Tip enters a fresh 1.5 mL tube") is None


def test_findings_cannot_cite_unseen_frames_or_exceed_the_clip():
    method = visual_method(PROTOCOL, FakeLLM(), "source.md")
    f = finding()
    f["evidence"][0]["seconds"] = 9
    llm = FakeLLM(
        [
            {
                "type": "tool_use",
                "id": "bad",
                "name": "submit_findings",
                "input": {"steps": [f, finding("s2", False)]},
            }
        ]
    )
    with pytest.raises(RuntimeError, match="turn limit"):
        inspect_video(method, Sampler(), llm, lambda _: None, max_turns=1)


def test_concurrent_steps_need_overlapping_windows_and_are_reported_both_ways():
    method = visual_method(PROTOCOL, FakeLLM(), "source.md")
    first, second = finding(), finding("s2")
    second.update(start_seconds=1, end_seconds=3)
    first["concurrent_with"] = ["s2"]
    gap = {**second, "start_seconds": 2}
    llm = FakeLLM(
        [
            {
                "type": "tool_use",
                "id": "apart",
                "name": "submit_findings",
                "input": {"steps": [first, gap]},
            },
            {
                "type": "tool_use",
                "id": "overlap",
                "name": "submit_findings",
                "input": {"steps": [first, second]},
            },
        ]
    )
    findings, _, events = inspect_video(method, Sampler(), llm, lambda _: None, max_turns=2)
    assert "overlapping windows" in events[-2]["message"]
    result = present_findings(method, findings, "r", "test")
    assert [o["concurrentWith"] for o in result["agent_observations"]] == [["s2"], ["s1"]]
