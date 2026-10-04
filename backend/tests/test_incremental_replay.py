import uuid

import pytest

from app.database import session_factory
from app.models import Excerpt, Source, Workspace
from app.schemas import (
    AnnotationCreateOperation,
    GraphPatchRequest,
    ProvenanceInput,
    StatementCreateOperation,
)
from app.services.graph_patches import GraphPatchExecutor
from app.services.verification import run_verification


def provenance() -> ProvenanceInput:
    return ProvenanceInput(actor_type="user", actor_id="incremental-replay-test")


async def create_source(session, workspace_id: uuid.UUID, text: str) -> Excerpt:
    source = Source(
        workspace_id=workspace_id,
        kind="document",
        origin="fixture",
        mime_type="text/markdown",
        original_filename="fixture.md",
        storage_key=f"test/{uuid.uuid4()}",
        content_hash=uuid.uuid4().hex,
        external_ids={},
        metadata_={},
    )
    session.add(source)
    await session.flush()
    excerpt = Excerpt(source_id=source.id, text=text, locator={"sequence": 0}, sequence=0)
    session.add(excerpt)
    await session.commit()
    await session.refresh(excerpt)
    return excerpt


@pytest.mark.asyncio
async def test_later_contradictory_source_opens_conflict_without_replacing_initial_claim() -> None:
    async with session_factory() as session:
        workspace = Workspace(title="incremental replay test")
        session.add(workspace)
        await session.commit()
        await session.refresh(workspace)
        try:
            initial_excerpt = await create_source(
                session, workspace.id, "Treated mice completed the maze faster than untreated mice."
            )
            initial_patch = await GraphPatchExecutor(session, workspace.id).apply(
                GraphPatchRequest(
                    idempotency_key=f"initial:{workspace.id}",
                    operations=[
                        StatementCreateOperation(
                            op="create_statement",
                            client_ref="initial_claim",
                            text="NeuroBoost improves maze completion time.",
                            assertion_mode="reported",
                            excerpt_ids=[initial_excerpt.id],
                            provenance=provenance(),
                        ),
                        AnnotationCreateOperation(
                            op="create_annotation",
                            subject_type="statement",
                            subject_id="initial_claim",
                            type="claim_key",
                            value={"key": "neuroboost:maze_time", "polarity": "supports"},
                            provenance=provenance(),
                        ),
                    ],
                )
            )
            first_run = await run_verification(session, workspace.id)
            assert first_run.issues_opened == 0

            update_excerpt = await create_source(
                session,
                workspace.id,
                "A blinded replication found no difference in maze completion time.",
            )
            patch = await GraphPatchExecutor(session, workspace.id).apply(
                GraphPatchRequest(
                    idempotency_key=f"update:{workspace.id}",
                    operations=[
                        StatementCreateOperation(
                            op="create_statement",
                            client_ref="update_claim",
                            text="A blinded replication found no NeuroBoost maze-time effect.",
                            assertion_mode="reported",
                            excerpt_ids=[update_excerpt.id],
                            provenance=provenance(),
                        ),
                        AnnotationCreateOperation(
                            op="create_annotation",
                            subject_type="statement",
                            subject_id="update_claim",
                            type="claim_key",
                            value={"key": "neuroboost:maze_time", "polarity": "refutes"},
                            provenance=provenance(),
                        ),
                    ],
                )
            )
            second_run = await run_verification(session, workspace.id)

            assert "initial_claim" in initial_patch.id_map
            assert "update_claim" in patch.id_map
            assert second_run.issues_opened == 2
            assert second_run.obligations_opened == 2
        finally:
            workspace = await session.get(Workspace, workspace.id)
            if workspace is not None:
                await session.delete(workspace)
                await session.commit()
