import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Annotation,
    Excerpt,
    GraphEdge,
    GraphEvent,
    ReasoningPremise,
    ReasoningStep,
    Source,
    Statement,
    StatementExcerpt,
)
from app.schemas import (
    AnnotationCreateOperation,
    GraphPatchRequest,
    GraphPatchResponse,
    RelationCreateOperation,
    ReasoningStepCreateOperation,
    StatementCreateOperation,
)


def provenance_dict(provenance) -> dict:
    return provenance.model_dump(mode="json", exclude_none=True)


def graph_patch_response_from_event(event: GraphEvent) -> GraphPatchResponse:
    result = event.payload["result"]
    return GraphPatchResponse(
        patch_id=event.id,
        id_map={key: uuid.UUID(value) for key, value in result["id_map"].items()},
        accepted_event_ids=[event.id],
        affected_node_ids=[uuid.UUID(item) for item in result["affected_node_ids"]],
    )


def validate_reasoning_ids(conclusion_id: uuid.UUID, premise_ids: list[uuid.UUID]) -> None:
    if not premise_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A reasoning step requires at least one premise",
        )
    if len(set(premise_ids)) != len(premise_ids):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A reasoning step cannot repeat a premise",
        )
    if conclusion_id in premise_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A reasoning step cannot use its conclusion as a premise",
        )


def validate_annotation_value(type_: str, value: dict) -> None:
    if type_.startswith("custom:"):
        return
    required: dict[str, tuple[str, ...]] = {
        "claim_strength": ("value",),
        "causal_support": ("supported",),
        "scope_transition": ("from", "to", "justified"),
        "required_premise": ("description", "satisfied"),
        "claim_key": ("key", "polarity"),
    }
    if type_ not in required:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown annotation type: {type_}. Use a documented type or the custom: namespace.",
        )
    missing = [field for field in required[type_] if field not in value]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Annotation {type_} is missing fields: {', '.join(missing)}",
        )
    if type_ == "claim_strength" and value["value"] not in {"causal", "associative", "descriptive"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid claim_strength value")
    if type_ == "claim_key" and value["polarity"] not in {"supports", "refutes"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid claim_key polarity")
    bool_fields = {
        "causal_support": "supported",
        "scope_transition": "justified",
        "required_premise": "satisfied",
    }
    if type_ in bool_fields and not isinstance(value[bool_fields[type_]], bool):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Annotation {type_} requires a boolean")


class GraphPatchExecutor:
    def __init__(self, session: AsyncSession, workspace_id: uuid.UUID) -> None:
        self.session = session
        self.workspace_id = workspace_id
        self.id_map: dict[str, uuid.UUID] = {}
        self.created_kinds: dict[str, str] = {}
        self.affected_node_ids: list[uuid.UUID] = []

    async def apply(self, request: GraphPatchRequest) -> GraphPatchResponse:
        existing = await self.session.scalar(
            select(GraphEvent).where(
                GraphEvent.workspace_id == self.workspace_id,
                GraphEvent.idempotency_key == request.idempotency_key,
            )
        )
        if existing is not None:
            return self._existing_response(existing)

        self._validate_client_references(request)
        try:
            for operation in request.operations:
                if isinstance(operation, StatementCreateOperation):
                    await self._create_statement(operation)

            for operation in request.operations:
                if isinstance(operation, ReasoningStepCreateOperation):
                    await self._create_reasoning_step(operation)

            for operation in request.operations:
                if isinstance(operation, RelationCreateOperation):
                    await self._create_relation(operation)
                elif isinstance(operation, AnnotationCreateOperation):
                    await self._create_annotation(operation)

            event = GraphEvent(
                workspace_id=self.workspace_id,
                event_type="graph_patch",
                idempotency_key=request.idempotency_key,
                payload={
                    "request": request.model_dump(mode="json"),
                    "result": {
                        "id_map": {key: str(value) for key, value in self.id_map.items()},
                        "affected_node_ids": [str(item) for item in self.affected_node_ids],
                    },
                },
                provenance={"actor_type": "integration", "actor_id": "graph_patch_api"},
            )
            self.session.add(event)
            await self.session.commit()
            await self.session.refresh(event)
        except IntegrityError:
            await self.session.rollback()
            existing = await self.session.scalar(
                select(GraphEvent).where(
                    GraphEvent.workspace_id == self.workspace_id,
                    GraphEvent.idempotency_key == request.idempotency_key,
                )
            )
            if existing is None:
                raise
            return self._existing_response(existing)

        return GraphPatchResponse(
            patch_id=event.id,
            id_map=self.id_map,
            accepted_event_ids=[event.id],
            affected_node_ids=self.affected_node_ids,
        )

    def _existing_response(self, event: GraphEvent) -> GraphPatchResponse:
        return graph_patch_response_from_event(event)

    def _validate_client_references(self, request: GraphPatchRequest) -> None:
        for operation in request.operations:
            if isinstance(operation, (StatementCreateOperation, ReasoningStepCreateOperation)):
                if operation.client_ref in self.created_kinds:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"Duplicate client_ref: {operation.client_ref}",
                    )
                self.created_kinds[operation.client_ref] = (
                    "statement" if isinstance(operation, StatementCreateOperation) else "reasoning_step"
                )

    async def _resolve_node_id(self, value: str | uuid.UUID, expected_kind: str) -> uuid.UUID:
        if isinstance(value, str) and value in self.id_map:
            node_id = self.id_map[value]
            if self.created_kinds[value] != expected_kind:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Client reference {value} is not a {expected_kind}",
                )
            return node_id

        try:
            node_id = uuid.UUID(str(value))
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown client reference or invalid UUID: {value}",
            ) from error

        model = Statement if expected_kind == "statement" else ReasoningStep
        node = await self.session.get(model, node_id)
        if node is None or node.workspace_id != self.workspace_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{expected_kind} does not exist in this workspace: {node_id}",
            )
        return node_id

    async def _create_statement(self, operation: StatementCreateOperation) -> None:
        if operation.client_ref in self.id_map:
            return

        if operation.excerpt_ids:
            if len(set(operation.excerpt_ids)) != len(operation.excerpt_ids):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="A statement cannot cite the same excerpt more than once",
                )
            excerpts = list(
                await self.session.scalars(
                    select(Excerpt)
                    .join(Source, Excerpt.source_id == Source.id)
                    .where(Excerpt.id.in_(operation.excerpt_ids), Source.workspace_id == self.workspace_id)
                )
            )
            found_ids = {item.id for item in excerpts}
            missing_ids = set(operation.excerpt_ids) - found_ids
            if missing_ids:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Excerpt IDs do not exist in this workspace: {sorted(map(str, missing_ids))}",
                )

        statement = Statement(
            workspace_id=self.workspace_id,
            text=operation.text,
            assertion_mode=operation.assertion_mode,
            role=operation.role,
            lifecycle=operation.lifecycle,
            provenance=provenance_dict(operation.provenance),
        )
        self.session.add(statement)
        await self.session.flush()
        self.id_map[operation.client_ref] = statement.id
        self.affected_node_ids.append(statement.id)
        self.session.add_all(
            [StatementExcerpt(statement_id=statement.id, excerpt_id=excerpt_id) for excerpt_id in operation.excerpt_ids]
        )

    async def _create_reasoning_step(self, operation: ReasoningStepCreateOperation) -> None:
        conclusion_id = await self._resolve_node_id(operation.conclusion_id, "statement")
        premise_ids = [await self._resolve_node_id(item, "statement") for item in operation.premise_ids]
        validate_reasoning_ids(conclusion_id, premise_ids)
        if await self._would_create_reasoning_cycle(conclusion_id, premise_ids):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A reasoning step cannot introduce a cyclic dependency",
            )

        step = ReasoningStep(
            workspace_id=self.workspace_id,
            conclusion_id=conclusion_id,
            explanation=operation.explanation,
            lifecycle=operation.lifecycle,
            provenance=provenance_dict(operation.provenance),
        )
        self.session.add(step)
        await self.session.flush()
        self.id_map[operation.client_ref] = step.id
        self.affected_node_ids.extend([step.id, conclusion_id, *premise_ids])
        self.session.add_all(
            [
                ReasoningPremise(reasoning_step_id=step.id, statement_id=premise_id, position=position)
                for position, premise_id in enumerate(premise_ids)
            ]
        )

    async def _would_create_reasoning_cycle(
        self, conclusion_id: uuid.UUID, premise_ids: list[uuid.UUID]
    ) -> bool:
        """A new premise -> conclusion edge is invalid if conclusion already reaches a premise."""
        rows = await self.session.execute(
            select(ReasoningPremise.statement_id, ReasoningStep.conclusion_id)
            .join(ReasoningStep, ReasoningStep.id == ReasoningPremise.reasoning_step_id)
            .where(ReasoningStep.workspace_id == self.workspace_id)
        )
        outgoing: dict[uuid.UUID, set[uuid.UUID]] = {}
        for premise_id, existing_conclusion_id in rows:
            outgoing.setdefault(premise_id, set()).add(existing_conclusion_id)

        for premise_id in premise_ids:
            pending = [conclusion_id]
            visited: set[uuid.UUID] = set()
            while pending:
                current = pending.pop()
                if current == premise_id:
                    return True
                if current in visited:
                    continue
                visited.add(current)
                pending.extend(outgoing.get(current, set()) - visited)
        return False

    async def _create_relation(self, operation: RelationCreateOperation) -> None:
        source_id = await self._resolve_node_id(operation.source_node_id, operation.source_node_kind)
        target_id = await self._resolve_node_id(operation.target_node_id, operation.target_node_kind)
        if source_id == target_id and operation.source_node_kind == operation.target_node_kind:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A relation cannot target the same node",
            )
        edge = GraphEdge(
            workspace_id=self.workspace_id,
            source_node_kind=operation.source_node_kind,
            source_node_id=source_id,
            relation=operation.relation,
            target_node_kind=operation.target_node_kind,
            target_node_id=target_id,
            metadata_=operation.metadata,
        )
        self.session.add(edge)
        self.affected_node_ids.extend([source_id, target_id])

    async def _create_annotation(self, operation: AnnotationCreateOperation) -> None:
        subject_id = await self._resolve_node_id(operation.subject_id, operation.subject_type)
        validate_annotation_value(operation.type, operation.value)
        annotation = Annotation(
            workspace_id=self.workspace_id,
            subject_type=operation.subject_type,
            subject_id=subject_id,
            type=operation.type,
            value=operation.value,
            provenance=provenance_dict(operation.provenance),
            confidence=operation.confidence,
            status=operation.status,
        )
        self.session.add(annotation)
        self.affected_node_ids.append(subject_id)
