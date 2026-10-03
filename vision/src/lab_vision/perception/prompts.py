import base64
from typing import Any

from lab_vision.models import CheckKind
from lab_vision.perception.base import PerceptionRequest

PROMPT_VERSION = "step-check-v4"

SYSTEM_PROMPT = """\
You monitor a laboratory video against a written protocol. You are shown timestamped frames \
and a list of protocol steps. Report only what the frames show.

Rules:
- Never assume a step happened because it is expected. If the frames do not show it, say so.
- For each step, give a status: "performed" (completed within these frames), "in_progress", \
"not_observed" (clearly not happening), or "unclear" (cannot tell).
- Answer each check question by reading the value from the frames. If you cannot read it, \
omit it or give a low confidence. Do not guess.
- Confidence is a number from 0 to 1 for how clearly the frames show your answer.
- Some windows include close-up crops and detector notes from an automatic object detector. \
They can be wrong: a crop may show the wrong object, and a detector note may miss something \
visible. Use them to help read values, but trust the full frames when they disagree.
- Use "unexpected_events" only for something that departs from the protocol or puts the \
experiment at risk: different apparatus or reagent than specified, a dropped or contaminated \
item, a safety problem. Ordinary preparation and handling is not unexpected. Report each \
event once, not again in every window.
Include one entry in "observations" for every listed step."""


def build_user_content(request: PerceptionRequest) -> list[dict[str, Any]]:
    """Text and images for one request. Expected values are deliberately left out."""
    lines = ["Protocol steps to check in these frames:"]
    for step in request.steps:
        lines.append(f'\nStep "{step.id}": {step.description}')
        if step.objects:
            lines.append(f"  Expected apparatus: {', '.join(step.objects)}")
        for check in step.checks:
            unit = (
                f" (report as a number in {check.unit})"
                if check.kind is CheckKind.NUMERIC and check.unit
                else ""
            )
            lines.append(f'  Check "{check.id}": {check.question}{unit}')
    content: list[dict[str, Any]] = [{"type": "text", "text": "\n".join(lines)}]
    for frame in request.window.frames:
        content.append({"type": "text", "text": f"Frame at {frame.timestamp_s:.1f}s:"})
        encoded = base64.b64encode(frame.jpeg).decode("ascii")
        content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": encoded},
            }
        )
    for crop in request.window.crops:
        content.append(
            {
                "type": "text",
                "text": (
                    f"Close-up of {crop.label} ({crop.track_id}) from the frame at "
                    f"{crop.timestamp_s:.1f}s, enlarged for reading values:"
                ),
            }
        )
        encoded = base64.b64encode(crop.jpeg).decode("ascii")
        content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": encoded},
            }
        )
    if request.window.notes:
        listed = "\n".join(f"- {note}" for note in request.window.notes)
        content.append({"type": "text", "text": f"Automatic detector notes:\n{listed}"})
    return content
