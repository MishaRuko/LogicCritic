"""The lab-vision bridge: grounded protocols, durable jobs and traceable results."""

import asyncio
import contextlib
import hashlib
import json
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from itertools import groupby
from pathlib import Path

from lab_vision.llm import ClaudeLLM
from lab_vision.models import Check, Observation, Protocol, ProtocolStep, Provenance, TimeSpan
from lab_vision.verification import ProtocolVerifier
from sqlalchemy import select, update

from app.config import get_settings
from app.database import session_factory
from app.models import (
    Excerpt,
    ExperimentProtocol,
    ExperimentRun,
    Source,
    Statement,
    StatementExcerpt,
)
from app.services.pdf_ingestion import parse_pdf
from app.services.text_ingestion import ParsedExcerpt

log = logging.getLogger(__name__)
METHOD_SECTION = re.compile(r"method|protocol|procedure|experimental|materials", re.I)
# Papers write "µl", "ul" or "ml" as often as "µL"; read them all and keep one spelling per unit.
UNIT_SPELLING = {"μl": "μL", "µl": "µL", "ul": "uL", "ml": "mL", "°c": "°C", "degc": "degC"}
# A number is a setting only on its own: not the end of a range ("1–5 µL") and not a negative
# ("-20 °C"), which would otherwise be read as 5 µL and +20 °C.
SETTING = re.compile(
    r"(?<![\d.\-–−])(\d+(?:\.\d+)?)\s*(μL|µL|uL|mL|°C|degC|%)(?=\s|[.,;)]|$)", re.IGNORECASE
)


def methodology_excerpts(excerpts: list[Excerpt] | list[ParsedExcerpt]) -> list:
    selected = [
        e
        for e in excerpts
        if METHOD_SECTION.search(
            str(e.locator.get("methodology_section", e.locator.get("section", "")))
        )
    ]
    if selected:
        return [e for e in selected if not e.text.lstrip().startswith("#")]
    # PDFs often lack section locators; restrict to the text following a methods heading.
    found = []
    in_methods = False
    for excerpt in excerpts:
        heading = excerpt.text.strip().strip("# ").lower()
        if heading in {"methods", "methodology", "materials and methods", "protocol", "procedure"}:
            in_methods = True
            continue
        if in_methods and heading in {
            "results",
            "discussion",
            "conclusion",
            "conclusions",
            "references",
        }:
            break
        if in_methods:
            found.append(excerpt)
    return found


def numbered_protocol(text: str, protocol_id: str, title: str) -> Protocol | None:
    """Keep explicit numbered instructions verbatim; never fabricate missing methodology."""
    matches = list(re.finditer(r"(?m)^\s*(\d+)[.)]\s+(.+?)(?=\n\s*\d+[.)]\s|\Z)", text, re.S))
    if not matches:
        return None
    if [int(m[1]) for m in matches] != list(range(1, len(matches) + 1)):
        expected, actual = next(
            (index, int(match[1]))
            for index, match in enumerate(matches, 1)
            if int(match[1]) != index
        )
        raise ValueError(
            f"The procedure has a numbering gap: expected step {expected}, found {actual}. "
            "Steps must be consecutive within each protocol section."
        )
    steps = []
    for index, match in enumerate(matches, 1):
        quote = match[2].strip()
        checks = []
        # Only visibly measurable numeric settings supported by the actual instruction.
        seen = set()
        for value in SETTING.finditer(quote):
            unit = UNIT_SPELLING.get(value[2].lower(), value[2])
            if (float(value[1]), unit) in seen:
                continue  # "5 µL … a pipette set to 5 µL" is one setting, not two
            seen.add((float(value[1]), unit))
            temperature = unit in {"°C", "degC"}
            # A dispensed quantity without a named readable instrument remains an instruction.
            if not temperature and unit != "%" and "pipette" not in quote.lower():
                continue
            checks.append(
                Check(
                    id=f"value-{len(checks) + 1}",
                    kind="numeric",
                    expected=float(value[1]),
                    unit=unit,
                    question="What temperature is shown on the display?"
                    if temperature
                    else "What concentration is readable on the reagent label?"
                    if unit == "%"
                    else "What volume is set on the pipette?",
                )
            )
        steps.append(
            ProtocolStep(id=f"s{index}", description=quote, source_text=quote, checks=checks)
        )
    return Protocol(id=protocol_id, title=title, steps=steps)


def extract_protocol(
    excerpts: list[Excerpt],
    protocol_id: str,
    title: str,
    *,
    methodology: list[ParsedExcerpt] | None = None,
) -> tuple[Protocol, str, dict]:
    methods = methodology_excerpts(methodology if methodology is not None else excerpts)
    if not methods:
        raise ValueError(
            "No methodology section found. "
            "Add a Methods or Protocol section with the procedure you want to test."
        )
    text = "\n\n".join(e.text for e in methods)
    if len(text) > 40_000:
        raise ValueError(
            "The methodology is too long. Upload the specific procedure as a separate source."
        )
    # PDF phases each have their own numbering. Validate those lists separately,
    # then assign consecutive internal IDs across the complete procedure.
    steps = []
    protocol = None
    for _, group in groupby(methods, key=lambda e: e.locator.get("protocol_group", "methods")):
        part = numbered_protocol("\n\n".join(e.text for e in group), protocol_id, title)
        if part is None:
            break
        for step in part.steps:
            steps.append(step.model_copy(update={"id": f"s{len(steps) + 1}"}))
    else:
        protocol = Protocol(id=protocol_id, title=title, steps=steps)
    method = "numbered_instructions"
    if protocol is None:
        settings = get_settings()
        if not settings.claude_api_key:
            raise ValueError(
                "Unnumbered methodology needs CLAUDE_API_KEY. "
                "Alternatively, upload an explicit numbered protocol."
            )
        from lab_vision.structuring import structure_protocol

        protocol = structure_protocol(text, make_llm(), protocol_id, title)
        method = "lab_vision_model"
    citations = {}
    # Cite the stored research excerpts even when a fresh PDF parse separates
    # steps that the earlier ingestion merged together or split across chunks.
    citation_text = ""
    citation_spans = []
    for excerpt in excerpts:
        normalized = " ".join(excerpt.text.split())
        start = len(citation_text)
        citation_text += normalized + " "
        citation_spans.append((start, start + len(normalized), str(excerpt.id)))
    for step in protocol.steps:
        quote = " ".join((step.source_text or "").split())
        start = citation_text.find(quote) if quote else -1
        end = start + len(quote)
        citations[step.id] = [
            excerpt_id
            for lower, upper, excerpt_id in citation_spans
            if start >= 0 and lower < end and upper > start
        ]
        if not citations[step.id]:
            raise ValueError(
                f"Step {step.id} cannot be traced to a source excerpt. "
                "Split the methodology into one paragraph per step and retry."
            )
    return protocol, method, citations


def extract_source_protocol(
    source: Source, excerpts: list[Excerpt], protocol_id: str, title: str
) -> tuple[Protocol, str, dict]:
    methodology = None
    if source.mime_type == "application/pdf":
        path = Path(get_settings().upload_dir) / source.storage_key
        if not path.is_file():
            raise ValueError(
                "The original protocol PDF is unavailable. Upload it again to continue."
            )
        methodology = parse_pdf(path.read_bytes()).excerpts
    return extract_protocol(excerpts, protocol_id, title, methodology=methodology)


def make_llm(model: str | None = None) -> ClaudeLLM:
    settings = get_settings()
    return ClaudeLLM(
        api_key=settings.claude_api_key,
        model=model or settings.vision_model,
        effort=settings.vision_effort or None,
        fallbacks=settings.vision_fallbacks,
        max_tokens=16_000,
    )


def demo_observations(protocol: Protocol, run_id: str) -> list[Observation]:
    """Clearly synthetic inputs; the real deterministic verifier produces the findings."""
    observations = []
    for index, step in enumerate(protocol.steps):
        if index == 1 and len(protocol.steps) > 2:
            continue
        observations.append(
            Observation(
                step_id=step.id,
                status="performed",
                confidence=0.95,
                values=[
                    {
                        "check_id": c.id,
                        "value": float(c.expected) + max(c.tolerance + 1, 5)
                        if index == 0 and c.kind == "numeric"
                        else c.expected,
                        "confidence": 0.95,
                    }
                    for c in step.checks
                ],
                description="Synthetic sample observation for the demo; no video was analysed.",
                span=TimeSpan(start_s=index * 5, end_s=index * 5 + 4),
                produced_by=Provenance(
                    actor_type="integration", actor_id="lab-vision-demo", run_id=run_id
                ),
            )
        )
    return observations


def verify_observations(
    protocol: Protocol,
    observations: list[Observation],
    run_id: str,
    complete_recording: bool = True,
) -> dict:
    known = {s.id: {c.id for c in s.checks} for s in protocol.steps}
    if not observations:
        raise ValueError("The replay contains no observations.")
    last_start = -1.0
    for obs in observations:
        if obs.span.start_s < last_start:
            raise ValueError("Replay observations must be in timestamp order.")
        last_start = obs.span.start_s
        if obs.step_id is not None and obs.step_id not in known:
            raise ValueError(f"Replay names unknown step {obs.step_id}.")
        if obs.step_id and any(v.check_id not in known[obs.step_id] for v in obs.values):
            raise ValueError(f"Replay names an unknown check on step {obs.step_id}.")
    verifier = ProtocolVerifier(protocol, run_id=run_id)
    deviations = []
    for obs in observations:
        deviations.extend(verifier.ingest(obs))
    if complete_recording:
        deviations.extend(verifier.finalize(observations[-1].span))
    return {
        "observations": [o.model_dump(mode="json") for o in observations],
        "deviations": [d.model_dump(mode="json") for d in deviations],
        "summary": {
            "observations": len(observations),
            "deviations": len(deviations),
            "needs_review": sum(d.needs_review for d in deviations),
            "failed_windows": 0,
        },
    }


def live_directory(run: ExperimentRun) -> Path:
    """Where a live session's frames, recording and end marker are kept."""
    return Path(get_settings().upload_dir) / str(run.workspace_id) / str(run.id)


class LiveResults:
    """Publishes what a live session finds as soon as it is found, for the dashboard."""

    def __init__(self, progress) -> None:
        self.progress = progress
        self.observations: list[dict] = []
        self.deviations: list[dict] = []

    def _publish(self) -> None:
        self.progress({"live": {"observations": self.observations, "deviations": self.deviations}})

    def on_observation(self, observation) -> None:
        self.observations.append(observation.model_dump(mode="json"))
        self._publish()

    def on_deviation(self, deviation) -> None:
        self.deviations.append(deviation.model_dump(mode="json"))
        self._publish()

    def on_run_complete(self, summary) -> None:
        pass

    def on_window(self, window, steps) -> None:
        self.progress({"processed_seconds": window.span.end_s})


def execute_live(protocol: Protocol, run: ExperimentRun, progress) -> dict:
    """Analyse a live stream window by window while the phone is still sending frames."""
    from lab_vision.perception.claude import ClaudePerceiver
    from lab_vision.pipeline import Pipeline
    from lab_vision.video import LiveFrameSource

    settings = get_settings()
    directory = live_directory(run)
    frames = directory / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    source = LiveFrameSource(
        frames,
        ended=(directory / "ended").exists,
        max_seconds=settings.live_max_seconds,
        idle_seconds=settings.live_idle_seconds,
    )
    live = LiveResults(progress)
    llm = make_llm(settings.vision_live_model)
    result = Pipeline(
        protocol,
        source,
        ClaudePerceiver(llm),
        sinks=[live],
        window_observers=[live],
        window_seconds=settings.live_window_seconds,
        lookahead_steps=10,
        source_name=run.filename,
        run_id_factory=lambda: str(run.id),
        complete_recording=(run.result or {}).get("coverage") != "excerpt",
    ).run()
    if not result.summary.windows:
        raise ValueError("No frames arrived from the camera. Open the camera link and try again.")
    if not result.observations:
        raise ValueError("No protocol steps were seen in the live stream.")
    return {
        "summary": result.summary.model_dump(mode="json"),
        "usage": llm.usage,
        "observations": [o.model_dump(mode="json") for o in result.observations],
        "deviations": [d.model_dump(mode="json") for d in result.deviations],
        "duration": source.last_timestamp_s,
    }


def execute_protocol(protocol: Protocol, run: ExperimentRun, progress) -> dict:
    complete_recording = (run.result or {}).get("coverage") != "excerpt"
    if run.mode == "live":
        return execute_live(protocol, run, progress)
    if run.mode == "demo":
        return verify_observations(protocol, demo_observations(protocol, str(run.id)), str(run.id))
    path = Path(get_settings().upload_dir) / run.storage_key
    if run.mode == "replay":
        observations = [
            Observation.model_validate_json(line)
            for line in path.read_text().splitlines()
            if line.strip()
        ]
        return verify_observations(protocol, observations, str(run.id), complete_recording)
    if get_settings().vision_strategy == "agent":
        from lab_vision.perception.experiment_agent import run_video_agent

        return run_video_agent(protocol, path, make_llm(), str(run.id), protocol.title, progress)
    from lab_vision.perception.claude import ClaudePerceiver
    from lab_vision.pipeline import Pipeline
    from lab_vision.sinks import JsonlSink
    from lab_vision.video import VideoFileSource

    class Progress:
        def on_window(self, window, steps):
            progress(window.span.end_s)

    llm = make_llm()
    result = Pipeline(
        protocol,
        VideoFileSource(path),
        ClaudePerceiver(llm),
        sinks=[JsonlSink(path.parent / "analysis")],
        window_observers=[Progress()],
        source_name=run.filename,
        lookahead_steps=10,
        run_id_factory=lambda: str(run.id),
        complete_recording=complete_recording,
    ).run()
    if not result.observations:
        raise ValueError(
            "No usable observations were obtained from this video. "
            "Check the recording and model configuration."
        )
    return {
        "summary": result.summary.model_dump(mode="json"),
        "usage": llm.usage,
        "observations": [o.model_dump(mode="json") for o in result.observations],
        "deviations": [d.model_dump(mode="json") for d in result.deviations],
    }


async def claim_next_experiment_run(
    modes: tuple[str, ...] = ("demo", "replay", "video", "live"),
) -> uuid.UUID | None:
    async with session_factory() as session, session.begin():
        # Interrupted jobs become explicit failures instead of hanging indefinitely.
        await session.execute(
            update(ExperimentRun)
            .where(
                ExperimentRun.status == "running",
                ExperimentRun.heartbeat_at < datetime.now(UTC) - timedelta(minutes=10),
            )
            .values(
                status="failed",
                error="The experiment worker was interrupted. Start a new run.",
                completed_at=datetime.now(UTC),
            )
        )
        run = await session.scalar(
            select(ExperimentRun)
            .where(ExperimentRun.status == "queued", ExperimentRun.mode.in_(modes))
            .order_by(ExperimentRun.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if run is None:
            return None
        run.status = "running"
        run.heartbeat_at = datetime.now(UTC)
        return run.id


async def process_experiment_run(run_id: uuid.UUID) -> None:
    async def heartbeat():
        while True:
            async with session_factory() as session:
                await session.execute(
                    update(ExperimentRun)
                    .where(ExperimentRun.id == run_id, ExperimentRun.status == "running")
                    .values(heartbeat_at=datetime.now(UTC))
                )
                await session.commit()
            await asyncio.sleep(5)

    async def record_progress(value: float | dict):
        patch = value if isinstance(value, dict) else {"processed_seconds": value}
        async with session_factory() as session:
            await session.execute(
                update(ExperimentRun)
                .where(ExperimentRun.id == run_id, ExperimentRun.status == "running")
                .values(result=ExperimentRun.result.op("||")(patch))
            )
            await session.commit()

    pulse = asyncio.create_task(heartbeat())
    try:
        async with session_factory() as session:
            run = await session.get(ExperimentRun, run_id)
            if run is None:
                return
            draft = await session.get(ExperimentProtocol, run.protocol_id)
            # Only the procedure this recording follows, when the source describes several.
            protocol = Protocol.model_validate(draft.protocol).only(
                (run.result or {}).get("variant")
            )
            loop = asyncio.get_running_loop()

            def progress(timestamp):
                asyncio.run_coroutine_threadsafe(record_progress(timestamp), loop).result()

            result = await asyncio.to_thread(execute_protocol, protocol, run, progress)
            result["coverage"] = (run.result or {}).get("coverage", "complete_recording")
            result_bytes = json.dumps(result).encode()
            prefix = {"demo": "Synthetic demo", "live": "Live session"}.get(run.mode, "Experiment")
            # Persist a source with temporal excerpts and reported claims in the same graph.
            source = Source(
                id=uuid.uuid4(),
                workspace_id=run.workspace_id,
                kind="tool_output",
                origin="lab-vision",
                title=f"{prefix}: {protocol.title}",
                mime_type="application/json",
                original_filename=f"experiment-{run.id}.json",
                storage_key=f"{run.workspace_id}/{run.id}/result.json",
                content_hash=hashlib.sha256(result_bytes).hexdigest(),
                metadata_={
                    "experiment_run_id": str(run.id),
                    "mode": run.mode,
                    "protocol_id": str(run.protocol_id),
                },
            )
            session.add(source)
            await session.flush()
            result_path = Path(get_settings().upload_dir) / source.storage_key
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_bytes(result_bytes)
            provenance = {
                "actor_type": "integration",
                "actor_id": "lab-vision",
                "run_id": str(run.id),
            }
            evidence = [(obs, "premise") for obs in result["observations"]]
            evidence.extend((deviation, "objection") for deviation in result["deviations"])
            for sequence, (item, role) in enumerate(evidence):
                if role == "objection":
                    text = item["message"]
                    if item["needs_review"]:
                        text = f"Needs review: {text}"
                else:
                    text = (
                        f"{item['step_id'] or 'Unexpected event'}: {item['status']}. "
                        f"{item.get('description') or ''}"
                    )
                if run.mode == "demo":
                    text = f"Synthetic demo: {text}"
                excerpt = Excerpt(
                    id=uuid.uuid4(),
                    source_id=source.id,
                    sequence=sequence,
                    text=text,
                    locator={
                        "kind": "time_range",
                        **(item["span"] or {}),
                        "frame_indices": item.get("frame_indices", []),
                        "step_id": item.get("step_id"),
                        "methodology_excerpt_ids": draft.step_excerpts.get(item.get("step_id"), []),
                    },
                )
                statement = Statement(
                    id=uuid.uuid4(),
                    workspace_id=run.workspace_id,
                    text=text,
                    assertion_mode="reported",
                    role=role,
                    salience="supporting",
                    lifecycle="proposed",
                    provenance=provenance,
                )
                session.add_all([excerpt, statement])
                await session.flush()
                session.add(StatementExcerpt(statement_id=statement.id, excerpt_id=excerpt.id))
            result["source_id"] = str(source.id)
            run.result = result
            run.status = "succeeded"
            run.completed_at = datetime.now(UTC)
            await session.commit()
    except Exception as error:
        log.exception("Experiment %s failed", run_id)
        async with session_factory() as session:
            await session.execute(
                update(ExperimentRun)
                .where(ExperimentRun.id == run_id)
                .values(status="failed", error=str(error)[:2000], completed_at=datetime.now(UTC))
            )
            await session.commit()
    finally:
        pulse.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pulse
