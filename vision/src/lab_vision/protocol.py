import json
from pathlib import Path

import yaml

from lab_vision.models import Protocol


def load_protocol(path: Path) -> Protocol:
    """Load a protocol from a YAML or JSON file and validate it."""
    text = path.read_text(encoding="utf-8")
    data = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    return Protocol.model_validate(data)


def save_protocol(protocol: Protocol, path: Path) -> None:
    """Write a protocol as YAML, omitting unset fields so the file stays readable."""
    data = protocol.model_dump(mode="json", exclude_none=True)
    for step in data["steps"]:
        for key in ("objects", "checks", "obligation_ids"):
            if not step.get(key):
                step.pop(key, None)
        if not step.get("optional"):
            step.pop("optional", None)
        for check in step.get("checks", []):
            if not check.get("accept"):
                check.pop("accept", None)
            if not check.get("tolerance"):
                check.pop("tolerance", None)
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
    path.write_text(text, encoding="utf-8")
