import argparse
import json
import logging
import sys
from pathlib import Path

import cv2

from lab_vision.config import get_settings
from lab_vision.debug import FrameDumper
from lab_vision.detection.owlv2 import DetectorUnavailable, Owlv2Detector
from lab_vision.detection.processor import DetectionProcessor
from lab_vision.evaluation import evaluate, load_deviations, load_truth
from lab_vision.llm import ClaudeLLM, LLMError
from lab_vision.perception import ClaudePerceiver, ReplayPerceiver
from lab_vision.pipeline import Pipeline
from lab_vision.protocol import load_protocol, save_protocol
from lab_vision.sinks import JsonlSink
from lab_vision.structuring import StructuringError, structure_protocol
from lab_vision.video import VideoFileSource
from lab_vision.viewer import Composer, RunView, play, render


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lab-vision", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="check a video against a protocol")
    run.add_argument("--protocol", type=Path, required=True)
    run.add_argument("--video", type=Path, required=True)
    run.add_argument("--out", type=Path, required=True, help="directory for results")
    run.add_argument(
        "--detect",
        action="store_true",
        help="find and track protocol objects, and send close-ups (needs the detection extras)",
    )
    run.add_argument(
        "--dump-frames",
        action="store_true",
        help="save the frames, detections and crops the model sees under OUT/windows",
    )
    run.add_argument(
        "--replay",
        type=Path,
        help="observations.jsonl from an earlier run; skips model calls",
    )

    structure = commands.add_parser(
        "structure", help="turn a plain-text protocol into structured YAML (review the result)"
    )
    structure.add_argument("--text", type=Path, required=True, help="the written protocol")
    structure.add_argument("--out", type=Path, required=True, help="YAML file to write")
    structure.add_argument("--id", dest="protocol_id", help="protocol id (default: file name)")
    structure.add_argument("--title", help="protocol title (default: the id)")

    view = commands.add_parser(
        "view", help="see what the model saw: footage, frames sent, close-ups, answers, deviations"
    )
    view.add_argument("--run", type=Path, required=True, help="a run directory")
    view.add_argument("--video", type=Path, required=True)
    view.add_argument(
        "--out", type=Path, help="render to this video file instead of opening a window"
    )
    view.add_argument("--at", type=float, help="save one still at this time (seconds) as --png")
    view.add_argument("--png", type=Path, help="where to write the still for --at")
    view.add_argument("--start", type=float, default=0.0, help="render from this time (seconds)")
    view.add_argument("--end", type=float, help="render up to this time (seconds)")

    score = commands.add_parser("eval", help="score a run against labelled ground truth")
    score.add_argument("--deviations", type=Path, required=True)
    score.add_argument("--truth", type=Path, required=True)
    score.add_argument("--tolerance", type=float, default=5.0, help="seconds of timing slack")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    handlers = {"run": _run, "eval": _eval, "structure": _structure, "view": _view}
    return handlers[args.command](args)


def _llm(settings) -> ClaudeLLM:
    return ClaudeLLM(
        model=settings.vision_model,
        api_key=settings.claude_api_key,
        effort=settings.vision_effort,
        fallbacks=settings.vision_fallbacks,
    )


def _structure(args: argparse.Namespace) -> int:
    protocol_id = args.protocol_id or args.text.stem
    try:
        protocol = structure_protocol(
            args.text.read_text(encoding="utf-8"),
            _llm(get_settings()),
            protocol_id,
            args.title or protocol_id,
        )
    except (LLMError, StructuringError) as exc:
        print(f"Could not structure the protocol: {exc}", file=sys.stderr)
        return 1
    save_protocol(protocol, args.out)
    checks = sum(len(s.checks) for s in protocol.steps)
    print(f"Wrote {args.out}: {len(protocol.steps)} steps, {checks} checks. Review before use.")
    return 0


def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    llm: ClaudeLLM | None = None
    if args.replay:
        perceiver = ReplayPerceiver.from_jsonl(args.replay)
    else:
        llm = _llm(settings)
        perceiver = ClaudePerceiver(llm)

    processors = []
    if args.detect:
        detector = Owlv2Detector(settings.detection_model, settings.detection_threshold)
        try:
            detector.load()
        except DetectorUnavailable as exc:
            print(exc, file=sys.stderr)
            return 2
        processors.append(DetectionProcessor(detector, min_crop_score=settings.crop_min_score))
    observers = [FrameDumper(args.out)] if args.dump_frames else []

    result = Pipeline(
        protocol=load_protocol(args.protocol),
        source=VideoFileSource(
            args.video, settings.sample_fps, settings.max_image_side, keep_images=args.detect
        ),
        perceiver=perceiver,
        sinks=[JsonlSink(args.out)],
        processors=processors,
        window_observers=observers,
        window_seconds=settings.window_seconds,
        max_frames_per_window=settings.max_frames_per_window,
        lookahead_steps=settings.lookahead_steps,
        min_confidence=settings.min_confidence,
        source_name=args.video.name,
    ).run()

    if llm is not None:
        usage = {
            **llm.usage,
            "requested_model": llm.model,
            "served_models": sorted(llm.served_models),
        }
        (args.out / "usage.json").write_text(json.dumps(usage, indent=2), encoding="utf-8")
        print(f"usage: {llm.usage['input_tokens']} in / {llm.usage['output_tokens']} out tokens")

    s = result.summary
    print(f"{s.windows} windows ({s.failed_windows} failed), {s.observations} observations")
    print(f"{s.deviations} deviations, {s.needs_review} need review")
    for d in result.deviations:
        flag = " [review]" if d.needs_review else ""
        print(f"  {d.kind.value}{flag}: {d.message}")
    return 0


def _view(args: argparse.Namespace) -> int:
    run = RunView.load(args.run)
    if not run.windows:
        print(f"No observations or windows found in {args.run}", file=sys.stderr)
        return 1
    composer = Composer.open(args.video, run)
    try:
        if args.at is not None:
            if args.png is None:
                print("--at needs --png", file=sys.stderr)
                return 2
            index = round(args.at * composer.fps)
            image = composer.frame_at(index)
            if image is None:
                print(f"No frame at {args.at}s", file=sys.stderr)
                return 1
            cv2.imwrite(str(args.png), composer.compose(image, index))
            print(f"Wrote {args.png}")
        elif args.out is not None:
            frames = render(composer, args.out, args.start, args.end)
            print(f"Wrote {args.out}: {frames} frames")
        else:
            try:
                play(composer, args.run)
            except RuntimeError as exc:
                print(exc, file=sys.stderr)
                return 2
    finally:
        composer.close()
    return 0


def _eval(args: argparse.Namespace) -> int:
    report = evaluate(load_truth(args.truth), load_deviations(args.deviations), args.tolerance)
    recall = "n/a" if report.recall is None else f"{report.recall:.0%}"
    print(f"{report.clip_id}: detected {report.detected}/{report.expected} (recall {recall})")
    print(f"false alarms: {len(report.false_alarms)}, review flags: {len(report.review_flags)}")
    for m in report.missed:
        print(f"  missed: {m.kind.value} on {m.step_id}")
    for d in report.false_alarms:
        print(f"  false alarm: {d.message}")
    return 0 if not report.missed and not report.false_alarms else 1


if __name__ == "__main__":
    sys.exit(main())
