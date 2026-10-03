import uuid

import pytest
from fastapi import HTTPException

from app.services.graph_patches import validate_annotation_value, validate_reasoning_ids


def test_rejects_a_reasoning_step_that_uses_its_conclusion_as_a_premise() -> None:
    statement_id = uuid.uuid4()

    with pytest.raises(HTTPException, match="conclusion as a premise"):
        validate_reasoning_ids(statement_id, [statement_id])


def test_rejects_an_invalid_rule_annotation() -> None:
    with pytest.raises(HTTPException, match="Invalid claim_key polarity"):
        validate_annotation_value("claim_key", {"key": "example", "polarity": "maybe"})
