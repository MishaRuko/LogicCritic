"""Worker adaptation of the experiment branch's methodology and video inspection agent."""

import base64
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Literal

import cv2
from pydantic import BaseModel, Field, ValidationError
from pydantic_core import from_json

from lab_vision.models import Deviation, Observation, Protocol, Provenance, TimeSpan
from lab_vision.perception.experiment_contract import AGENT_SYSTEM, AGENT_TOOLS, EXTRACTION_SYSTEM
from lab_vision.video import VideoError, VideoFileSource

MIN_CONFIDENCE = 0.7
# Same exclusions as experiment/agent.ts: numerical and label claims become caveats.
UNVERIFIABLE = [
    (
        "Measured quantity",
        r"\b(volume|amount|concentration|quantity|graduation|liquid level)s?\b|"
        r"\b(about|approximately|roughly|~)\s*\d+(\.\d+)?\s*"
        r"(µl|μl|ul|ml|l|mg|µg|μg|ng|g|mm|µm|μm|nm|m)\b|"
        r"\d+(\.\d+)?\s*(µl|μl|ul|ml|mg|µg|μg|ng|g)\s+of\b",
    ),
    ("Label or contents", r"\blabel(l?ed|s)?\b|\blegible\b"),
    (
        "Exact count",
        r"\b\d+(\s*(-|–|to)\s*\d+)?\s+(\w+\s+){0,2}"
        r"(times|flicks|strokes|inversions|taps)\b|\bnumber of\b|\bcount(ed)?\b",
    ),
    (
        "Claim about every item",
        r"\b(each|every|all)\s+(of\s+the\s+)?(bottles?|items?|materials?|reagents?|tubes?|containers?|surfaces?|wells?|dishes?|plates?)\b",
    ),
    (
        "Exact duration or temperature",
        r"\b\d+(\.\d+)?\s*(-|–|to)?\s*\d*\s*(s|secs?|seconds?|mins?|minutes?|h|hrs?|hours?)\b|\d+(\.\d+)?\s*°\s*[cf]\b|\bexactly\b",
    ),
]


def unverifiable_reason(check: str) -> str | None:
    return next(
        (reason for reason, pattern in UNVERIFIABLE if re.search(pattern, check, re.I)), None
    )


class VisualStep(BaseModel):
    source_step_id: str
    title: str
    description: str
    defining_action: str
    visual_group: str
    checks: list[str]
    detail_checks: list[str]
    caveats: list[str]
    criticality: Literal["informational", "important", "critical"]
    category: Literal["identity", "timing", "action", "ordering"]


class VisualMethod(BaseModel):
    title: str
    summary: str
    steps: list[VisualStep]


def visual_requirement(step, visual: VisualStep, index: int, group: str | None = None) -> dict:
    core = [c for c in visual.checks if not unverifiable_reason(c)]
    details = [c for c in visual.detail_checks if not unverifiable_reason(c)]
    moved = [
        f"{reason}, not verifiable from video: {c}"
        for c in visual.checks + visual.detail_checks
        if (reason := unverifiable_reason(c))
    ]
    return {
        "id": step.id,
        "order": index + 1,
        "title": visual.title,
        "description": step.description,
        "quote": step.source_text,
        "definingAction": visual.defining_action,
        **({"visualGroup": group} if group else {}),
        "checks": (core or [f"{visual.title} is visibly performed"]) + details,
        "details": details,
        "caveats": visual.caveats + moved,
        "criticality": visual.criticality,
        "category": visual.category,
    }


def visual_method(protocol: Protocol, llm, source: str, progress=None) -> dict:
    published = 0

    def preview(text: str):
        nonlocal published
        try:
            partial = from_json(text, allow_partial=True)
        except ValueError:
            return
        if not isinstance(partial, dict):
            return
        requirements = []
        for index, item in enumerate(partial.get("steps", [])):
            try:
                visual = VisualStep.model_validate(item)
            except ValidationError:
                break
            if index >= len(protocol.steps) or visual.source_step_id != protocol.steps[index].id:
                break
            requirements.append(visual_requirement(protocol.steps[index], visual, index))
        if len(requirements) <= published:
            return
        published = len(requirements)
        progress({"agent_method": {
            "schemaVersion": 1, "title": protocol.title, "version": protocol.version,
            "source": source, "summary": partial.get("summary", ""),
            "requirements": requirements,
        }})

    generate = llm.generate
    streaming = progress is not None and callable(getattr(llm, "generate_streamed", None))
    if streaming:
        generate = llm.generate_streamed
    extracted = generate(
        system=EXTRACTION_SYSTEM,
        content=[
            {
                "type": "text",
                "text": (
                    "Turn these extracted source steps into visible checks. Keep one "
                    "entry per step, in this order, with its source_step_id. Do not add, merge "
                    "or reorder steps. The original description will remain the source of truth.\n"
                    + json.dumps(
                        [
                            {"source_step_id": s.id, "description": s.description}
                            for s in protocol.steps
                        ]
                    )
                ),
            }
        ],
        output=VisualMethod,
        **({"on_text": preview} if streaming else {}),
    )
    if [s.source_step_id for s in extracted.steps] != [s.id for s in protocol.steps]:
        raise ValueError("Visual checks did not retain the source methodology steps in order.")
    labels = [s.visual_group.strip() for s in extracted.steps]
    groups, seen = [], Counter()
    for i, label in enumerate(labels):
        adjacent = label and (
            i > 0 and labels[i - 1] == label or i + 1 < len(labels) and labels[i + 1] == label
        )
        if not adjacent:
            groups.append(None)
            continue
        if i == 0 or labels[i - 1] != label:
            seen[label] += 1
        groups.append(label if seen[label] == 1 else f"{label} ({seen[label]})")
    requirements = []
    for i, (step, visual) in enumerate(zip(protocol.steps, extracted.steps, strict=True)):
        requirements.append(visual_requirement(step, visual, i, groups[i]))
    return {
        "schemaVersion": 1,
        "title": protocol.title,
        "version": protocol.version,
        "source": source,
        "summary": extracted.summary,
        "requirements": requirements,
    }


class CheckFinding(BaseModel):
    check_index: int = Field(ge=0)
    result: Literal["confirmed", "contradicted", "not_visible"]
    note: str


class EvidenceFinding(BaseModel):
    seconds: float = Field(ge=0, allow_inf_nan=False)
    description: str


class StepFinding(BaseModel):
    step_id: str
    found: bool
    absence: Literal["n/a", "not_performed", "not_visible"]
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float = Field(ge=0, allow_inf_nan=False)
    summary: str
    check_results: list[CheckFinding]
    evidence: list[EvidenceFinding]
    uncertainties: list[str]
    confidence: float = Field(ge=0, le=1)
    # Steps performed at the same time as this one (background waits, two hands, interleaving).
    concurrent_with: list[str] = []


class Findings(BaseModel):
    steps: list[StepFinding]


class VideoSampler:
    """Seek only requested windows; the decoder stays in the worker, not the browser."""

    def __init__(self, path: Path):
        self.capture = cv2.VideoCapture(str(path))
        fps = self.capture.get(cv2.CAP_PROP_FPS)
        frames = self.capture.get(cv2.CAP_PROP_FRAME_COUNT)
        self.duration = frames / fps if fps > 0 else 0
        if not self.capture.isOpened() or not math.isfinite(self.duration) or self.duration <= 0:
            self.close()
            raise VideoError("This recording cannot be decoded. Try an H.264 MP4.")
        self.encoder = VideoFileSource(path, jpeg_quality=75)

    def close(self):
        self.capture.release()

    def frames(self, start: float, end: float, count: int, width: int = 1024) -> list[dict]:
        result = []
        self.encoder.max_side = width
        for i in range(count):
            t = min(max(0, start + (end - start) * (i + 0.5) / count), max(0, self.duration - 0.05))
            self.capture.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, image = self.capture.read()
            if not ok:
                raise VideoError(f"Could not decode the recording at {t:.1f} seconds.")
            result.append({"t": t, "data": base64.b64encode(self.encoder._encode(image)).decode()})
        return result


def frame_blocks(frames: list[dict]) -> list[dict]:
    return [
        block
        for frame in frames
        for block in [
            {"type": "text", "text": f"Frame at {frame['t']:.1f} s"},
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": frame["data"]},
            },
        ]
    ]


def validate_findings(findings: Findings, method: dict, duration: float, shown: list[float]):
    known = {r["id"]: r for r in method["requirements"]}
    if Counter(s.step_id for s in findings.steps) != Counter(known.keys()):
        raise ValueError("Include every methodology step exactly once; use only its source id.")
    by_id = {s.step_id: s for s in findings.steps}
    for step in findings.steps:
        if step.found and (
            step.absence != "n/a" or not 0 <= step.start_seconds < step.end_seconds <= duration
        ):
            raise ValueError(f"{step.step_id}: use an ordered window inside this recording.")
        if not step.found and (step.absence == "n/a" or step.start_seconds or step.end_seconds):
            raise ValueError(
                f"{step.step_id}: an unlocated step needs an absence reason and zero times."
            )
        indices = [c.check_index for c in step.check_results]
        if len(indices) != len(set(indices)) or any(
            i >= len(known[step.step_id]["checks"]) for i in indices
        ):
            raise ValueError(f"{step.step_id}: use each valid check index at most once.")
        for other_id in step.concurrent_with:
            other = by_id.get(other_id)
            if other is None or other_id == step.step_id:
                raise ValueError(f"{step.step_id}: concurrent_with names unknown step {other_id}.")
            if not (
                step.found
                and other.found
                and step.start_seconds < other.end_seconds
                and other.start_seconds < step.end_seconds
            ):
                raise ValueError(
                    f"{step.step_id}: concurrent_with {other_id} needs both steps found "
                    "with overlapping windows."
                )
        if any(
            e.seconds > duration or not any(abs(e.seconds - t) <= 0.6 for t in shown)
            for e in step.evidence
        ):
            raise ValueError(f"{step.step_id}: cite timestamps of frames you were actually shown.")


def inspect_video(
    method: dict, sampler, llm, progress, max_turns: int = 20, frame_budget: int | None = None
) -> tuple[Findings, list[dict], list[dict]]:
    duration = sampler.duration
    budget = frame_budget if frame_budget is not None else max(48, 4 * len(method["requirements"]))
    overview = sampler.frames(0, duration, max(8, min(40, math.floor(duration / 3 + 0.5))), 640)
    shown = [f["t"] for f in overview]
    events = [{"kind": "overview", "frames": len(overview), "duration": duration}]
    progress(
        {
            "analysis_stage": "inspection",
            "agent_events": events,
            "overview": overview,
            "duration": duration,
        }
    )
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Methodology: {method['title']}\n{json.dumps(method['requirements'])}\n"
                        f"The recording is {duration:.1f} seconds long. Overview frames follow. "
                        f"You may request up to {budget} more frames. "
                        "Assess every step, then submit_findings."
                    ),
                },
                *frame_blocks(overview),
            ],
        }
    ]
    for _ in range(max_turns):
        response = llm.call_tools(system=AGENT_SYSTEM, messages=messages, tools=AGENT_TOOLS)
        if response.stop_reason in {"refusal", "max_tokens"}:
            raise RuntimeError(f"Video inspection stopped: {response.stop_reason}.")
        content = [
            b.model_dump(mode="json", exclude_none=True) if hasattr(b, "model_dump") else b
            for b in response.content
        ]
        messages.append({"role": "assistant", "content": content})
        uses = [b for b in content if b["type"] == "tool_use"]
        if not uses:
            messages.append({"role": "user", "content": "Call submit_findings for every step."})
            continue
        results, final = [], None
        for use in uses:
            result = {"type": "tool_result", "tool_use_id": use["id"]}
            try:
                if use["name"] == "view_frames":
                    args = use["input"]
                    start = max(0, min(duration, float(args["start_seconds"])))
                    end = max(start, min(duration, float(args["end_seconds"])))
                    count = max(0, min(8, int(args["count"]), budget))
                    if not all(
                        math.isfinite(float(args[k]))
                        for k in ("start_seconds", "end_seconds", "count")
                    ):
                        raise ValueError("Frame window values must be finite.")
                    if not count:
                        raise ValueError("Frame budget exhausted. Submit your findings now.")
                    budget -= count
                    frames = sampler.frames(start, end, count)
                    shown.extend(f["t"] for f in frames)
                    events.append({"kind": "inspect", "start": start, "end": end, "count": count})
                    progress({"agent_events": events[-80:], "processed_seconds": end})
                    result["content"] = frame_blocks(frames) + [
                        {"type": "text", "text": f"{budget} frames left."}
                    ]
                elif use["name"] == "submit_findings":
                    candidate = Findings.model_validate(use["input"])
                    validate_findings(candidate, method, duration, shown)
                    final = candidate
                    result["content"] = "Findings received."
                else:
                    raise ValueError(f"Unknown tool {use['name']}.")
            except (ValueError, KeyError, TypeError, ValidationError) as error:
                result.update(is_error=True, content=str(error))
                events.append({"kind": "retry", "message": str(error)[:300]})
                progress({"agent_events": events[-80:]})
            results.append(result)
        if final is not None:
            events.append({"kind": "done", "steps": len(final.steps)})
            return final, overview, events[-80:]
        messages.append({"role": "user", "content": results})
    raise RuntimeError("The video agent did not submit valid findings within its turn limit.")


def present_findings(method: dict, findings: Findings, run_id: str, model: str) -> dict:
    """Deterministic per-check verdicts, plus the existing lab-vision graph schema."""
    observations, native, deviations, results, absences = [], [], [], {}, []
    by_id = {s.step_id: s for s in findings.steps}
    # Concurrency is symmetric even when the agent only reports it on one side.
    concurrent = {s.step_id: set(s.concurrent_with) for s in findings.steps}
    for step in findings.steps:
        for other in step.concurrent_with:
            concurrent[other].add(step.step_id)
    order = [r["id"] for r in method["requirements"]]
    for requirement in method["requirements"]:
        step = by_id[requirement["id"]]
        evidence = (
            [
                {
                    "id": f"{step.step_id}-e{i}",
                    "timestamp": e.seconds,
                    "end": e.seconds,
                    "description": e.description,
                    "kind": "video",
                }
                for i, e in enumerate(step.evidence)
            ]
            if step.found
            else []
        )
        checks = []
        for i, check in enumerate(requirement["checks"]):
            finding = next((c for c in step.check_results if c.check_index == i), None)
            checks.append(
                {
                    "check": check,
                    "result": finding.result if finding else "not_visible",
                    "note": finding.note if finding else "Not assessed.",
                    "required": check not in requirement.get("details", []),
                }
            )
        unknown = [c["check"] for c in checks if c["required"] and c["result"] != "confirmed"]
        conflict = next((c for c in checks if c["result"] == "contradicted"), None)
        if step.found and step.confidence >= MIN_CONFIDENCE and evidence and conflict:
            verdict = {
                "status": "contradicted",
                "confidence": step.confidence,
                "expected": conflict["check"],
                "observed": conflict["note"],
                "evidence": evidence,
            }
        elif step.found and step.confidence >= MIN_CONFIDENCE and evidence and not unknown:
            verdict = {"status": "verified", "confidence": step.confidence, "evidence": evidence}
        else:
            reason = (
                step.summary
                if not step.found
                else "Observation confidence is below the verification threshold."
                if step.confidence < MIN_CONFIDENCE
                else "Part of this step is visible, but not every condition "
                "can be established from the recording."
            )
            verdict = {
                "status": "unverifiable",
                "reason": reason,
                "missingEvidence": unknown or [reason],
                "evidence": evidence,
            }
        results[step.step_id] = verdict
        if not step.found:
            absences.append(
                {
                    "stepId": step.step_id,
                    "reason": step.absence,
                    "confidence": step.confidence,
                    "note": step.summary,
                }
            )
            continue
        observation = {
            "id": f"{run_id}-{step.step_id}",
            "stepId": step.step_id,
            "timestampStart": step.start_seconds,
            "timestampEnd": step.end_seconds,
            "observed": {},
            "establishes": [],
            "uncertain": step.uncertainties,
            "confidence": step.confidence,
            "provenance": "claude_agent",
            "summary": step.summary,
            "checkResults": checks,
            "evidence": evidence,
            "concurrentWith": [i for i in order if i in concurrent[step.step_id]],
        }
        observations.append(observation)
        raw = Observation(
            id=observation["id"],
            step_id=step.step_id,
            status="performed" if verdict["status"] == "verified" else "unclear",
            confidence=step.confidence,
            description=step.summary,
            span=TimeSpan(start_s=step.start_seconds, end_s=step.end_seconds),
            produced_by=Provenance(
                actor_type="integration", actor_id="experiment-agent", run_id=run_id, model=model
            ),
        )
        native.append(raw.model_dump(mode="json"))
        if verdict["status"] != "verified":
            deviations.append(
                Deviation(
                    kind="wrong_value"
                    if verdict["status"] == "contradicted"
                    else "unverified_check",
                    rule="visual_check",
                    step_id=step.step_id,
                    message=conflict["note"] if conflict else verdict["reason"],
                    needs_review=verdict["status"] == "unverifiable",
                    observation_ids=[raw.id],
                    span=raw.span,
                    run_id=run_id,
                ).model_dump(mode="json")
            )
    return {
        "observations": native,
        "deviations": deviations,
        "agent_method": method,
        "agent_observations": observations,
        "agent_results": results,
        "absences": absences,
        "summary": {
            "observations": len(native),
            "deviations": len(deviations),
            "needs_review": sum(d["needs_review"] for d in deviations),
            "failed_windows": 0,
        },
    }


def run_video_agent(
    protocol: Protocol, path: Path, llm, run_id: str, filename: str, progress
) -> dict:
    progress({"analysis_stage": "method"})
    method = visual_method(protocol, llm, filename, progress)
    progress({"analysis_stage": "frames", "agent_method": method})
    sampler = VideoSampler(path)
    try:
        findings, overview, events = inspect_video(method, sampler, llm, progress)
        progress({"analysis_stage": "verification", "agent_events": events})
        result = present_findings(method, findings, run_id, llm.model)
        result.update(
            duration=sampler.duration,
            overview=overview,
            agent_events=events,
            analysis_stage="done",
            usage=llm.usage,
        )
        return result
    finally:
        sampler.close()
