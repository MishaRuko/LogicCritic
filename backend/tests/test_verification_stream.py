import json
import uuid

import httpx
import pytest

from app.database import engine
from app.main import app
from app.services.verification import RULE_CODES


@pytest.fixture
async def client():
    await engine.dispose()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        workspace = (await client.post(
            "/api/workspaces", json={"title": "Live verification test"}
        )).json()
        client.base = f"/api/workspaces/{workspace['id']}"
        yield client
        await client.delete(client.base)
    await engine.dispose()


async def test_stream_reports_real_rule_findings_and_commits_verification(client):
    patch = await client.post(f"{client.base}/graph-patches", json={
        "idempotency_key": str(uuid.uuid4()),
        "operations": [{
            "op": "create_statement", "client_ref": "claim", "text": "Unsupported claim",
            "assertion_mode": "asserted", "role": "conclusion", "excerpt_ids": [],
            "provenance": {"actor_type": "user", "actor_id": "test"},
        }],
    })
    assert patch.status_code == 200, patch.text
    graph = (await client.get(f"{client.base}/graph")).json()
    node_id = graph["statements"][0]["id"]
    response = await client.post(f"{client.base}/verify/stream")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[0] == {"type": "started", "rules": RULE_CODES}
    assert [e["rule_code"] for e in events if e["type"] == "rule_started"] == RULE_CODES
    assert [e["rule_code"] for e in events if e["type"] == "rule_completed"] == RULE_CODES
    grounding = events[2]
    assert grounding["rule_code"] == "ungrounded_statement"
    assert grounding["node_ids"] == [node_id]
    assert grounding["findings"][0]["node_id"] == node_id
    assert events[-1]["type"] == "completed"
    assert events[-1]["result"]["issues_opened"] == 1
    # Completion is emitted only after the same durable handoff used by experiments is saved.
    assert (await client.get(f"{client.base}/experiments")).json()["verified"] is True
    again = (await client.post(f"{client.base}/verify")).json()
    assert again["issues_opened"] == 0 and again["issues_resolved"] == 0


async def test_missing_workspace_fails_before_a_stream_is_started(client):
    response = await client.post(f"/api/workspaces/{uuid.uuid4()}/verify/stream")
    assert response.status_code == 404
