from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.imports.service import (
    ImportNotFoundError,
    get_batch_read,
    get_bulk_approval_previews,
    get_import_group,
    get_item_read,
)
from app.models import ExamSubject, KnowledgeNode, QuestionImportBatch, QuestionImportItem

router = APIRouter(tags=["question-import-admin"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
templates = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))


@router.get("/admin/imports", response_class=HTMLResponse)
async def import_index(request: Request, session: SessionDependency) -> HTMLResponse:
    ids = list(
        (
            await session.scalars(
                select(QuestionImportBatch.id)
                .order_by(QuestionImportBatch.created_at.desc())
                .limit(50)
            )
        ).all()
    )
    batches = [
        (await get_batch_read(session, value)).model_dump(mode="json") for value in ids
    ]
    return templates.TemplateResponse(
        request=request,
        name="imports.html",
        context={"batches": batches},
    )


@router.get("/admin/imports/{batch_id}", response_class=HTMLResponse)
async def import_batch_page(
    request: Request, batch_id: uuid.UUID, session: SessionDependency
) -> HTMLResponse:
    try:
        batch = (await get_batch_read(session, batch_id)).model_dump(mode="json")
    except ImportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if batch["parser_version"].startswith("markdown-"):
        return templates.TemplateResponse(request=request, name="markdown.html", context={"batch": batch, "block_id": None})
    legacy_items = list((await session.scalars(select(QuestionImportItem)
        .where(QuestionImportItem.batch_id == batch_id)
        .order_by(QuestionImportItem.question_no))).all())
    return templates.TemplateResponse(request=request, name="legacy.html",
        context={"batch": batch, "items": legacy_items})
    items = list(
        (
            await session.scalars(
                select(QuestionImportItem)
                .where(QuestionImportItem.batch_id == batch_id)
                .order_by(QuestionImportItem.question_no)
            )
        ).all()
    )
    batch_record = await session.get(QuestionImportBatch, batch_id)
    assert batch_record is not None
    approval_previews = await get_bulk_approval_previews(session, batch_record, items)
    item_rows = [
        {
            "id": str(item.id),
            "question_no": item.question_no,
            "status": item.status,
            "errors": sum(
                issue.get("severity") == "error" for issue in item.validation_issues
            ),
            "warnings": sum(
                issue.get("severity") == "warning" for issue in item.validation_issues
            ),
            "approval_eligible": approval_previews[item.id]["eligible"],
            "approval_reason": approval_previews[item.id]["reason"],
            "approval_suggestion": approval_previews[item.id]["suggestion"],
        }
        for item in items
    ]
    return templates.TemplateResponse(
        request=request,
        name="batch.html",
        context={"batch": batch, "items": item_rows},
    )


@router.get("/admin/imports/{batch_id}/blocks/{block_id}", response_class=HTMLResponse)
async def markdown_block_page(request: Request, batch_id: uuid.UUID, block_id: str, session: SessionDependency):
    from app.imports.markdown_api import context, block_for
    batch_record, doc = await context(session, batch_id)
    if not batch_record.parser_version.startswith("markdown-"):
        raise HTTPException(404, "该批次不是 Markdown 导入")
    block_for(doc, block_id)
    batch = (await get_batch_read(session, batch_id)).model_dump(mode="json")
    return templates.TemplateResponse(request=request, name="markdown.html", context={"batch": batch, "block_id": block_id})


@router.get("/admin/imports/{batch_id}/items/{item_id}", response_class=HTMLResponse)
async def import_item_page(
    request: Request,
    batch_id: uuid.UUID,
    item_id: uuid.UUID,
    session: SessionDependency,
) -> HTMLResponse:
    item = await session.get(QuestionImportItem, item_id)
    if item is None or item.batch_id != batch_id:
        raise HTTPException(status_code=404, detail="import item not found")
    item_read = (await get_item_read(session, item_id)).model_dump(mode="json")
    batch = (await get_batch_read(session, batch_id)).model_dump(mode="json")
    group = (
        (await get_import_group(session, item.group_id)).model_dump(mode="json")
        if item.group_id
        else None
    )
    neighbor_rows = (
        await session.execute(
            select(QuestionImportItem.id, QuestionImportItem.question_no)
            .where(QuestionImportItem.batch_id == batch_id)
            .order_by(QuestionImportItem.question_no)
        )
    ).all()
    index = next(i for i, row in enumerate(neighbor_rows) if row.id == item_id)
    previous_id = str(neighbor_rows[index - 1].id) if index > 0 else None
    next_id = str(neighbor_rows[index + 1].id) if index + 1 < len(neighbor_rows) else None

    subject = await session.scalar(
        select(ExamSubject).where(ExamSubject.code == batch["subject_code"])
    )
    topics = list(
        (
            await session.scalars(
                select(KnowledgeNode)
                .where(
                    KnowledgeNode.subject_id == subject.id,
                    KnowledgeNode.node_type == "topic",
                    KnowledgeNode.status == "active",
                )
                .order_by(KnowledgeNode.code)
            )
        ).all()
    )
    topic_values = [{"code": node.code, "name": node.name} for node in topics]
    return templates.TemplateResponse(
        request=request,
        name="review.html",
        context={
            "batch": batch,
            "item": item_read,
            "option_map": {value["key"]: value["content_markdown"] for value in item_read["options"]},
            "group": group,
            "topics": topic_values,
            "previous_id": previous_id,
            "next_id": next_id,
        },
    )
