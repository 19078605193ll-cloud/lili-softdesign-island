from __future__ import annotations

import uuid
import logging
from datetime import date
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_session
from app.imports.ai import AIConfigurationError, OpenAICompatibleQuestionClient, QuestionAIClient
from app.imports.schemas import (
    AssetCropCreate,
    AssetRead,
    BulkApproveItemsInput,
    BulkApproveItemsResult,
    ClassificationProgress,
    ImportBatchRead,
    ImportBatchMetadataUpdate,
    ImportGroupRead,
    ImportGroupUpdate,
    ImportItemRead,
    ImportItemUpdate,
    KnowledgeSelectionInput,
    ParseProgress,
    PublishResult,
    RepairProgress,
    RejectItemInput,
    SourceSectionSelection,
)
from app.imports.service import (
    BulkApprovalError,
    ImportConflictError,
    ImportNotFoundError,
    ImportWorkflowError,
    approve_import_item,
    approve_import_items,
    classify_batch,
    create_batch_record,
    delete_unpublished_batch,
    create_question_asset,
    get_batch_read,
    get_import_group,
    get_item_read,
    list_import_items,
    parse_batch,
    publish_batch,
    repair_batch,
    reconcile_batch,
    reject_import_item,
    set_document_sections,
    suggest_document_sections,
    update_batch_metadata,
    update_import_item,
    update_import_group,
)
from app.imports.storage import ImportStorageError, LocalImportStorage
from app.models import QuestionAsset, QuestionImportBatch, QuestionSourceDocument

router = APIRouter(prefix="/api/v1/admin", tags=["question-import"])
logger = logging.getLogger(__name__)
from app.imports.dependencies import SessionDependency, StorageDependency, AIClientDependency, get_import_storage, get_ai_client, _http_error


@router.post("/import-batches", response_model=ImportBatchRead, status_code=201)
async def create_import_batch(
    session: SessionDependency,
    storage: StorageDependency,
    file: Annotated[UploadFile, File()],
    subject_code: Annotated[str, Form()] = "software-designer.foundation",
    taxonomy_version: Annotated[str | None, Form()] = None,
    year: Annotated[int, Form(ge=2000, le=2100)] = 2020,
    period: Annotated[str, Form(pattern="^(first_half|second_half|other)$")] = "second_half",
    batch_code: Annotated[str, Form(min_length=1, max_length=50)] = "default",
    title: Annotated[str, Form(min_length=1, max_length=300)] = "软件设计师基础知识真题",
    exam_date: Annotated[date | None, Form()] = None,
    source_reference: Annotated[str | None, Form()] = None,
) -> ImportBatchRead:
    batch_id_for_cleanup: uuid.UUID | None = None
    commit_attempted = False
    try:
        batch = await create_batch_record(
            session,
            subject_code=subject_code,
            taxonomy_version=taxonomy_version,
            year=year,
            period=period,
            batch_code=batch_code,
            title=title,
            exam_date=exam_date,
            source_reference=source_reference,
        )
        batch_id_for_cleanup = batch.id
        from app.imports.markdown_storage import unpack
        from app.imports.markdown_workflow import initialize
        data = await file.read(storage.max_bytes + 1)
        document_path = unpack(storage, batch.id, file.filename or "source.md", data)
        await initialize(session, batch, storage, document_path, file.filename or "source.md",
                         storage.resolve(f"{batch.id}/markdown"))
        commit_attempted = True
        await session.commit()
        return await get_batch_read(session, batch.id)
    except Exception as exc:
        await session.rollback()
        # A transport error during commit is ambiguous. Preserve the directory
        # for storage reconciliation instead of deleting possibly committed data.
        if batch_id_for_cleanup is not None and not commit_attempted:
            storage.remove_batch(batch_id_for_cleanup)
        raise _http_error(exc) from exc
    finally:
        await file.close()


@router.get("/import-batches", response_model=list[ImportBatchRead])
async def get_import_batches(
    session: SessionDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[ImportBatchRead]:
    ids = list(
        (
            await session.scalars(
                select(QuestionImportBatch.id)
                .order_by(QuestionImportBatch.created_at.desc())
                .limit(limit)
            )
        ).all()
    )
    return [await get_batch_read(session, value) for value in ids]


@router.get("/import-batches/{batch_id}", response_model=ImportBatchRead)
async def get_import_batch(batch_id: uuid.UUID, session: SessionDependency) -> ImportBatchRead:
    try:
        return await get_batch_read(session, batch_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.patch("/import-batches/{batch_id}", response_model=ImportBatchRead)
async def patch_import_batch(
    batch_id: uuid.UUID, payload: ImportBatchMetadataUpdate, session: SessionDependency
) -> ImportBatchRead:
    try:
        await update_batch_metadata(session, batch_id, payload)
        await session.commit()
        return await get_batch_read(session, batch_id)
    except Exception as exc:
        await session.rollback()
        raise _http_error(exc) from exc


@router.post("/import-batches/{batch_id}/suggest-sections", response_model=ImportBatchRead)
async def suggest_sections(
    batch_id: uuid.UUID,
    session: SessionDependency,
    storage: StorageDependency,
    ai_client: AIClientDependency,
    retry: bool = False,
) -> ImportBatchRead:
    try:
        return await suggest_document_sections(
            session, batch_id, ai_client=ai_client, storage=storage, retry=retry
        )
    except Exception as exc:
        await session.rollback()
        raise _http_error(exc) from exc


@router.delete("/import-batches/{batch_id}")
async def delete_import_batch(
    batch_id: uuid.UUID, session: SessionDependency, storage: StorageDependency
) -> dict:
    try:
        await delete_unpublished_batch(session, batch_id)
        await session.commit()
    except Exception as exc:
        await session.rollback()
        raise _http_error(exc) from exc
    try:
        storage.remove_batch(batch_id)
        return {"batch_id": batch_id, "deleted": True, "warning": None}
    except Exception as exc:
        pending_path = str(storage.resolve(str(batch_id)))
        logger.exception("Import batch %s database deleted; pending file cleanup: %s", batch_id, pending_path)
        return {
            "batch_id": batch_id,
            "deleted": True,
            "warning": f"记录已删除，但文件清理失败：{exc}。待清理目录：{pending_path}",
        }


@router.put("/import-batches/{batch_id}/sections", response_model=ImportBatchRead)
async def update_sections(
    batch_id: uuid.UUID,
    payload: SourceSectionSelection,
    session: SessionDependency,
) -> ImportBatchRead:
    try:
        await set_document_sections(session, batch_id, payload)
        await session.commit()
        return await get_batch_read(session, batch_id)
    except Exception as exc:
        await session.rollback()
        raise _http_error(exc) from exc


@router.post("/import-batches/{batch_id}/parse", response_model=ParseProgress)
async def parse_import_batch(
    batch_id: uuid.UUID,
    session: SessionDependency,
    storage: StorageDependency,
    ai_client: AIClientDependency,
    limit: Annotated[int, Query(ge=1, le=50)] = 5,
    retry_failed: bool = True,
) -> ParseProgress:
    try:
        return await parse_batch(
            session, batch_id, ai_client=ai_client, storage=storage, limit=limit,
            retry_failed=retry_failed,
        )
    except Exception as exc:
        await session.rollback()
        raise _http_error(exc) from exc


@router.post("/import-batches/{batch_id}/classify", response_model=ClassificationProgress)
async def classify_import_batch(
    batch_id: uuid.UUID,
    session: SessionDependency,
    storage: StorageDependency,
    ai_client: AIClientDependency,
    limit: Annotated[int, Query(ge=1, le=5)] = 5,
) -> ClassificationProgress:
    try:
        result = await classify_batch(
            session,
            batch_id,
            ai_client=ai_client,
            limit=limit,
            storage=storage,
        )
        await session.commit()
        return result
    except Exception as exc:
        await session.rollback()
        raise _http_error(exc) from exc


@router.post("/import-batches/{batch_id}/repair", response_model=RepairProgress)
async def repair_import_batch(
    batch_id: uuid.UUID,
    session: SessionDependency,
    storage: StorageDependency,
    ai_client: AIClientDependency,
    limit: Annotated[int, Query(ge=1, le=10)] = 1,
) -> RepairProgress:
    try:
        result = await repair_batch(
            session,
            batch_id,
            ai_client=ai_client,
            storage=storage,
            limit=limit,
        )
        await session.commit()
        return result
    except Exception as exc:
        await session.rollback()
        raise _http_error(exc) from exc


@router.post("/import-batches/{batch_id}/reconcile")
async def reconcile_import_batch(
    batch_id: uuid.UUID, session: SessionDependency, storage: StorageDependency,
    apply: bool = False,
) -> dict:
    try:
        result = await reconcile_batch(session, batch_id, storage, apply=apply)
        if apply:
            await session.commit()
        return result
    except Exception as exc:
        await session.rollback()
        raise _http_error(exc) from exc


@router.get("/import-batches/{batch_id}/items", response_model=list[ImportItemRead])
async def get_import_items(
    batch_id: uuid.UUID,
    session: SessionDependency,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 75,
) -> list[ImportItemRead]:
    try:
        return await list_import_items(session, batch_id, offset=offset, limit=limit)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/import-items/{item_id}", response_model=ImportItemRead)
async def get_import_item(item_id: uuid.UUID, session: SessionDependency) -> ImportItemRead:
    try:
        return await get_item_read(session, item_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/import-groups/{group_id}", response_model=ImportGroupRead)
async def get_group(group_id: uuid.UUID, session: SessionDependency) -> ImportGroupRead:
    try:
        return await get_import_group(session, group_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.patch("/import-groups/{group_id}", response_model=ImportGroupRead)
async def patch_group(
    group_id: uuid.UUID,
    payload: ImportGroupUpdate,
    session: SessionDependency,
) -> ImportGroupRead:
    try:
        result = await update_import_group(session, group_id, payload)
        await session.commit()
        return result
    except Exception as exc:
        await session.rollback()
        raise _http_error(exc) from exc


@router.patch("/import-items/{item_id}", response_model=ImportItemRead)
async def patch_import_item(
    item_id: uuid.UUID,
    payload: ImportItemUpdate,
    session: SessionDependency,
) -> ImportItemRead:
    try:
        await update_import_item(session, item_id, payload)
        await session.commit()
        return await get_item_read(session, item_id)
    except Exception as exc:
        await session.rollback()
        raise _http_error(exc) from exc


@router.post("/import-items/{item_id}/approve", response_model=ImportItemRead)
async def approve_item(
    item_id: uuid.UUID,
    payload: KnowledgeSelectionInput,
    session: SessionDependency,
) -> ImportItemRead:
    try:
        await approve_import_item(session, item_id, payload)
        await session.commit()
        return await get_item_read(session, item_id)
    except Exception as exc:
        await session.rollback()
        raise _http_error(exc) from exc


@router.post(
    "/import-batches/{batch_id}/approve-items",
    response_model=BulkApproveItemsResult,
)
async def approve_items(
    batch_id: uuid.UUID,
    payload: BulkApproveItemsInput,
    session: SessionDependency,
) -> BulkApproveItemsResult:
    try:
        result = await approve_import_items(session, batch_id, payload.item_ids)
        await session.commit()
        return result
    except BulkApprovalError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=422,
            detail={
                "message": "批量批准失败，未修改任何题目",
                "items": exc.items,
            },
        ) from exc
    except Exception as exc:
        await session.rollback()
        raise _http_error(exc) from exc


@router.post("/import-items/{item_id}/reject", response_model=ImportItemRead)
async def reject_item(
    item_id: uuid.UUID,
    payload: RejectItemInput,
    session: SessionDependency,
) -> ImportItemRead:
    try:
        await reject_import_item(session, item_id, payload.reason)
        await session.commit()
        return await get_item_read(session, item_id)
    except Exception as exc:
        await session.rollback()
        raise _http_error(exc) from exc


@router.post("/import-items/{item_id}/assets", response_model=AssetRead, status_code=201)
async def crop_item_asset(
    item_id: uuid.UUID,
    payload: AssetCropCreate,
    session: SessionDependency,
    storage: StorageDependency,
) -> AssetRead:
    try:
        asset = await create_question_asset(session, item_id, payload, storage)
        await session.commit()
        return AssetRead.model_validate(asset)
    except Exception as exc:
        await session.rollback()
        raise _http_error(exc) from exc


@router.post("/import-batches/{batch_id}/publish", response_model=PublishResult)
async def publish_import_batch(
    batch_id: uuid.UUID, session: SessionDependency, storage: StorageDependency
) -> PublishResult:
    try:
        from app.imports.markdown_workflow import is_markdown, publish
        from fastapi.responses import JSONResponse
        batch = await session.get(QuestionImportBatch, batch_id)
        if batch is not None and is_markdown(batch):
            result = await publish(session, batch_id, storage)
            await session.commit()
            return JSONResponse(result)
        raise ImportConflictError("旧导入仅保留查看，请改用 Markdown 导入")
    except Exception as exc:
        await session.rollback()
        raise _http_error(exc) from exc


@router.get("/source-documents/{document_id}/pages/{page_no}", response_class=FileResponse)
async def get_source_page(
    document_id: uuid.UUID,
    page_no: int,
    session: SessionDependency,
    storage: StorageDependency,
) -> FileResponse:
    document = await session.get(QuestionSourceDocument, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="source document not found")
    try:
        path = storage.page_image_path(document.batch_id, document.id, page_no)
    except ImportStorageError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type="image/png")


@router.get("/question-assets/{asset_id}", response_class=FileResponse)
async def get_question_asset(
    asset_id: uuid.UUID,
    session: SessionDependency,
    storage: StorageDependency,
) -> FileResponse:
    asset = await session.get(QuestionAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="question asset not found")
    path = storage.resolve(asset.storage_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="question asset file not found")
    return FileResponse(path, media_type=asset.mime_type)
