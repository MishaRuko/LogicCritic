import pytest

from app.services import literature


@pytest.fixture(autouse=True)
def no_open_paper_indexes(monkeypatch):
    """Agent runs in tests search only the Amass fakes they are given, never the live indexes."""
    monkeypatch.setattr(literature, "get_literature_client", lambda: None)
