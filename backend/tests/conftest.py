import pytest

from app.services import literature


@pytest.fixture(autouse=True)
def no_open_paper_indexes(monkeypatch):
    """Agent runs in tests search only the Amass fakes they are given, never the live indexes."""
    monkeypatch.setattr(literature, "get_literature_client", lambda: None)


@pytest.fixture
def ample_evidence(monkeypatch):
    """For tests of the verifier's rules: the evidence and the search behind a conclusion are
    taken to be ample, so only the obligations limit its certainty. The evidence ceiling itself
    is tested in test_agent_evidence.py."""
    from app.agent import evidence

    async def three_studies(session, cone):
        return [evidence.EvidenceSource(str(n), f"Study {n}", "scholarly", False) for n in range(3)]

    async def searches(session, workspace_id):
        return evidence.Effort(searches=4, against=True)

    monkeypatch.setattr(evidence, "cone_sources", three_studies)
    monkeypatch.setattr(evidence, "search_effort", searches)
