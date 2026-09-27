from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.repository import (
    KnowledgeNodeNotFoundError,
    SubjectNotFoundError,
    get_knowledge_node_detail,
    get_knowledge_tree,
)
from app.schemas import HealthResponse, KnowledgeNodeDetail, KnowledgeTreeNode

router = APIRouter()
SessionDependency = Annotated[AsyncSession, Depends(get_session)]


@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/api/v1/subjects/{subject_code}/knowledge-tree",
    response_model=list[KnowledgeTreeNode],
    tags=["knowledge"],
)
async def knowledge_tree(
    subject_code: str,
    session: SessionDependency,
    include_deprecated: bool = False,
    max_depth: Annotated[int | None, Query(ge=0, le=2)] = None,
) -> list[KnowledgeTreeNode]:
    try:
        return await get_knowledge_tree(
            session,
            subject_code,
            include_deprecated=include_deprecated,
            max_depth=max_depth,
        )
    except SubjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Exam subject not found") from exc


@router.get(
    "/api/v1/subjects/{subject_code}/knowledge-nodes/{node_code}",
    response_model=KnowledgeNodeDetail,
    tags=["knowledge"],
)
async def knowledge_node(
    subject_code: str,
    node_code: str,
    session: SessionDependency,
    include_ancestors: bool = True,
    include_descendants: bool = True,
    include_deprecated: bool = False,
) -> KnowledgeNodeDetail:
    try:
        return await get_knowledge_node_detail(
            session,
            subject_code,
            node_code,
            include_ancestors=include_ancestors,
            include_descendants=include_descendants,
            include_deprecated=include_deprecated,
        )
    except SubjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Exam subject not found") from exc
    except KnowledgeNodeNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Knowledge node not found") from exc

