"""Report integrity and grounding in the experiment that actually ran."""

import hashlib
import json

from app.models import ExperimentProtocol, ExperimentRun


def canonical_hash(value) -> str:
    def normalize(item):
        if isinstance(item, float) and item.is_integer():
            return int(item)
        if isinstance(item, dict):
            return {k: normalize(v) for k, v in item.items()}
        if isinstance(item, list):
            return [normalize(v) for v in item]
        return item

    content = json.dumps(
        normalize(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(content.encode()).hexdigest()


def validate_record(record: dict, run: ExperimentRun, protocol: ExperimentProtocol) -> None:
    body = {k: v for k, v in record.items() if k != "recordHash"}
    if record.get("schemaVersion") != 2 or record.get("experimentId") != str(run.id):
        raise ValueError("The report does not identify this experiment run.")
    if canonical_hash(body) != record.get("recordHash"):
        raise ValueError("The report hash does not match its content.")
    method = record.get("method", {})
    media = record.get("run", {})
    if canonical_hash(method) != record.get("methodHash") or canonical_hash(
        media.get("observations", [])
    ) != record.get("observationHash"):
        raise ValueError("The method or observation hash does not match.")
    if media.get("id") != str(run.id):
        raise ValueError("The recording does not match this experiment.")
    if "agent_method" in run.result:
        if method != run.result["agent_method"]:
            raise ValueError(
                "Report methodology must match the stored source-grounded visual checks."
            )
        if media.get("observations") != run.result["agent_observations"]:
            raise ValueError("Report evidence and checks must match the stored video inspection.")
        if record.get("results") != run.result["agent_results"]:
            raise ValueError("Report verdicts must match the persisted video findings.")
        if record.get("rawObservations") != run.result["observations"]:
            raise ValueError("Raw report observations must match the stored analysis.")
        return
    steps = protocol.protocol["steps"]
    requirements = method.get("requirements", [])
    if len(requirements) != len(steps):
        raise ValueError("The report must include every methodology step.")
    for step, requirement in zip(steps, requirements, strict=True):
        if (
            requirement.get("id") != step["id"]
            or requirement.get("description") != step["description"]
            or requirement.get("quote") != step.get("source_text")
        ):
            raise ValueError("Report steps must match the extracted source passages.")
    actual = {o["id"]: o for o in run.result.get("observations", []) if o.get("step_id")}
    reported = media.get("observations", [])
    if len(reported) > len(actual) or not {o.get("id") for o in reported}.issubset(actual):
        raise ValueError("Report observations must match the stored analysis.")
    if "rawObservations" in record and record["rawObservations"] != run.result.get(
        "observations", []
    ):
        raise ValueError("Raw report observations must match the stored analysis.")
    for observation in reported:
        original = actual[observation["id"]]
        if (
            observation.get("stepId"),
            observation.get("timestampStart"),
            observation.get("timestampEnd"),
            observation.get("confidence"),
        ) != (
            original["step_id"],
            original["span"]["start_s"],
            original["span"]["end_s"],
            original["confidence"],
        ):
            raise ValueError("Report timestamps and confidence must match the stored observations.")
    results = record.get("results", {})
    if set(results) != {s["id"] for s in steps}:
        raise ValueError("Each methodology step needs a report result.")
    for step in steps:
        status = results[step["id"]].get("status")
        findings = [d for d in run.result.get("deviations", []) if d.get("step_id") == step["id"]]
        completed = any(
            o["step_id"] == step["id"] and o["status"] == "performed" and o["confidence"] >= 0.6
            for o in actual.values()
        )
        if status not in {"verified", "contradicted", "unverifiable"}:
            raise ValueError("Unknown report verification status.")
        if status == "verified" and (not completed or findings):
            raise ValueError("An uncertain or contradicted step cannot be reported as verified.")
        if status == "contradicted" and not any(not d["needs_review"] for d in findings):
            raise ValueError("A contradiction needs a persisted experiment finding.")
