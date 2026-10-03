import uuid

import pytest

from app.models import Excerpt
from app.services.extraction_jobs import chunk_excerpt_ids


def make_excerpt(text: str) -> Excerpt:
    return Excerpt(id=uuid.uuid4(), source_id=uuid.uuid4(), text=text, locator={}, sequence=0)


def test_chunks_only_at_excerpt_boundaries() -> None:
    first = make_excerpt("a" * 20)
    second = make_excerpt("b" * 20)
    third = make_excerpt("c" * 20)

    chunks = chunk_excerpt_ids([first, second, third], limit=180)

    assert chunks == [[first.id, second.id], [third.id]]


def test_rejects_an_excerpt_larger_than_the_context_limit() -> None:
    with pytest.raises(ValueError, match="exceeds the extraction context limit"):
        chunk_excerpt_ids([make_excerpt("a" * 100)], limit=99)
