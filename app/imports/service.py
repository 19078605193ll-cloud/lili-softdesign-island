from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from difflib import SequenceMatcher
from datetime import datetime, timezone
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.imports.ai import (
    CLASSIFICATION_PROMPT_VERSION,
    COMBINED_PROMPT_VERSION,
    REPAIR_PROMPT_VERSION,
    VISION_PROMPT_VERSION,
    QuestionAIClient,
)
from app.imports.extraction import EXTRACTOR_VERSION, extract_page
from app.imports.segmentation import segment_pages
from app.imports.schemas import (
    AssetCropCreate,
    BulkApproveItemsResult,
    ClassificationCandidateRead,
    ClassificationProgress,
    ImportBatchRead,
    ImportBatchMetadataUpdate,
    ImportGroupRead,
    ImportGroupUpdate,
    ImportItemRead,
    ImportItemUpdate,
    ImportOption,
    KnowledgeSelectionInput,
    KnowledgeSelectionRead,
    ParseProgress,
    PublishResult,
    RepairProgress,
    SourceDocumentRead,
    SourceSectionSelection,
    ValidationIssue,
    VisionAnswerPage,
    VisionQuestionPage,
)
from app.imports.storage import LocalImportStorage
from app.models import (
    ExamPaper,
    ExamSubject,
    KnowledgeNode,
    KnowledgeTaxonomyRelease,
    Question,
    QuestionAsset,
    QuestionAssetUsage,
    QuestionClassificationCandidate,
    QuestionClassificationRun,
    QuestionCorrectOption,
    QuestionGroup,
    QuestionImportBatch,
    QuestionImportGroup,
    QuestionImportItem,
    QuestionImportKnowledgeSelection,
    QuestionImportRepairRun,
    QuestionKnowledgeAssignment,
    QuestionOption,
    QuestionSourceDocument,
    QuestionSourcePageExtraction,
)
from app.services import recompute_paper_aggregates

EXPECTED_QUESTION_COUNT = 75
logger = logging.getLogger(__name__)
STRUCTURAL_ISSUE_CODES = {
    "missing_stem", "invalid_options", "empty_option", "invalid_answer_count",
    "answer_not_in_options", "conflicting_answers", "visual_asset_required",
    "source_conflict", "conflicting_content",
}
ASSET_URI_PATTERN = re.compile(r"asset://([0-9a-fA-F-]{36})")


class ImportWorkflowError(ValueError):
    pass


class ImportNotFoundError(LookupError):
    pass


class ImportConflictError(ImportWorkflowError):
    pass


class BulkApprovalError(ImportWorkflowError):
    def __init__(self, items: list[dict[str, Any]]) -> None:
        super().__init__("bulk approval validation failed")
        self.items = items


@dataclass(frozen=True)
class ApprovalSuggestion:
    primary_code: str
    primary_name: str
    related: list[tuple[str, str]]

    def as_selection(self) -> KnowledgeSelectionInput:
        return KnowledgeSelectionInput(
            primary_code=self.primary_code,
            related_codes=[code for code, _name in self.related],
        )


def parse_explicit_question_range(label: str) -> list[int] | None:
    match = re.fullmatch(r"\s*(\d{1,3})\s*[-~–—]\s*(\d{1,3})\s*", label)
    if match is None:
        return None
    start, end = (int(value) for value in match.groups())
    if start < 1 or end < start or end > EXPECTED_QUESTION_COUNT:
        return None
    return list(range(start, end + 1))


def build_batch_validation_summary(
    items: list[QuestionImportItem], *, expected_count: int
) -> dict[str, Any]:
    # Retained historical page pipeline; Markdown has a separate variable-size validator.
    expected_count = EXPECTED_QUESTION_COUNT if expected_count is None else expected_count
    numbers = [item.question_no for item in items]
    expected = set(range(1, expected_count + 1))
    actual = set(numbers)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    duplicate = sorted({number for number in numbers if numbers.count(number) > 1})
    item_issues: list[dict[str, Any]] = []
    repairable = set(missing)
    for item in items:
        for raw_issue in item.validation_issues or []:
            issue = ValidationIssue.model_validate(raw_issue)
            if issue.severity != "error":
                continue
            repairable.add(item.question_no)
            item_issues.append(
                {
                    "question_no": item.question_no,
                    "code": issue.code,
                    "message": issue.message,
                }
            )
    blocking_issue_count = (
        len(missing) + len(extra) + len(duplicate) + len(item_issues)
    )
    return {
        "is_valid": blocking_issue_count == 0,
        "expected_question_count": expected_count,
        "actual_question_count": len(items),
        "missing_question_numbers": missing,
        "extra_question_numbers": extra,
        "duplicate_question_numbers": duplicate,
        "repairable_question_numbers": sorted(repairable),
        "item_issues": item_issues,
        "blocking_issue_count": blocking_issue_count,
    }


def infer_repeated_stem_ranges(
    items_data: dict[int, dict[str, Any]],
) -> list[tuple[int, int, str]]:
    inferred: list[tuple[int, int, str]] = []
    numbers = sorted(items_data)
    index = 0
    while index < len(numbers):
        start_number = numbers[index]
        start_item = items_data[start_number]
        stem = " ".join(str(start_item.get("stem", "")).split())
        if (
            len(stem) < 20
            or parse_explicit_question_range(str(start_item.get("group_label", "")))
        ):
            index += 1
            continue
        end_index = index
        while end_index + 1 < len(numbers):
            current = numbers[end_index]
            following = numbers[end_index + 1]
            following_item = items_data[following]
            following_stem = " ".join(
                str(following_item.get("stem", "")).split()
            )
            if (
                following != current + 1
                or following_stem != stem
                or parse_explicit_question_range(
                    str(following_item.get("group_label", ""))
                )
            ):
                break
            end_index += 1
        if end_index > index:
            inferred.append((start_number, numbers[end_index], stem))
            index = end_index + 1
        else:
            index += 1
    return inferred


async def create_batch_record(
    session: AsyncSession,
    *,
    subject_code: str,
    taxonomy_version: str | None,
    year: int,
    period: str,
    batch_code: str,
    title: str,
    exam_date: Any,
    source_reference: str | None,
) -> QuestionImportBatch:
    subject = await session.scalar(select(ExamSubject).where(ExamSubject.code == subject_code))
    if subject is None:
        raise ImportNotFoundError("exam subject not found")
    release_query = select(KnowledgeTaxonomyRelease).where(
        KnowledgeTaxonomyRelease.subject_id == subject.id
    )
    if taxonomy_version:
        release_query = release_query.where(KnowledgeTaxonomyRelease.version == taxonomy_version)
    else:
        release_query = release_query.order_by(KnowledgeTaxonomyRelease.applied_at.desc()).limit(1)
    release = await session.scalar(release_query)
    if release is None:
        raise ImportNotFoundError("knowledge taxonomy release not found")
    batch = QuestionImportBatch(
        subject_id=subject.id,
        taxonomy_release_id=release.id,
        year=year,
        period=period,
        batch_code=batch_code,
        title=title,
        exam_date=exam_date,
        source_reference=source_reference,
        status="uploaded",
        parser_version=VISION_PROMPT_VERSION,
        expected_question_count=EXPECTED_QUESTION_COUNT,
    )
    session.add(batch)
    await session.flush()
    return batch


async def set_document_sections(
    session: AsyncSession,
    batch_id: uuid.UUID,
    selection: SourceSectionSelection,
) -> QuestionImportBatch:
    batch = await _get_batch(session, batch_id)
    if batch.status in {"published", "ready_to_publish"}:
        raise ImportConflictError("cannot change page sections after approval or publication")
    approved_count = int(
        await session.scalar(
            select(func.count(QuestionImportItem.id)).where(
                QuestionImportItem.batch_id == batch.id,
                QuestionImportItem.status == "approved",
            )
        )
        or 0
    )
    if approved_count:
        raise ImportConflictError("cannot change page sections after item approval has started")
    documents = list(
        (
            await session.scalars(
                select(QuestionSourceDocument).where(
                    QuestionSourceDocument.batch_id == batch.id
                )
            )
        ).all()
    )
    if not documents:
        raise ImportWorkflowError("batch has no source document")
    for document in documents:
        if document.page_count is None or document.status != "rendered":
            raise ImportWorkflowError("source document has not been rendered")
        for role, ranges in (("试题", selection.questions), ("答案解析", selection.answers)):
            for index, value in enumerate(ranges, 1):
                if value.end > document.page_count:
                    raise ImportWorkflowError(
                        f"{role}第 {index} 组结束页 {value.end} 超出文件总页数 {document.page_count}"
                    )
        document.page_selections = selection.as_json()
    document_ids = [document.id for document in documents]
    await session.execute(
        delete(QuestionSourcePageExtraction).where(
            QuestionSourcePageExtraction.document_id.in_(document_ids)
        )
    )
    await session.execute(
        delete(QuestionImportItem).where(QuestionImportItem.batch_id == batch.id)
    )
    await session.execute(
        delete(QuestionImportGroup).where(QuestionImportGroup.batch_id == batch.id)
    )
    batch.status = "sectioned"
    batch.error_summary = None
    await session.flush()
    return batch


def _compact_pages(page_numbers: list[int]) -> list[dict[str, int]]:
    ranges: list[dict[str, int]] = []
    for page_no in sorted(set(page_numbers)):
        if ranges and page_no == ranges[-1]["end"] + 1:
            ranges[-1]["end"] = page_no
        else:
            ranges.append({"start": page_no, "end": page_no})
    return ranges


async def suggest_document_sections(
    session: AsyncSession,
    batch_id: uuid.UUID,
    *,
    ai_client: QuestionAIClient,
    storage: LocalImportStorage,
    retry: bool = False,
) -> ImportBatchRead:
    batch = await _get_batch(session, batch_id)
    document = await session.scalar(
        select(QuestionSourceDocument).where(QuestionSourceDocument.batch_id == batch.id)
    )
    if document is None or document.status != "rendered" or not document.page_count:
        raise ImportWorkflowError("源文件尚未完成页面渲染")
    if document.page_selections:
        return await get_batch_read(session, batch_id)
    if document.section_suggestion and not retry:
        return await get_batch_read(session, batch_id)
    try:
        roles: dict[int, tuple[bool, bool]] = {}
        for start in range(1, document.page_count + 1, 4):
            numbers = list(range(start, min(start + 4, document.page_count + 1)))
            pages = [
                (page_no, storage.page_image_path(batch.id, document.id, page_no))
                for page_no in numbers
            ]
            response = await ai_client.classify_page_roles(pages)
            returned = [row.page_no for row in response.pages]
            for page_no, image_path in pages:
                matching = [row for row in response.pages if row.page_no == page_no]
                if len(matching) != 1 or returned.count(page_no) != 1:
                    single = await ai_client.classify_page_roles([(page_no, image_path)])
                    matching = [row for row in single.pages if row.page_no == page_no]
                if len(matching) != 1:
                    raise ImportWorkflowError(f"视觉模型未能识别第 {page_no} 页")
                row = matching[0]
                roles[page_no] = (row.questions, row.answers)
        initial_roles = roles.copy()
        role_spans = []
        for role_index in (0, 1):
            selected = [number for number, value in initial_roles.items() if value[role_index]]
            if selected:
                role_spans.append((role_index, min(selected), max(selected)))
        for page_no, current in initial_roles.items():
            previous = initial_roles.get(page_no - 1, (False, False))
            next_role = initial_roles.get(page_no + 1, (False, False))
            suspected_cover = (
                page_no == 1
                and current == (True, False)
                and next_role == (True, True)
            )
            suspected_continuation = current == (False, False) and previous != (False, False)
            suspected_gap = any(
                first < page_no < last and not current[role_index]
                for role_index, first, last in role_spans
            )
            if suspected_cover or suspected_continuation or suspected_gap:
                image_path = storage.page_image_path(batch.id, document.id, page_no)
                verification = await ai_client.classify_page_roles([(page_no, image_path)])
                matching = [row for row in verification.pages if row.page_no == page_no]
                if len(matching) == 1:
                    roles[page_no] = (matching[0].questions, matching[0].answers)
        inferred_question_pages: list[int] = []
        question_pages_before_inference = [number for number, values in roles.items() if values[0]]
        overlap_count = sum(roles[number][1] for number in question_pages_before_inference)
        if question_pages_before_inference and overlap_count / len(question_pages_before_inference) >= 0.7:
            first_question, last_question = min(question_pages_before_inference), max(question_pages_before_inference)
            gaps = _compact_pages([
                number for number in range(first_question + 1, last_question)
                if roles[number] == (False, True)
            ])
            for gap in gaps:
                if gap["end"] - gap["start"] + 1 <= 3:
                    for page_no in range(gap["start"], gap["end"] + 1):
                        roles[page_no] = (True, True)
                        inferred_question_pages.append(page_no)
        question_pages = [number for number, (questions, _) in roles.items() if questions]
        answer_pages = [number for number, (_, answers) in roles.items() if answers]
        if not question_pages or not answer_pages:
            raise ImportWorkflowError("未能同时识别试题页和答案页，请手工选择")
        document.section_suggestion = {
            "status": "completed",
            "questions": _compact_pages(question_pages),
            "answers": _compact_pages(answer_pages),
            "mode": "inline" if set(question_pages) & set(answer_pages) else "separate",
            "inferred_question_pages": inferred_question_pages,
            "page_roles": [
                {"page_no": number, "questions": values[0], "answers": values[1]}
                for number, values in sorted(roles.items())
            ],
        }
    except Exception as exc:
        document.section_suggestion = {"status": "failed", "error": str(exc)[:1000]}
    await session.commit()
    return await get_batch_read(session, batch_id)


async def update_batch_metadata(
    session: AsyncSession, batch_id: uuid.UUID, payload: ImportBatchMetadataUpdate
) -> None:
    batch = await _get_batch(session, batch_id)
    if batch.status == "published" or batch.published_paper_id:
        raise ImportConflictError("已发布批次不能修改基本信息")
    batch.year = payload.year
    batch.period = payload.period
    batch.title = payload.title.strip()
    if not batch.title:
        raise ImportWorkflowError("标题不能为空")
    await session.flush()


async def delete_unpublished_batch(session: AsyncSession, batch_id: uuid.UUID) -> None:
    batch = await session.scalar(
        select(QuestionImportBatch).where(QuestionImportBatch.id == batch_id).with_for_update()
    )
    if batch is None:
        raise ImportNotFoundError("import batch not found")
    if batch.status == "published" or batch.published_paper_id:
        raise ImportConflictError("已发布批次不能删除")
    published_item = await session.scalar(
        select(QuestionImportItem.id)
        .where(
            QuestionImportItem.batch_id == batch_id,
            or_(QuestionImportItem.published_question_id.is_not(None), QuestionImportItem.status == "published"),
        )
        .limit(1)
    )
    if published_item:
        raise ImportConflictError("批次包含已发布题目，不能删除")
    asset_ids = select(QuestionAsset.id).join(
        QuestionSourceDocument, QuestionSourceDocument.id == QuestionAsset.document_id
    ).where(QuestionSourceDocument.batch_id == batch_id)
    formal_usage = await session.scalar(
        select(QuestionAssetUsage.id).where(
            QuestionAssetUsage.asset_id.in_(asset_ids),
            or_(QuestionAssetUsage.question_id.is_not(None), QuestionAssetUsage.question_group_id.is_not(None)),
        ).limit(1)
    )
    if formal_usage:
        raise ImportConflictError("批次素材已关联正式题库，不能删除")
    await session.execute(delete(QuestionAssetUsage).where(QuestionAssetUsage.asset_id.in_(asset_ids)))
    await session.execute(delete(QuestionAsset).where(QuestionAsset.id.in_(asset_ids)))
    await session.execute(delete(QuestionImportBatch).where(QuestionImportBatch.id == batch_id))


def _pages_from_document(document: QuestionSourceDocument) -> list[tuple[str, int]]:
    selection = SourceSectionSelection.model_validate(document.page_selections)
    values: list[tuple[str, int]] = []
    for role, ranges in (("questions", selection.questions), ("answers", selection.answers)):
        for page_range in ranges:
            values.extend((role, page) for page in page_range.pages())
    return values


def _parse_page_state(
    documents: list[QuestionSourceDocument],
    extractions: list[QuestionSourcePageExtraction],
) -> tuple[list[tuple[QuestionSourceDocument, int, set[str]]], dict[tuple[uuid.UUID, int, str], QuestionSourcePageExtraction], dict[str, int], list[dict]]:
    """Resolve one effective result per selected document/page/role, across prompt versions."""
    selected: list[tuple[QuestionSourceDocument, int, set[str]]] = []
    for document in documents:
        if not document.page_selections:
            continue
        roles_by_page: dict[int, set[str]] = {}
        for role, page_no in _pages_from_document(document):
            roles_by_page.setdefault(page_no, set()).add(role)
        selected.extend((document, page_no, roles) for page_no, roles in sorted(roles_by_page.items()))
    effective: dict[tuple[uuid.UUID, int, str], QuestionSourcePageExtraction] = {}
    for row in sorted(extractions, key=lambda value: (value.created_at, value.updated_at)):
        key = (row.document_id, row.page_no, row.role)
        if row.status == "completed" or key not in effective or effective[key].status != "completed":
            effective[key] = row
    counts = {"total_pages": len(selected), "completed_pages": 0, "pending_pages": 0,
              "failed_pages": 0, "completed_results": 0, "remaining_results": 0}
    failures: list[dict] = []
    for document, page_no, roles in selected:
        states = {role: effective.get((document.id, page_no, role)) for role in roles}
        completed = sum(row is not None and row.status == "completed" for row in states.values())
        failed = [role for role, row in states.items() if row is not None and row.status == "failed"]
        counts["completed_results"] += completed
        counts["remaining_results"] += len(roles) - completed
        if completed == len(roles):
            counts["completed_pages"] += 1
        elif failed:
            counts["failed_pages"] += 1
            failures.append({"document_id": str(document.id), "page_no": page_no,
                             "roles": failed,
                             "reason": "；".join(f"{role}: {states[role].error_summary or '未知错误'}" for role in failed)})
        else:
            counts["pending_pages"] += 1
    return selected, effective, counts, failures


async def parse_batch(
    session: AsyncSession,
    batch_id: uuid.UUID,
    *,
    ai_client: QuestionAIClient,
    storage: LocalImportStorage,
    limit: int,
    retry_failed: bool = True,
) -> ParseProgress:
    batch = await _get_batch(session, batch_id)
    if batch.status not in {"sectioned", "failed", "parsed", "in_review"}:
        raise ImportConflictError(f"batch in status {batch.status} cannot be parsed")
    documents = list(
        (
            await session.scalars(
                select(QuestionSourceDocument).where(
                    QuestionSourceDocument.batch_id == batch.id
                )
            )
        ).all()
    )
    rows = list((await session.scalars(
        select(QuestionSourcePageExtraction).join(QuestionSourceDocument)
        .where(QuestionSourceDocument.batch_id == batch.id)
    )).all())
    selected, effective, _counts, _failures = _parse_page_state(documents, rows)
    tasks = []
    for document, page_no, roles in selected:
        missing = {role for role in roles if (row := effective.get((document.id, page_no, role))) is None or row.status != "completed"}
        if not missing:
            continue
        if not retry_failed and any((row := effective.get((document.id, page_no, role))) is not None and row.status == "failed" for role in missing):
            continue
        tasks.append((document, page_no, missing))

    processed = failed = 0
    for document, page_no, missing in tasks[:limit]:
        combined = missing == {"questions", "answers"}
        prompt_version = COMBINED_PROMPT_VERSION if combined else VISION_PROMPT_VERSION
        targets: dict[str, QuestionSourcePageExtraction] = {}
        for role in missing:
            extraction = next((row for row in rows if row.document_id == document.id and row.page_no == page_no and row.role == role and row.prompt_version == prompt_version), None)
            if extraction is None:
                extraction = QuestionSourcePageExtraction(
                    document_id=document.id, page_no=page_no, role=role,
                    provider=ai_client.provider_name, model=ai_client.vision_model,
                    prompt_version=prompt_version, status="pending",
                )
                session.add(extraction)
                rows.append(extraction)
            targets[role] = extraction
        try:
            image_path = storage.page_image_path(batch.id, document.id, page_no)
            ocr_payload = None
            ocr_error = None
            if getattr(storage, "use_local_ocr", False) and hasattr(storage, "extraction_cache_path"):
                source_path = storage.resolve(document.storage_path)
                if source_path.is_file() and source_path.suffix.lower() == ".pdf":
                    try:
                        ocr_payload = await extract_page(
                            source_path, image_path,
                            storage.extraction_cache_path(batch.id, document.id, page_no),
                            source_sha256=document.sha256, page_no=page_no,
                        )
                        if not any(not line.get("furniture") for line in ocr_payload["lines"]):
                            ocr_payload = None
                    except Exception as exc:
                        # Keep the existing vision path available when local OCR is unavailable.
                        ocr_error = f"page {page_no} OCR failed: {exc}"[:500]
                        logger.warning(ocr_error)
                        ocr_payload = None
            if ocr_payload is not None:
                payloads = {role: ocr_payload for role in missing}
                for extraction in targets.values():
                    extraction.prompt_version = EXTRACTOR_VERSION
            elif combined:
                parsed, _raw = await ai_client.parse_combined_page(image_path, page_no)
                # Validate both halves before either is persisted as successful.
                question_raw = VisionQuestionPage(groups=parsed.groups).model_dump(mode="json")
                answer_raw = VisionAnswerPage(answers=parsed.answers).model_dump(mode="json")
                payloads = {"questions": question_raw, "answers": answer_raw}
            elif "questions" in missing:
                _, raw = await ai_client.parse_question_page(image_path, page_no)
                payloads = {"questions": raw}
            else:
                _, raw = await ai_client.parse_answer_page(image_path, page_no)
                payloads = {"answers": raw}
            for role, extraction in targets.items():
                if ocr_error and isinstance(payloads[role], dict):
                    payloads[role]["_ocr_fallback_error"] = ocr_error
                extraction.raw_response = payloads[role]
                extraction.status = "completed"
                extraction.error_summary = None
        except Exception as exc:
            for extraction in targets.values():
                extraction.status = "failed"
                extraction.error_summary = str(exc)[:4000]
            failed += 1
        processed += 1
        await session.commit()

    rows = list((await session.scalars(
        select(QuestionSourcePageExtraction).join(QuestionSourceDocument)
        .where(QuestionSourceDocument.batch_id == batch.id)
    )).all())
    _, _, counts, failures = _parse_page_state(documents, rows)
    if counts["remaining_results"] == 0:
        if processed or batch.status != "in_review":
            await _merge_page_extractions(session, batch)
            batch.status = "in_review"
            batch.error_summary = None
    elif counts["failed_pages"]:
        batch.status = "failed"
        batch.error_summary = f"{counts['failed_pages']} 个页面解析失败，请查看页码和原因"
    else:
        batch.status = "sectioned"
        batch.error_summary = None
    await session.commit()
    return ParseProgress(
        batch_id=batch.id,
        processed=processed,
        completed=counts["completed_results"],
        failed=failed,
        remaining=counts["remaining_results"],
        batch_status=batch.status,
        total_pages=counts["total_pages"],
        completed_pages=counts["completed_pages"],
        pending_pages=counts["pending_pages"],
        failed_pages=counts["failed_pages"],
        processed_pages=processed,
        failed_page_details=failures,
    )


async def _merge_page_extractions(
    session: AsyncSession, batch: QuestionImportBatch
) -> None:
    approved_count = int(
        await session.scalar(
            select(func.count(QuestionImportItem.id)).where(
                QuestionImportItem.batch_id == batch.id,
                QuestionImportItem.status.in_(["approved", "published"]),
            )
        )
        or 0
    )
    if approved_count:
        raise ImportConflictError("cannot rebuild parsed items after review has started")

    all_rows = (
        await session.execute(
            select(QuestionSourcePageExtraction, QuestionSourceDocument)
            .join(
                QuestionSourceDocument,
                QuestionSourceDocument.id == QuestionSourcePageExtraction.document_id,
            )
            .where(
                QuestionSourceDocument.batch_id == batch.id,
                QuestionSourcePageExtraction.status == "completed",
            )
            .order_by(
                QuestionSourcePageExtraction.created_at,
                QuestionSourcePageExtraction.updated_at,
            )
        )
    ).all()
    effective_rows: dict[
        tuple[uuid.UUID, int, str],
        tuple[QuestionSourcePageExtraction, QuestionSourceDocument],
    ] = {}
    for extraction, document in all_rows:
        effective_rows[(document.id, extraction.page_no, extraction.role)] = (
            extraction,
            document,
        )
    rows = sorted(
        effective_rows.values(),
        key=lambda value: (value[0].role, value[0].page_no),
    )
    await session.execute(
        delete(QuestionImportItem).where(QuestionImportItem.batch_id == batch.id)
    )
    await session.execute(
        delete(QuestionImportGroup).where(QuestionImportGroup.batch_id == batch.id)
    )

    groups_data: dict[str, dict[str, Any]] = {}
    items_data: dict[int, dict[str, Any]] = {}
    answers_data: dict[int, dict[str, Any]] = {}
    for extraction, document in rows:
        if (extraction.raw_response or {}).get("extractor_version") == EXTRACTOR_VERSION:
            continue
        if extraction.role == "questions":
            parsed = VisionQuestionPage.model_validate(extraction.raw_response or {})
            for group in parsed.groups:
                explicit_range = parse_explicit_question_range(group.source_label)
                shared_range = (
                    explicit_range if group.material_markdown.strip() else None
                )
                group_data = groups_data.setdefault(
                    group.source_label,
                    {
                        "material": "",
                        "question_numbers": set(),
                        "refs": [],
                    },
                )
                group_data["material"] = _merge_markdown(
                    group_data["material"], group.material_markdown
                )
                group_data["refs"].append(
                    {
                        "document_id": str(document.id),
                        "page_no": extraction.page_no,
                        "role": "questions",
                    }
                )
                for parsed_item in group.items:
                    if shared_range and parsed_item.question_no in shared_range:
                        group_data["question_numbers"].add(parsed_item.question_no)
                    item_group_label = (
                        group.source_label
                        if shared_range and parsed_item.question_no in shared_range
                        else f"single-{parsed_item.question_no}"
                    )
                    item = items_data.setdefault(
                        parsed_item.question_no,
                        {
                            "stem": "",
                            "options": {},
                            "group_label": item_group_label,
                            "refs": [],
                        },
                    )
                    if shared_range:
                        item["group_label"] = item_group_label
                    visible_stem = parsed_item.stem_markdown
                    if shared_range is None:
                        visible_stem = _merge_markdown(
                            group.material_markdown, visible_stem
                        )
                    item["stem"] = _merge_markdown(item["stem"], visible_stem)
                    for option in parsed_item.options:
                        item["options"][option.key] = option.content_markdown
                    item["refs"].append(
                        {
                            "document_id": str(document.id),
                            "page_no": extraction.page_no,
                            "role": "questions",
                            "needs_visual_asset": parsed_item.needs_visual_asset,
                            "visual_description": parsed_item.visual_description,
                        }
                    )
        else:
            parsed = VisionAnswerPage.model_validate(extraction.raw_response or {})
            for answer in parsed.answers:
                item = answers_data.setdefault(
                    answer.question_no,
                    {"keys": [], "explanation": "", "refs": [], "conflicts": []},
                )
                if answer.correct_option_keys:
                    if item["keys"] and set(item["keys"]) != set(answer.correct_option_keys):
                        item["conflicts"].append(
                            f"第 {answer.question_no} 题在不同页面出现相互矛盾的答案："
                            f"{'、'.join(item['keys'])} 与 {'、'.join(answer.correct_option_keys)}"
                        )
                    else:
                        item["keys"] = answer.correct_option_keys
                item["explanation"] = _merge_markdown(
                    item["explanation"], answer.explanation_markdown or ""
                )
                item["refs"].append(
                    {
                        "document_id": str(document.id),
                        "page_no": extraction.page_no,
                        "role": "answers",
                    }
                )

    repair_rows = list(
        (
            await session.scalars(
                select(QuestionImportRepairRun)
                .where(
                    QuestionImportRepairRun.batch_id == batch.id,
                    QuestionImportRepairRun.status == "completed",
                )
                .order_by(QuestionImportRepairRun.created_at)
            )
        ).all()
    )
    for repair in repair_rows:
        if repair.role == "questions":
            parsed = VisionQuestionPage.model_validate(repair.raw_response or {})
            for group in parsed.groups:
                explicit_range = parse_explicit_question_range(group.source_label)
                shared_range = (
                    explicit_range if group.material_markdown.strip() else None
                )
                group_data = groups_data.setdefault(
                    group.source_label,
                    {"material": "", "question_numbers": set(), "refs": []},
                )
                group_data["material"] = _merge_markdown(
                    group_data["material"], group.material_markdown
                )
                for page_no in repair.page_numbers:
                    group_data["refs"].append(
                        {
                            "document_id": str(repair.document_id),
                            "page_no": page_no,
                            "role": "questions",
                            "repair_run_id": str(repair.id),
                        }
                    )
                for parsed_item in group.items:
                    if parsed_item.question_no not in repair.question_numbers:
                        continue
                    if shared_range and parsed_item.question_no in shared_range:
                        group_data["question_numbers"].add(parsed_item.question_no)
                    item_group_label = (
                        group.source_label
                        if shared_range and parsed_item.question_no in shared_range
                        else f"single-{parsed_item.question_no}"
                    )
                    item = items_data.setdefault(
                        parsed_item.question_no,
                        {"stem": "", "options": {}, "group_label": item_group_label, "refs": []},
                    )
                    if shared_range:
                        item["group_label"] = item_group_label
                    visible_stem = parsed_item.stem_markdown
                    if shared_range is None:
                        visible_stem = _merge_markdown(group.material_markdown, visible_stem)
                    if visible_stem:
                        if item["stem"] and visible_stem not in item["stem"] and item["stem"] not in visible_stem:
                            item.setdefault("conflicts", []).append("题干修复结果与原始提取不一致，需核对来源页")
                        else:
                            item["stem"] = visible_stem
                    if parsed_item.options:
                        for option in parsed_item.options:
                            old = item["options"].get(option.key)
                            if old and old != option.content_markdown:
                                item.setdefault("conflicts", []).append(
                                    f"选项 {option.key} 修复结果与原始提取不一致"
                                )
                            elif not old:
                                item["options"][option.key] = option.content_markdown
                    item["refs"].extend([
                        {
                            "document_id": str(repair.document_id),
                            "page_no": page_no,
                            "role": "questions",
                            "repair_run_id": str(repair.id),
                            "needs_visual_asset": parsed_item.needs_visual_asset,
                            "visual_description": parsed_item.visual_description,
                        }
                        for page_no in repair.page_numbers
                    ])
        else:
            parsed = VisionAnswerPage.model_validate(repair.raw_response or {})
            for answer in parsed.answers:
                if answer.question_no not in repair.question_numbers:
                    continue
                item = answers_data.setdefault(
                    answer.question_no,
                    {"keys": [], "explanation": "", "refs": [], "conflicts": []},
                )
                if item["keys"] and set(item["keys"]) != set(answer.correct_option_keys):
                    item["conflicts"].append("答案修复结果与原始提取不一致，需核对来源页")
                else:
                    item["keys"] = answer.correct_option_keys
                if answer.explanation_markdown:
                    if item["explanation"] and answer.explanation_markdown not in item["explanation"]:
                        item["conflicts"].append("解析修复结果与原始提取不一致，需核对来源页")
                    else:
                        item["explanation"] = answer.explanation_markdown
                item["refs"].extend([
                    {
                        "document_id": str(repair.document_id),
                        "page_no": page_no,
                        "role": "answers",
                        "repair_run_id": str(repair.id),
                    }
                    for page_no in repair.page_numbers
                ])

    # Position-bearing OCR rows are joined across selected pages before splitting.
    # The same page payload is stored for both roles but must be read only once.
    position_pages: dict[tuple[uuid.UUID, int], dict[str, Any]] = {}
    for extraction, document in rows:
        raw = extraction.raw_response or {}
        if raw.get("extractor_version") == EXTRACTOR_VERSION and "lines" in raw:
            position_pages[(document.id, extraction.page_no)] = raw
    if position_pages:
        for document_id in {identifier for identifier, _ in position_pages}:
            parsed_position = segment_pages(
                [value for (identifier, _), value in position_pages.items() if identifier == document_id],
                str(document_id),
            )
            for number, candidate in parsed_position["questions"].items():
                item = items_data.setdefault(number, {"stem": "", "options": {},
                                                      "group_label": f"single-{number}", "refs": []})
                if candidate["stem"] and not item["stem"]:
                    item["stem"] = candidate["stem"]
                for key, value in candidate["options"].items():
                    item["options"].setdefault(key, value)
                if candidate["group"]:
                    item["group_label"] = candidate["group"]
                for ref in candidate["refs"]:
                    if ref not in item["refs"]:
                        item["refs"].append(ref)
            for number, candidate in parsed_position["answers"].items():
                answer = answers_data.setdefault(number, {"keys": [], "explanation": "",
                                                          "refs": [], "conflicts": []})
                if answer["keys"] and answer["keys"] != candidate["keys"]:
                    answer["conflicts"].append(f"第 {number} 题 OCR 答案与已有答案不一致")
                elif not answer["keys"]:
                    answer["keys"] = candidate["keys"]
                if candidate["explanation"] and not answer["explanation"]:
                    answer["explanation"] = candidate["explanation"]
                for ref in candidate["refs"]:
                    if ref not in answer["refs"]:
                        answer["refs"].append(ref)
            for number, messages in parsed_position["conflicts"].items():
                answer = answers_data.setdefault(number, {"keys": [], "explanation": "",
                                                          "refs": [], "conflicts": []})
                answer["conflicts"].extend(messages)

    for start, end, material in infer_repeated_stem_ranges(items_data):
        label = f"{start}-{end}"
        refs: list[dict[str, Any]] = []
        for question_no in range(start, end + 1):
            item = items_data[question_no]
            item["group_label"] = label
            item["stem"] = ""
            for ref in item["refs"]:
                if ref not in refs:
                    refs.append(ref)
        groups_data[label] = {
            "material": material,
            "question_numbers": set(range(start, end + 1)),
            "refs": refs,
        }

    group_models: dict[str, QuestionImportGroup] = {}
    for order, (label, data) in enumerate(groups_data.items(), 1):
        explicit_range = parse_explicit_question_range(label)
        if (
            explicit_range is None
            or not data["question_numbers"]
            or not data["material"].strip()
        ):
            continue
        model = QuestionImportGroup(
            batch_id=batch.id,
            source_label=label,
            material_markdown=data["material"],
            source_refs=data["refs"],
            sort_order=order,
        )
        session.add(model)
        group_models[label] = model
    await session.flush()

    for question_no in sorted(set(items_data) | set(answers_data)):
        question = items_data.get(
            question_no,
            {"stem": "", "options": {}, "group_label": str(question_no), "refs": []},
        )
        answer = answers_data.get(question_no, {"keys": [], "explanation": "", "refs": [], "conflicts": []})
        group = group_models.get(question["group_label"])
        stem = question["stem"]
        if group is None:
            material = groups_data.get(question["group_label"], {}).get("material", "")
            stem = _merge_markdown(material, stem)
        options = [
            {"key": key, "content_markdown": content}
            for key, content in sorted(question["options"].items())
        ]
        item = QuestionImportItem(
            batch_id=batch.id,
            group_id=group.id if group else None,
            question_no=question_no,
            question_type="single_choice",
            stem_markdown=stem,
            options_payload=options,
            correct_option_keys=answer["keys"],
            explanation_markdown=answer["explanation"] or None,
            score=Decimal("1"),
            source_refs=[*question["refs"], *answer["refs"]],
            status="needs_review",
        )
        item.validation_issues = [
            issue.model_dump() for issue in validate_import_item(item, group)
        ]
        item.validation_issues.extend(
            ValidationIssue(code="conflicting_answers", severity="error", message=reason).model_dump()
            for reason in answer["conflicts"]
        )
        item.validation_issues.extend(
            ValidationIssue(code="conflicting_content", severity="error", message=reason).model_dump()
            for reason in question.get("conflicts", [])
        )
        if any(issue["severity"] == "error" for issue in item.validation_issues):
            item.status = "blocked"
        session.add(item)
    await session.flush()
    items = list(
        (
            await session.scalars(
                select(QuestionImportItem)
                .where(QuestionImportItem.batch_id == batch.id)
                .order_by(QuestionImportItem.question_no)
            )
        ).all()
    )
    batch.validation_summary = build_batch_validation_summary(
        items, expected_count=batch.expected_question_count
    )
    batch.parser_version = (
        COMBINED_PROMPT_VERSION
        if any(extraction.prompt_version == COMBINED_PROMPT_VERSION for extraction, _ in rows)
        else VISION_PROMPT_VERSION
    )
    batch.status = "parsed"
    await session.flush()


def _repair_pages_for_question(
    question_no: int,
    *,
    role: str,
    refs_by_question: dict[int, dict[str, set[int]]],
    selected_pages: list[int],
) -> list[int]:
    previous_pages = refs_by_question.get(question_no - 1, {}).get(role, set())
    direct_pages = refs_by_question.get(question_no, {}).get(role, set())
    following_pages = refs_by_question.get(question_no + 1, {}).get(role, set())
    pages: set[int] = set()
    pages.update(previous_pages)
    pages.update(direct_pages)
    pages.update(following_pages)
    ordered = sorted(page for page in pages if page in selected_pages)
    if len(ordered) > 2:
        direct = sorted(direct_pages)
        if direct:
            anchor = direct[0]
            following = anchor + 1
            previous = anchor - 1
            ordered = [anchor]
            if following in selected_pages:
                ordered.append(following)
            elif previous in selected_pages:
                ordered.insert(0, previous)
        else:
            ordered = ordered[:2]
    if len(ordered) == 1:
        anchor = ordered[0]
        previous = anchor - 1
        following = anchor + 1
        if not direct_pages and previous_pages and not following_pages and following in selected_pages:
            ordered.append(following)
        elif not direct_pages and following_pages and not previous_pages and previous in selected_pages:
            ordered.insert(0, previous)
        elif previous in selected_pages:
            ordered.insert(0, previous)
        elif following in selected_pages:
            ordered.append(following)
    if not ordered and selected_pages:
        proportional = round(
            (question_no - 1)
            * (len(selected_pages) - 1)
            / max(EXPECTED_QUESTION_COUNT - 1, 1)
        )
        anchor = selected_pages[max(0, min(proportional, len(selected_pages) - 1))]
        ordered = [anchor]
    return ordered


def _build_repair_windows(
    items: list[QuestionImportItem],
    summary: dict[str, Any],
    document: QuestionSourceDocument,
) -> list[dict[str, Any]]:
    refs_by_question: dict[int, dict[str, set[int]]] = {}
    item_by_number = {item.question_no: item for item in items}
    for item in items:
        roles = refs_by_question.setdefault(
            item.question_no, {"questions": set(), "answers": set()}
        )
        for ref in item.source_refs or []:
            role = ref.get("role")
            page_no = ref.get("page_no")
            if role in roles and isinstance(page_no, int):
                roles[role].add(page_no)

    issue_codes: dict[int, set[str]] = {}
    for issue in summary.get("item_issues", []):
        issue_codes.setdefault(int(issue["question_no"]), set()).add(issue["code"])
    missing = set(summary.get("missing_question_numbers", []))
    question_targets = set(missing)
    answer_targets = set(missing)
    for number, codes in issue_codes.items():
        if codes & {"missing_stem", "invalid_options", "answer_not_in_options"}:
            question_targets.add(number)
        if codes & {"invalid_answer_count"}:
            answer_targets.add(number)

    selection = SourceSectionSelection.model_validate(document.page_selections)
    selected_by_role = {
        "questions": [page for value in selection.questions for page in value.pages()],
        "answers": [page for value in selection.answers for page in value.pages()],
    }
    windows: dict[tuple[str, tuple[int, ...]], set[int]] = {}
    for role, targets in (("questions", question_targets), ("answers", answer_targets)):
        for number in sorted(targets):
            pages = _repair_pages_for_question(
                number,
                role=role,
                refs_by_question=refs_by_question,
                selected_pages=selected_by_role[role],
            )
            if not pages:
                continue
            required = {number}
            for nearby in (number - 1, number + 1):
                if 1 <= nearby <= summary.get("expected_question_count", EXPECTED_QUESTION_COUNT):
                    nearby_pages = refs_by_question.get(nearby, {}).get(role, set())
                    if nearby_pages & set(pages):
                        required.add(nearby)
            windows.setdefault((role, tuple(pages)), set()).update(required)

    result: list[dict[str, Any]] = []
    for (role, pages), numbers in sorted(
        windows.items(), key=lambda value: (value[0][0], value[0][1])
    ):
        previous_items = []
        for number in sorted(numbers):
            item = item_by_number.get(number)
            if item is None:
                previous_items.append({"question_no": number, "missing": True})
                continue
            previous_items.append(
                {
                    "question_no": number,
                    "stem": item.stem_markdown,
                    "options": item.options_payload,
                    "answer": item.correct_option_keys,
                    "explanation": item.explanation_markdown,
                    "source_refs": item.source_refs,
                    "validation_issues": item.validation_issues,
                }
            )
        result.append(
            {
                "role": role,
                "pages": list(pages),
                "question_numbers": sorted(numbers),
                "previous_items": previous_items,
            }
        )
    return result


def _repair_position_evidence(
    storage: LocalImportStorage, batch_id: uuid.UUID, document_id: uuid.UUID,
    page_numbers: list[int],
) -> dict[str, Any] | None:
    if not hasattr(storage, "extraction_cache_path"):
        return None
    pages = []
    for page_no in page_numbers:
        path = storage.extraction_cache_path(batch_id, document_id, page_no)
        if path.is_file():
            try:
                pages.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
    return segment_pages(pages, str(document_id)) if pages else None


def _repair_cropped_images(
    storage: LocalImportStorage, batch_id: uuid.UUID, run_id: uuid.UUID,
    image_paths: list[Path], page_numbers: list[int], role: str,
    question_numbers: list[int], position: dict[str, Any] | None,
) -> list[Path]:
    if position is None or not hasattr(storage, "resolve"):
        return image_paths
    from PIL import Image

    output = []
    section = "questions" if role == "questions" else "answers"
    for page_no, image_path in zip(page_numbers, image_paths, strict=True):
        refs = [ref for number in question_numbers
                for ref in position[section].get(number, {}).get("refs", [])
                if ref.get("page_no") == page_no and ref.get("bbox")]
        if not refs:
            output.append(image_path)
            continue
        box = [min(ref["bbox"]["x0"] for ref in refs),
               min(ref["bbox"]["y0"] for ref in refs),
               max(ref["bbox"]["x1"] for ref in refs),
               max(ref["bbox"]["y1"] for ref in refs)]
        box = [max(0.0, box[0] - 0.04), max(0.0, box[1] - 0.04),
               min(1.0, box[2] + 0.04), min(1.0, box[3] + 0.04)]
        crop_path = storage.resolve(f"{batch_id}/repair-crops/{run_id}/page-{page_no:04d}.png")
        crop_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(image_path) as image:
            width, height = image.size
            image.crop((int(box[0] * width), int(box[1] * height),
                        int(box[2] * width), int(box[3] * height))).save(crop_path)
        output.append(crop_path)
    return output


def _verify_repair_against_position(
    role: str, parsed: VisionQuestionPage | VisionAnswerPage,
    numbers: list[int], position: dict[str, Any] | None,
) -> None:
    if position is None:
        raise ImportWorkflowError("local position evidence unavailable; repair requires manual review")
    if role == "answers":
        by_number = {answer.question_no: answer for answer in parsed.answers}
        for number in numbers:
            evidence = position["answers"].get(number)
            if evidence is None or number not in by_number or by_number[number].correct_option_keys != evidence["keys"]:
                raise ImportWorkflowError(f"question {number} answer differs from positioned source evidence")
            source_text = re.sub(r"\W+", "", evidence.get("explanation") or "")
            model_text = re.sub(r"\W+", "", by_number[number].explanation_markdown or "")
            if source_text:
                source_pairs = {source_text[index:index + 2] for index in range(len(source_text) - 1)}
                model_pairs = {model_text[index:index + 2] for index in range(len(model_text) - 1)}
                if not model_pairs or len(source_pairs & model_pairs) / len(model_pairs) < 0.15:
                    raise ImportWorkflowError(f"question {number} explanation differs from positioned source evidence")
    else:
        by_number = {item.question_no: item for group in parsed.groups for item in group.items}
        for number in numbers:
            evidence = position["questions"].get(number)
            item = by_number.get(number)
            if evidence is None or item is None:
                raise ImportWorkflowError(f"question {number} has no positioned source evidence")
            options = {option.key: option.content_markdown for option in item.options}
            if set(options) != set(evidence["options"]):
                raise ImportWorkflowError(f"question {number} option labels differ from source evidence")
            for key, text in options.items():
                if SequenceMatcher(None, text.replace(" ", ""),
                                   evidence["options"][key].replace(" ", "")).ratio() < 0.55:
                    raise ImportWorkflowError(f"question {number} option {key} differs from source evidence")


async def repair_batch(
    session: AsyncSession,
    batch_id: uuid.UUID,
    *,
    ai_client: QuestionAIClient,
    storage: LocalImportStorage,
    limit: int,
) -> RepairProgress:
    batch = await _get_batch(session, batch_id)
    if batch.status not in {"parsed", "in_review", "failed"}:
        raise ImportConflictError(f"batch in status {batch.status} cannot be repaired")
    approved_count = int(
        await session.scalar(
            select(func.count(QuestionImportItem.id)).where(
                QuestionImportItem.batch_id == batch.id,
                QuestionImportItem.status.in_(["approved", "published"]),
            )
        )
        or 0
    )
    if approved_count:
        raise ImportConflictError("cannot automatically repair after review has started")
    document = await session.scalar(
        select(QuestionSourceDocument)
        .where(QuestionSourceDocument.batch_id == batch.id)
        .order_by(QuestionSourceDocument.created_at)
        .limit(1)
    )
    if document is None:
        raise ImportWorkflowError("batch has no source document")
    items = list(
        (
            await session.scalars(
                select(QuestionImportItem)
                .where(QuestionImportItem.batch_id == batch.id)
                .order_by(QuestionImportItem.question_no)
            )
        ).all()
    )
    summary = build_batch_validation_summary(
        items, expected_count=batch.expected_question_count
    )
    batch.validation_summary = summary
    if summary["is_valid"]:
        return RepairProgress(
            batch_id=batch.id,
            processed_runs=0,
            completed_runs=0,
            failed_runs=0,
            remaining_question_numbers=[],
            batch_status=batch.status,
            validation_summary=summary,
        )

    windows = _build_repair_windows(items, summary, document)
    processed = completed = failed = 0
    for window in windows:
        if processed >= limit:
            break
        previous_runs = list(
            (
                await session.scalars(
                    select(QuestionImportRepairRun).where(
                        QuestionImportRepairRun.batch_id == batch.id,
                        QuestionImportRepairRun.role == window["role"],
                        QuestionImportRepairRun.page_numbers == window["pages"],
                    )
                )
            ).all()
        )
        attempt = len(previous_runs) + 1
        if attempt > 2:
            continue
        run = QuestionImportRepairRun(
            batch_id=batch.id,
            document_id=document.id,
            role=window["role"],
            page_numbers=window["pages"],
            question_numbers=window["question_numbers"],
            attempt=attempt,
            provider=ai_client.provider_name,
            model=ai_client.vision_model,
            prompt_version=REPAIR_PROMPT_VERSION,
            status="pending",
        )
        session.add(run)
        await session.flush()
        image_paths = [
            storage.page_image_path(batch.id, document.id, page_no)
            for page_no in window["pages"]
        ]
        raw = None
        try:
            position = _repair_position_evidence(
                storage, batch.id, document.id, window["pages"]
            ) if isinstance(storage, LocalImportStorage) else None
            if isinstance(storage, LocalImportStorage):
                image_paths = _repair_cropped_images(
                    storage, batch.id, run.id, image_paths, window["pages"],
                    window["role"], window["question_numbers"], position,
                )
            if window["role"] == "questions":
                parsed, raw = await ai_client.repair_question_pages(
                    image_paths,
                    window["pages"],
                    window["question_numbers"],
                    window["previous_items"],
                )
                returned = {
                    item.question_no
                    for group in parsed.groups
                    for item in group.items
                }
            else:
                parsed, raw = await ai_client.repair_answer_pages(
                    image_paths,
                    window["pages"],
                    window["question_numbers"],
                    window["previous_items"],
                )
                returned = {answer.question_no for answer in parsed.answers}
            missing = set(window["question_numbers"]) - returned
            if missing:
                raise ImportWorkflowError(
                    f"repair response omitted required questions: {sorted(missing)}"
                )
            if isinstance(storage, LocalImportStorage):
                _verify_repair_against_position(
                    window["role"], parsed, window["question_numbers"], position
                )
            run.raw_response = raw
            run.status = "completed"
            completed += 1
        except Exception as exc:
            run.status = "failed"
            run.error_summary = str(exc)[:4000]
            if raw is not None:
                run.raw_response = raw
            failed += 1
        processed += 1
        await session.commit()

    if completed:
        await _merge_page_extractions(session, batch)
        items = list(
            (
                await session.scalars(
                    select(QuestionImportItem)
                    .where(QuestionImportItem.batch_id == batch.id)
                    .order_by(QuestionImportItem.question_no)
                )
            ).all()
        )
        summary = build_batch_validation_summary(
            items, expected_count=batch.expected_question_count
        )
        batch.validation_summary = summary
    batch.status = "in_review"
    await session.flush()
    return RepairProgress(
        batch_id=batch.id,
        processed_runs=processed,
        completed_runs=completed,
        failed_runs=failed,
        remaining_question_numbers=summary["repairable_question_numbers"],
        batch_status=batch.status,
        validation_summary=summary,
    )


def _merge_markdown(existing: str, value: str) -> str:
    existing = existing.strip()
    value = value.strip()
    if not value or value in existing:
        return existing
    if not existing or existing in value:
        return value
    return f"{existing}\n\n{value}"


def validate_import_item(
    item: QuestionImportItem, group: QuestionImportGroup | None = None
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not item.stem_markdown.strip() and not (group and group.material_markdown.strip()):
        issues.append(
            ValidationIssue(code="missing_stem", severity="error", message="题干为空")
        )
    material_text = (group.material_markdown if group else '') + '\n' + item.stem_markdown
    if re.search(r'(?m)^\s*(?:参考|正确)?答案\s*[:：]|^\s*(?:答案)?解析\s*[:：]', material_text):
        issues.append(ValidationIssue(code='answer_in_stem', severity='error', message='题干含答案或解析，请重新提取结构后核对'))
    options: list[dict[str, Any]] = item.options_payload or []
    option_keys = [str(value.get("key", "")).upper() for value in options]
    if option_keys != ["A", "B", "C", "D"]:
        issues.append(
            ValidationIssue(
                code="invalid_options",
                severity="error",
                message="上午单选题必须按 A、B、C、D 提供四个选项",
            )
        )
    if any(not str(value.get("content_markdown", "")).strip() for value in options):
        issues.append(
            ValidationIssue(code="empty_option", severity="error", message="选项内容不能为空")
        )
    if len(item.correct_option_keys) != 1:
        issues.append(
            ValidationIssue(
                code="invalid_answer_count",
                severity="error",
                message="上午单选题必须且只能有一个正确答案",
            )
        )
    elif item.correct_option_keys[0] not in option_keys:
        issues.append(
            ValidationIssue(
                code="answer_not_in_options",
                severity="error",
                message="正确答案没有对应选项",
            )
        )
    material = f"{group.material_markdown if group else ''}\n{item.stem_markdown}"
    needs_asset = any(bool(ref.get("needs_visual_asset")) for ref in item.source_refs)
    if needs_asset and "asset://" not in material:
        issues.append(
            ValidationIssue(
                code="visual_asset_required",
                severity="error",
                message="原题依赖图表或代码，请裁剪素材并插入题干或共享材料",
            )
        )
    if not (item.explanation_markdown or "").strip():
        issues.append(
            ValidationIssue(
                code="missing_explanation",
                severity="warning",
                message="该题没有解析，可审核后继续发布",
            )
        )
    return issues


def classification_fingerprint(
    item: QuestionImportItem, group: QuestionImportGroup | None = None, *, version: int = 2,
) -> str:
    """v1 is the historical content hash; v2 also binds numbering and shared solution."""
    if version not in (1, 2):
        raise ValueError("Unsupported classification fingerprint version")
    payload = {"stem": item.stem_markdown, "options": item.options_payload,
               "answer": item.correct_option_keys, "explanation": item.explanation_markdown,
               "shared_material": group.material_markdown if group else None}
    if version == 2:
        payload.update(shared_explanation=getattr(group, "explanation_markdown", None),
                       question_no=item.question_no)
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def classification_run_matches(run, item, group, batch, release) -> bool:
    """One compatibility gate for display, approval provenance and classification reuse.

    A missing hash is never evidence of unchanged content. Historical hashes may
    be reused only with no new shared explanation and original question-number
    evidence in the stored provider response. No historical records are rewritten.
    """
    if (run.import_item_id != item.id or release is None
            or run.taxonomy_release_id != batch.taxonomy_release_id
            or run.catalog_checksum != release.checksum_sha256):
        return False
    if run.input_fingerprint == classification_fingerprint(item, group):
        return True
    if (getattr(group, "explanation_markdown", None)
            or run.input_fingerprint != classification_fingerprint(item, group, version=1)):
        return False
    raw = run.raw_response
    results = raw.get('results') if isinstance(raw, dict) else None
    return isinstance(results, list) and any(
        isinstance(result, dict) and result.get('question_no') == item.question_no
        for result in results)


async def matching_classification_run_id(session, item, group, batch):
    release = await session.get(KnowledgeTaxonomyRelease, batch.taxonomy_release_id)
    runs = await session.scalars(select(QuestionClassificationRun).where(
        QuestionClassificationRun.import_item_id == item.id,
        QuestionClassificationRun.status == 'completed').order_by(
            QuestionClassificationRun.created_at.desc(), QuestionClassificationRun.id.desc()))
    return next((run.id for run in runs if classification_run_matches(run, item, group, batch, release)), None)


async def classify_batch(
    session: AsyncSession,
    batch_id: uuid.UUID,
    *,
    ai_client: QuestionAIClient,
    limit: int,
    storage: LocalImportStorage | None = None,
    item_ids: set[uuid.UUID] | None = None,
    force: bool = False,
) -> ClassificationProgress:
    batch = await session.scalar(select(QuestionImportBatch).where(QuestionImportBatch.id == batch_id).with_for_update())
    if batch.status not in {"parsed", "in_review", "ready_to_publish"}:
        raise ImportConflictError(f"batch in status {batch.status} cannot be classified")
    release = await session.get(KnowledgeTaxonomyRelease, batch.taxonomy_release_id)
    if release is None:
        raise ImportWorkflowError("taxonomy release no longer exists")
    all_nodes = list(
        (
            await session.scalars(
                select(KnowledgeNode).where(KnowledgeNode.subject_id == batch.subject_id)
            )
        ).all()
    )
    by_id = {node.id: node for node in all_nodes}

    def path_for(node: KnowledgeNode) -> str:
        names = [node.name]
        cursor = node.parent_id
        while cursor and cursor in by_id:
            parent = by_id[cursor]
            names.append(parent.name)
            cursor = parent.parent_id
        return " / ".join(reversed(names))

    topics = [
        node
        for node in all_nodes
        if node.node_type == "topic" and node.status == "active"
    ]
    topics.sort(key=lambda node: node.code)
    catalog = [
        {
            "code": node.code,
            "path": path_for(node),
            "aliases": node.aliases,
            "keywords": node.keywords,
            "guidance": node.classification_guidance,
        }
        for node in topics
    ]
    all_items = list((await session.scalars(
        select(QuestionImportItem).where(QuestionImportItem.batch_id == batch.id)
        .order_by(QuestionImportItem.question_no)
    )).all())
    if item_ids is not None:
        all_items = [item for item in all_items if item.id in item_ids]
    all_group_ids = {item.group_id for item in all_items if item.group_id}
    all_groups = {group.id: group for group in (await session.scalars(
        select(QuestionImportGroup).where(QuestionImportGroup.id.in_(all_group_ids))
    )).all()} if all_group_ids else {}
    previous = list((await session.scalars(
        select(QuestionClassificationRun).where(
            QuestionClassificationRun.import_item_id.in_([item.id for item in all_items]),
            QuestionClassificationRun.taxonomy_release_id == batch.taxonomy_release_id,
            QuestionClassificationRun.prompt_version == CLASSIFICATION_PROMPT_VERSION,
            QuestionClassificationRun.catalog_checksum == release.checksum_sha256,
            QuestionClassificationRun.status == "completed",
        )
    )).all()) if all_items else []
    fingerprints = {item.id: classification_fingerprint(item, all_groups.get(item.group_id))
                    for item in all_items}
    items_by_id = {item.id: item for item in all_items}
    completed_ids = {run.import_item_id for run in previous
                     if classification_run_matches(run, items_by_id[run.import_item_id],
                        all_groups.get(items_by_id[run.import_item_id].group_id), batch, release)}
    eligible = [item for item in all_items
                if item.status in {"needs_review", "approved"}
                and not any(issue.severity == "error" for issue in
                            validate_import_item(item, all_groups.get(item.group_id)))
                and not any(issue.get("severity") == "error" for issue in item.validation_issues)]
    items = [item for item in eligible if force or item.id not in completed_ids][:limit]
    if not items:
        completed = len(completed_ids)
        return ClassificationProgress(
            batch_id=batch.id,
            processed=0,
            completed_items=completed,
            failed_items=0,
            remaining_items=max(len(eligible) - completed, 0),
            batch_status=batch.status,
            eligible_items=len(eligible), blocked_items=len(all_items) - len(eligible),
        )

    runs: dict[uuid.UUID, QuestionClassificationRun] = {}
    for item in items:
        run = QuestionClassificationRun(
            import_item_id=item.id,
            taxonomy_release_id=batch.taxonomy_release_id,
            provider=ai_client.provider_name,
            model=ai_client.classification_model,
            prompt_version=CLASSIFICATION_PROMPT_VERSION,
            catalog_checksum=release.checksum_sha256,
            input_fingerprint=fingerprints[item.id],
            status="pending",
        )
        session.add(run)
        runs[item.id] = run
    await session.flush()

    groups = all_groups
    payload: list[dict[str, Any]] = []
    for item in items:
        value: dict[str, Any] = {
            "question_no": item.question_no,
            "shared_material": groups[item.group_id].material_markdown if item.group_id else None,
            "stem": item.stem_markdown,
            "options": item.options_payload,
            "answer": item.correct_option_keys,
            "explanation": item.explanation_markdown,
            "shared_explanation": getattr(groups.get(item.group_id), "explanation_markdown", None),
            "locator": {"question_no": item.question_no},
        }
        if storage is not None:
            owner_conditions = [QuestionAssetUsage.import_item_id == item.id]
            if item.group_id is not None:
                owner_conditions.append(QuestionAssetUsage.import_group_id == item.group_id)
            assets = list(
                (
                    await session.scalars(
                        select(QuestionAsset)
                        .join(
                            QuestionAssetUsage,
                            QuestionAssetUsage.asset_id == QuestionAsset.id,
                        )
                        .where(or_(*owner_conditions))
                        .order_by(QuestionAssetUsage.sort_order, QuestionAsset.created_at)
                    )
                ).all()
            )
            image_paths = [storage.resolve(asset.storage_path) for asset in assets]
            missing = [str(path) for path in image_paths if not path.is_file()]
            if missing:
                raise ImportWorkflowError(
                    f"question {item.question_no} references missing visual assets"
                )
            value["asset_image_paths"] = [str(path) for path in image_paths]
        payload.append(value)
    failed_items = sum(run.status == 'failed' for run in runs.values())
    try:
        if not payload:
            raise ImportWorkflowError('没有可供分类的完整图片与文本')
        parsed, raw = await ai_client.classify_questions(payload, catalog)
        result_by_number = {value.question_no: value for value in parsed.results}
        node_by_code = {node.code: node for node in topics}
        for item in items:
            run = runs[item.id]
            if run.status == "failed":
                continue
            run.raw_response = raw
            result = result_by_number.get(item.question_no)
            if result is None:
                run.status = "failed"
                run.error_summary = "AI response omitted this question"
                failed_items += 1
                continue
            seen: set[tuple[str, str]] = set()
            for role, candidates in (
                ("primary", result.primary_candidates),
                ("related", result.related_candidates),
            ):
                for rank, candidate in enumerate(candidates, 1):
                    node = node_by_code.get(candidate.code)
                    if node is None:
                        continue
                    identity = (role, candidate.code)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    session.add(
                        QuestionClassificationCandidate(
                            run_id=run.id,
                            knowledge_node_id=node.id,
                            role=role,
                            rank=rank,
                            confidence=candidate.confidence,
                            rationale=candidate.rationale,
                        )
                    )
            if not any(role == "primary" for role, _ in seen):
                run.status = "failed"
                run.error_summary = "AI response contained no valid primary candidate"
                failed_items += 1
            else:
                run.status = "completed"
    except Exception as exc:
        for run in runs.values():
            run.status = "failed"
            run.error_summary = str(exc)[:4000]
        failed_items = len(items)
    batch.status = "in_review"
    await session.flush()

    completed = len(completed_ids | {key for key, run in runs.items() if run.status == "completed"})
    return ClassificationProgress(
        batch_id=batch.id,
        processed=len(items),
        completed_items=completed,
        failed_items=failed_items,
        remaining_items=max(len(eligible) - completed, 0),
        batch_status=batch.status,
        eligible_items=len(eligible), blocked_items=len(all_items) - len(eligible),
        results=[dict(item_id=str(item.id), question_no=item.question_no, status=runs[item.id].status, error=runs[item.id].error_summary) for item in items],
    )


async def update_import_item(
    session: AsyncSession, item_id: uuid.UUID, update: ImportItemUpdate
) -> QuestionImportItem:
    item = await _get_item(session, item_id)
    if (await _get_batch(session, item.batch_id)).parser_version.startswith("markdown-"):
        raise ImportConflictError("Markdown 题目请使用整题编辑入口")
    if item.status == "published":
        raise ImportConflictError("published import items cannot be edited")
    fields = update.model_fields_set
    if "stem_markdown" in fields and update.stem_markdown is not None:
        item.stem_markdown = update.stem_markdown
    if "options" in fields and update.options is not None:
        item.options_payload = [value.model_dump() for value in update.options]
    if "correct_option_keys" in fields and update.correct_option_keys is not None:
        item.correct_option_keys = update.correct_option_keys
    if "explanation_markdown" in fields:
        item.explanation_markdown = update.explanation_markdown
    if "score" in fields and update.score is not None:
        item.score = update.score
    if "review_note" in fields:
        item.review_note = update.review_note
    if item.status == "approved":
        await session.execute(
            delete(QuestionImportKnowledgeSelection).where(
                QuestionImportKnowledgeSelection.import_item_id == item.id
            )
        )
    await session.execute(delete(QuestionClassificationRun).where(
        QuestionClassificationRun.import_item_id == item.id
    ))
    group = await session.get(QuestionImportGroup, item.group_id) if item.group_id else None
    issues = validate_import_item(item, group)
    item.validation_issues = [issue.model_dump() for issue in issues]
    item.status = "blocked" if any(issue.severity == "error" for issue in issues) else "needs_review"
    await _refresh_batch_status(session, item.batch_id)
    await session.flush()
    return item


async def get_import_group(
    session: AsyncSession, group_id: uuid.UUID
) -> ImportGroupRead:
    group = await session.get(QuestionImportGroup, group_id)
    if group is None:
        raise ImportNotFoundError("import group not found")
    return ImportGroupRead(
        id=group.id,
        batch_id=group.batch_id,
        source_label=group.source_label,
        material_markdown=group.material_markdown,
        source_refs=group.source_refs,
        sort_order=group.sort_order,
    )


async def update_import_group(
    session: AsyncSession, group_id: uuid.UUID, update: ImportGroupUpdate
) -> ImportGroupRead:
    group = await session.get(QuestionImportGroup, group_id)
    if group is None:
        raise ImportNotFoundError("import group not found")
    if (await _get_batch(session, group.batch_id)).parser_version.startswith("markdown-"):
        raise ImportConflictError("Markdown 题目请使用整题编辑入口")
    items = list(
        (
            await session.scalars(
                select(QuestionImportItem).where(QuestionImportItem.group_id == group.id)
            )
        ).all()
    )
    if any(item.status == "published" for item in items):
        raise ImportConflictError("published question groups cannot be edited")
    group.material_markdown = update.material_markdown
    for item in items:
        if item.status == "approved":
            await session.execute(
                delete(QuestionImportKnowledgeSelection).where(
                    QuestionImportKnowledgeSelection.import_item_id == item.id
                )
            )
        await session.execute(delete(QuestionClassificationRun).where(
            QuestionClassificationRun.import_item_id == item.id
        ))
        issues = validate_import_item(item, group)
        item.validation_issues = [issue.model_dump() for issue in issues]
        item.status = (
            "blocked" if any(issue.severity == "error" for issue in issues) else "needs_review"
        )
    await _refresh_batch_status(session, group.batch_id)
    await session.flush()
    return await get_import_group(session, group.id)


async def _load_approval_suggestions(
    session: AsyncSession,
    batch: QuestionImportBatch,
    items: list[QuestionImportItem],
) -> dict[uuid.UUID, ApprovalSuggestion]:
    if not items:
        return {}
    release = await session.get(KnowledgeTaxonomyRelease, batch.taxonomy_release_id)
    item_ids = [item.id for item in items]
    group_ids = {item.group_id for item in items if item.group_id}
    groups = {group.id: group for group in (await session.scalars(
        select(QuestionImportGroup).where(QuestionImportGroup.id.in_(group_ids))
    )).all()} if group_ids else {}
    items_by_id = {item.id: item for item in items}
    runs = await session.scalars(select(QuestionClassificationRun).where(
        QuestionClassificationRun.import_item_id.in_(item_ids),
        QuestionClassificationRun.status == 'completed').order_by(
            QuestionClassificationRun.created_at.desc(), QuestionClassificationRun.id.desc()))
    latest_run_by_item = {}
    for run in runs:
        item = items_by_id[run.import_item_id]
        if classification_run_matches(run, item, groups.get(item.group_id), batch, release):
            latest_run_by_item.setdefault(item.id, run.id)
    if not latest_run_by_item:
        return {}

    item_by_run = {run_id: item_id for item_id, run_id in latest_run_by_item.items()}
    candidate_rows = (
        await session.execute(
            select(QuestionClassificationCandidate, KnowledgeNode)
            .join(
                KnowledgeNode,
                KnowledgeNode.id == QuestionClassificationCandidate.knowledge_node_id,
            )
            .where(
                QuestionClassificationCandidate.run_id.in_(item_by_run),
                KnowledgeNode.subject_id == batch.subject_id,
                KnowledgeNode.node_type == "topic",
                KnowledgeNode.status == "active",
            )
            .order_by(
                QuestionClassificationCandidate.run_id,
                QuestionClassificationCandidate.role,
                QuestionClassificationCandidate.rank,
            )
        )
    ).all()
    rows_by_item: dict[uuid.UUID, list[tuple[QuestionClassificationCandidate, KnowledgeNode]]] = {}
    for candidate, node in candidate_rows:
        rows_by_item.setdefault(item_by_run[candidate.run_id], []).append((candidate, node))

    suggestions: dict[uuid.UUID, ApprovalSuggestion] = {}
    for item in items:
        rows = rows_by_item.get(item.id, [])
        primary_rows = sorted(
            ((candidate, node) for candidate, node in rows if candidate.role == "primary"),
            key=lambda value: value[0].rank,
        )
        if not primary_rows:
            continue
        _primary_candidate, primary_node = primary_rows[0]
        related: list[tuple[str, str]] = []
        seen_codes = {primary_node.code}
        for _candidate, node in sorted(
            ((candidate, node) for candidate, node in rows if candidate.role == "related"),
            key=lambda value: value[0].rank,
        ):
            if node.code in seen_codes:
                continue
            seen_codes.add(node.code)
            related.append((node.code, node.name))
            if len(related) == 3:
                break
        suggestions[item.id] = ApprovalSuggestion(
            primary_code=primary_node.code,
            primary_name=primary_node.name,
            related=related,
        )
    return suggestions


async def get_bulk_approval_previews(
    session: AsyncSession,
    batch: QuestionImportBatch,
    items: list[QuestionImportItem],
) -> dict[uuid.UUID, dict[str, Any]]:
    suggestions = await _load_approval_suggestions(session, batch, items)
    group_ids = {item.group_id for item in items if item.group_id is not None}
    groups = list(
        (
            await session.scalars(
                select(QuestionImportGroup).where(QuestionImportGroup.id.in_(group_ids))
            )
        ).all()
    ) if group_ids else []
    groups_by_id = {group.id: group for group in groups}
    previews: dict[uuid.UUID, dict[str, Any]] = {}
    for item in items:
        reason: str | None = None
        if item.status != "needs_review":
            reason = {
                "approved": "已批准",
                "published": "已发布",
                "rejected": "已驳回，请修改后重新审核",
                "blocked": "存在阻断问题",
            }.get(item.status, f"状态 {item.status} 不可批准")
        issues = validate_import_item(item, groups_by_id.get(item.group_id))
        errors = [issue.message for issue in issues if issue.severity == "error"]
        if reason is None and errors:
            reason = "; ".join(errors)
        suggestion = suggestions.get(item.id)
        if reason is None and suggestion is None:
            reason = "没有可用的主知识点候选"
        previews[item.id] = {
            "eligible": reason is None,
            "reason": reason,
            "suggestion": suggestion,
        }
    return previews


async def _validate_approval_selection(
    session: AsyncSession,
    item: QuestionImportItem,
    batch: QuestionImportBatch,
    selection: KnowledgeSelectionInput,
) -> tuple[
    dict[str, KnowledgeNode],
    dict[tuple[str, str], QuestionClassificationCandidate],
]:
    group = await session.get(QuestionImportGroup, item.group_id) if item.group_id else None
    issues = validate_import_item(item, group)
    item.validation_issues = [issue.model_dump() for issue in issues]
    errors = [issue.message for issue in issues if issue.severity == "error"]
    if errors:
        raise ImportWorkflowError("; ".join(errors))

    codes = [selection.primary_code, *selection.related_codes]
    nodes = list(
        (
            await session.scalars(
                select(KnowledgeNode).where(
                    KnowledgeNode.subject_id == batch.subject_id,
                    KnowledgeNode.code.in_(codes),
                    KnowledgeNode.node_type == "topic",
                    KnowledgeNode.status == "active",
                )
            )
        ).all()
    )
    by_code = {node.code: node for node in nodes}
    missing = [code for code in codes if code not in by_code]
    if missing:
        raise ImportWorkflowError(
            f"knowledge selections must be active topic nodes: {', '.join(missing)}"
        )

    latest_run_id = await matching_classification_run_id(session, item, group, batch)
    candidate_by_role_and_code: dict[
        tuple[str, str], QuestionClassificationCandidate
    ] = {}
    if latest_run_id is not None:
        candidates = (
            await session.execute(
                select(QuestionClassificationCandidate, KnowledgeNode.code)
                .join(
                    KnowledgeNode,
                    KnowledgeNode.id == QuestionClassificationCandidate.knowledge_node_id,
                )
                .where(QuestionClassificationCandidate.run_id == latest_run_id)
            )
        ).all()
        candidate_by_role_and_code = {
            (candidate.role, code): candidate for candidate, code in candidates
        }
    return by_code, candidate_by_role_and_code


async def _apply_approval_selection(
    session: AsyncSession,
    item: QuestionImportItem,
    selection: KnowledgeSelectionInput,
    by_code: dict[str, KnowledgeNode],
    candidate_by_role_and_code: dict[
        tuple[str, str], QuestionClassificationCandidate
    ],
) -> None:
    await session.execute(
        delete(QuestionImportKnowledgeSelection).where(
            QuestionImportKnowledgeSelection.import_item_id == item.id
        )
    )
    for role, selected_codes in (
        ("primary", [selection.primary_code]),
        ("related", selection.related_codes),
    ):
        for code in selected_codes:
            candidate = candidate_by_role_and_code.get((role, code))
            session.add(
                QuestionImportKnowledgeSelection(
                    import_item_id=item.id,
                    knowledge_node_id=by_code[code].id,
                    candidate_id=candidate.id if candidate else None,
                    role=role,
                    source="ai" if candidate else "manual",
                )
            )
    item.status = "approved"
    item.review_note = None


async def approve_import_item(
    session: AsyncSession,
    item_id: uuid.UUID,
    selection: KnowledgeSelectionInput,
) -> QuestionImportItem:
    item = await _get_item(session, item_id)
    if item.status == "published":
        raise ImportConflictError("published import items cannot be reviewed")
    batch = await _get_batch(session, item.batch_id)
    if batch.parser_version.startswith("markdown-"):
        raise ImportConflictError("Markdown 题目必须整题批准")
    by_code, candidate_by_role_and_code = await _validate_approval_selection(
        session, item, batch, selection
    )
    await _apply_approval_selection(
        session, item, selection, by_code, candidate_by_role_and_code
    )
    await session.flush()
    await _refresh_batch_status(session, batch.id)
    return item


async def approve_import_items(
    session: AsyncSession,
    batch_id: uuid.UUID,
    item_ids: list[uuid.UUID],
) -> BulkApproveItemsResult:
    batch = await _get_batch(session, batch_id)
    if batch.parser_version.startswith("markdown-"):
        raise ImportConflictError("Markdown 题目必须按完整组合题批量批准")
    locked_items = list(
        (
            await session.scalars(
                select(QuestionImportItem)
                .where(QuestionImportItem.id.in_(item_ids))
                .with_for_update()
            )
        ).all()
    )
    by_id = {item.id: item for item in locked_items}
    ordered_items = [by_id[item_id] for item_id in item_ids if item_id in by_id]
    problems: list[dict[str, Any]] = []
    for item_id in item_ids:
        item = by_id.get(item_id)
        if item is None:
            problems.append(
                {"item_id": str(item_id), "question_no": None, "reason": "题目不存在"}
            )
        elif item.batch_id != batch.id:
            problems.append(
                {
                    "item_id": str(item.id),
                    "question_no": item.question_no,
                    "reason": "题目不属于当前批次",
                }
            )
        elif item.status != "needs_review":
            problems.append(
                {
                    "item_id": str(item.id),
                    "question_no": item.question_no,
                    "reason": f"题目状态 {item.status} 不可批量批准",
                }
            )

    eligible_items = [
        item
        for item in ordered_items
        if item.batch_id == batch.id and item.status == "needs_review"
    ]
    suggestions = await _load_approval_suggestions(session, batch, eligible_items)
    prepared: dict[
        uuid.UUID,
        tuple[
            KnowledgeSelectionInput,
            dict[str, KnowledgeNode],
            dict[tuple[str, str], QuestionClassificationCandidate],
        ],
    ] = {}
    for item in eligible_items:
        suggestion = suggestions.get(item.id)
        if suggestion is None:
            problems.append(
                {
                    "item_id": str(item.id),
                    "question_no": item.question_no,
                    "reason": "没有可用的主知识点候选",
                }
            )
            continue
        selection = suggestion.as_selection()
        try:
            selection_data = await _validate_approval_selection(
                session, item, batch, selection
            )
        except ImportWorkflowError as exc:
            problems.append(
                {
                    "item_id": str(item.id),
                    "question_no": item.question_no,
                    "reason": str(exc),
                }
            )
            continue
        prepared[item.id] = (selection, *selection_data)

    if problems:
        problems.sort(key=lambda value: value["question_no"] or 0)
        raise BulkApprovalError(problems)

    for item in ordered_items:
        selection, nodes_by_code, candidates_by_code = prepared[item.id]
        await _apply_approval_selection(
            session,
            item,
            selection,
            nodes_by_code,
            candidates_by_code,
        )
    await session.flush()
    await _refresh_batch_status(session, batch.id)
    await session.flush()
    return BulkApproveItemsResult(
        batch_id=batch.id,
        approved_item_ids=item_ids,
        approved_count=len(item_ids),
        batch_status=batch.status,
    )


async def reject_import_item(
    session: AsyncSession, item_id: uuid.UUID, reason: str
) -> QuestionImportItem:
    item = await _get_item(session, item_id)
    if (await _get_batch(session, item.batch_id)).parser_version.startswith("markdown-"):
        raise ImportConflictError("Markdown 题目必须整题排除")
    if item.status == "published":
        raise ImportConflictError("published import items cannot be rejected")
    item.status = "rejected"
    item.review_note = reason
    await _refresh_batch_status(session, item.batch_id)
    await session.flush()
    return item


async def create_question_asset(
    session: AsyncSession,
    item_id: uuid.UUID,
    crop: AssetCropCreate,
    storage: LocalImportStorage,
) -> QuestionAsset:
    item = await _get_item(session, item_id)
    document = await session.get(QuestionSourceDocument, crop.document_id)
    if document is None or document.batch_id != item.batch_id:
        raise ImportWorkflowError("source document does not belong to this import item")
    if document.page_count is None or crop.page_no > document.page_count:
        raise ImportWorkflowError("asset page is outside source document")
    asset_id = uuid.uuid4()
    result = await storage.crop_asset(
        item.batch_id, document.id, crop.page_no, crop.bbox, asset_id
    )
    existing = await session.scalar(
        select(QuestionAsset).where(
            QuestionAsset.document_id == document.id,
            QuestionAsset.page_no == crop.page_no,
            QuestionAsset.sha256 == result.sha256,
        )
    )
    if existing is not None:
        Path(storage.resolve(result.relative_path)).unlink(missing_ok=True)
        asset = existing
    else:
        asset = QuestionAsset(
            id=asset_id,
            document_id=document.id,
            page_no=crop.page_no,
            bbox=crop.bbox.model_dump(),
            kind=crop.kind,
            storage_path=result.relative_path,
            sha256=result.sha256,
            width=result.width,
            height=result.height,
        )
        session.add(asset)
        await session.flush()

    if crop.placement == "group_material" and item.group_id is None:
        raise ImportWorkflowError("this question does not belong to a shared group")
    owner_filter = (
        QuestionAssetUsage.import_group_id == item.group_id
        if crop.placement == "group_material"
        else QuestionAssetUsage.import_item_id == item.id
    )
    usage = await session.scalar(
        select(QuestionAssetUsage).where(
            QuestionAssetUsage.asset_id == asset.id,
            owner_filter,
            QuestionAssetUsage.placement == crop.placement,
        )
    )
    if usage is None:
        usage_count = int(
            await session.scalar(
                select(func.count(QuestionAssetUsage.id)).where(owner_filter)
            )
            or 0
        )
        session.add(
            QuestionAssetUsage(
                asset_id=asset.id,
                import_item_id=item.id if crop.placement == "stem" else None,
                import_group_id=item.group_id if crop.placement == "group_material" else None,
                placement=crop.placement,
                sort_order=usage_count,
                alt_text=crop.alt_text,
            )
        )
    await session.flush()
    return asset


async def publish_batch(
    session: AsyncSession, batch_id: uuid.UUID
) -> PublishResult:
    batch = await session.scalar(
        select(QuestionImportBatch)
        .where(QuestionImportBatch.id == batch_id)
        .with_for_update()
    )
    if batch is None:
        raise ImportNotFoundError("import batch not found")
    if batch.status == "published" and batch.published_paper_id:
        paper = await session.get(ExamPaper, batch.published_paper_id)
        count = int(
            await session.scalar(
                select(func.count(Question.id)).where(Question.paper_id == paper.id)
            )
            or 0
        )
        return PublishResult(
            batch_id=batch.id,
            paper_id=paper.id,
            question_count=count,
            total_score=paper.total_score,
            already_published=True,
        )
    if batch.status != "ready_to_publish":
        raise ImportConflictError("batch has not passed all review gates")
    items = list(
        (
            await session.scalars(
                select(QuestionImportItem)
                .where(QuestionImportItem.batch_id == batch.id)
                .order_by(QuestionImportItem.question_no)
            )
        ).all()
    )
    _validate_batch_for_publish(items)
    await _validate_batch_asset_usages(session, items)
    collision = await session.scalar(
        select(ExamPaper).where(
            ExamPaper.subject_id == batch.subject_id,
            ExamPaper.year == batch.year,
            ExamPaper.period == batch.period,
            ExamPaper.batch_code == batch.batch_code,
        )
    )
    if collision is not None:
        raise ImportConflictError("an exam paper already exists for this session")
    total_score = sum((item.score for item in items), Decimal("0"))
    paper = ExamPaper(
        subject_id=batch.subject_id,
        year=batch.year,
        period=batch.period,
        batch_code=batch.batch_code,
        exam_date=batch.exam_date,
        title=batch.title,
        source_reference=batch.source_reference,
        status="verified",
        total_score=total_score,
    )
    session.add(paper)
    await session.flush()

    import_groups = list(
        (
            await session.scalars(
                select(QuestionImportGroup)
                .where(QuestionImportGroup.batch_id == batch.id)
                .order_by(QuestionImportGroup.sort_order)
            )
        ).all()
    )
    formal_groups: dict[uuid.UUID, QuestionGroup] = {}
    for value in import_groups:
        group = QuestionGroup(
            subject_id=batch.subject_id,
            paper_id=paper.id,
            source_label=value.source_label,
            material_markdown=value.material_markdown,
            sort_order=value.sort_order,
        )
        session.add(group)
        formal_groups[value.id] = group
    await session.flush()
    for import_group_id, formal_group in formal_groups.items():
        usages = list(
            (
                await session.scalars(
                    select(QuestionAssetUsage).where(
                        QuestionAssetUsage.import_group_id == import_group_id
                    )
                )
            ).all()
        )
        for usage in usages:
            session.add(
                QuestionAssetUsage(
                    asset_id=usage.asset_id,
                    question_group_id=formal_group.id,
                    placement=usage.placement,
                    sort_order=usage.sort_order,
                    alt_text=usage.alt_text,
                )
            )

    for item in items:
        selections = list(
            (
                await session.scalars(
                    select(QuestionImportKnowledgeSelection).where(
                        QuestionImportKnowledgeSelection.import_item_id == item.id
                    )
                )
            ).all()
        )
        primary_count = sum(value.role == "primary" for value in selections)
        if primary_count != 1:
            raise ImportWorkflowError(
                f"question {item.question_no} must have exactly one primary knowledge node"
            )
        payload = {
            "stem": item.stem_markdown,
            "options": item.options_payload,
            "answer": item.correct_option_keys,
            "explanation": item.explanation_markdown,
        }
        question = Question(
            subject_id=batch.subject_id,
            paper_id=paper.id,
            group_id=formal_groups[item.group_id].id if item.group_id else None,
            group_order=item.question_no if item.group_id else None,
            question_no=item.question_no,
            question_type=item.question_type,
            stem_markdown=item.stem_markdown,
            explanation_markdown=item.explanation_markdown,
            score=item.score,
            source_type="official",
            source_reference=f"import-batch:{batch.id};item:{item.id}",
            content_hash=hashlib.sha256(
                json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            status="published",
        )
        session.add(question)
        await session.flush()
        usages = list(
            (
                await session.scalars(
                    select(QuestionAssetUsage).where(
                        QuestionAssetUsage.import_item_id == item.id
                    )
                )
            ).all()
        )
        for usage in usages:
            session.add(
                QuestionAssetUsage(
                    asset_id=usage.asset_id,
                    question_id=question.id,
                    placement=usage.placement,
                    sort_order=usage.sort_order,
                    alt_text=usage.alt_text,
                )
            )
        options_by_key: dict[str, QuestionOption] = {}
        for sort_order, option_payload in enumerate(item.options_payload, 1):
            option = QuestionOption(
                question_id=question.id,
                option_key=option_payload["key"],
                content_markdown=option_payload["content_markdown"],
                sort_order=sort_order,
            )
            session.add(option)
            options_by_key[option.option_key] = option
        await session.flush()
        for answer_key in item.correct_option_keys:
            session.add(
                QuestionCorrectOption(
                    question_id=question.id,
                    option_id=options_by_key[answer_key].id,
                )
            )
        for selected in selections:
            session.add(
                QuestionKnowledgeAssignment(
                    subject_id=batch.subject_id,
                    question_id=question.id,
                    knowledge_node_id=selected.knowledge_node_id,
                    taxonomy_release_id=batch.taxonomy_release_id,
                    role=selected.role,
                    status="confirmed",
                    source=selected.source,
                )
            )
        item.published_question_id = question.id
        item.status = "published"
    await session.flush()
    await recompute_paper_aggregates(session, paper.id, calculation_version="import-v1")
    batch.published_paper_id = paper.id
    batch.status = "published"
    await session.flush()
    return PublishResult(
        batch_id=batch.id,
        paper_id=paper.id,
        question_count=len(items),
        total_score=total_score,
    )


def _validate_batch_for_publish(items: list[QuestionImportItem]) -> None:
    if len(items) != EXPECTED_QUESTION_COUNT:
        raise ImportWorkflowError(
            f"expected {EXPECTED_QUESTION_COUNT} questions, found {len(items)}"
        )
    numbers = [item.question_no for item in items]
    expected = list(range(1, EXPECTED_QUESTION_COUNT + 1))
    if numbers != expected:
        raise ImportWorkflowError("question numbers must be unique and continuous from 1 to 75")
    invalid = [item.question_no for item in items if item.status != "approved"]
    if invalid:
        raise ImportWorkflowError(f"questions are not approved: {invalid}")
    for item in items:
        errors = [
            issue
            for issue in item.validation_issues
            if issue.get("severity") == "error"
        ]
        if errors:
            raise ImportWorkflowError(f"question {item.question_no} has blocking issues")


def _asset_ids_from_markdown(markdown: str | None) -> set[uuid.UUID]:
    return {
        uuid.UUID(raw_id)
        for raw_id in ASSET_URI_PATTERN.findall(markdown or "")
    }


async def _validate_batch_asset_usages(
    session: AsyncSession, items: list[QuestionImportItem]
) -> None:
    group_ids = {item.group_id for item in items if item.group_id is not None}
    if group_ids:
        groups = list(
            (
                await session.scalars(
                    select(QuestionImportGroup).where(QuestionImportGroup.id.in_(group_ids))
                )
            ).all()
        )
        for group in groups:
            referenced = _asset_ids_from_markdown(group.material_markdown)
            linked = set(
                (
                    await session.scalars(
                        select(QuestionAssetUsage.asset_id).where(
                            QuestionAssetUsage.import_group_id == group.id
                        )
                    )
                ).all()
            )
            if referenced != linked:
                raise ImportWorkflowError(
                    f"shared group {group.source_label} has inconsistent visual asset links"
                )
    for item in items:
        markdown_values = [item.stem_markdown, item.explanation_markdown]
        markdown_values.extend(
            str(option.get("content_markdown", "")) for option in item.options_payload
        )
        referenced: set[uuid.UUID] = set()
        for markdown in markdown_values:
            referenced.update(_asset_ids_from_markdown(markdown))
        linked = set(
            (
                await session.scalars(
                    select(QuestionAssetUsage.asset_id).where(
                        QuestionAssetUsage.import_item_id == item.id
                    )
                )
            ).all()
        )
        if referenced != linked:
            raise ImportWorkflowError(
                f"question {item.question_no} has inconsistent visual asset links"
            )


async def reconcile_batch(
    session: AsyncSession, batch_id: uuid.UUID, storage: LocalImportStorage, *, apply: bool = False,
) -> dict[str, Any]:
    """Compare local OCR evidence with a pre-review batch, then optionally apply safe changes."""
    batch = await _get_batch(session, batch_id)
    if batch.status in {"published", "ready_to_publish"} or batch.published_paper_id:
        raise ImportConflictError("published or approved batches cannot be reconciled")
    documents = list((await session.scalars(
        select(QuestionSourceDocument).where(QuestionSourceDocument.batch_id == batch.id)
    )).all())
    items = list((await session.scalars(
        select(QuestionImportItem).where(QuestionImportItem.batch_id == batch.id)
        .order_by(QuestionImportItem.question_no)
    )).all())
    if any(item.status in {"approved", "published"} for item in items):
        raise ImportConflictError("cannot reconcile after review has started")
    if not documents or not items:
        raise ImportWorkflowError("batch has no parsed items")
    proposals: dict[int, dict[str, Any]] = {}
    ocr_pages = 0
    failed_pages: list[dict[str, Any]] = []
    for document in documents:
        selection = SourceSectionSelection.model_validate(document.page_selections)
        page_numbers = sorted({number for ranges in (selection.questions, selection.answers)
                               for page_range in ranges for number in page_range.pages()})
        source_path = storage.resolve(document.storage_path)
        if not source_path.is_file() or source_path.suffix.lower() != ".pdf":
            raise ImportWorkflowError("reconciliation requires the original PDF")
        pages = []
        for page_no in page_numbers:
            try:
                payload = await extract_page(
                    source_path, storage.page_image_path(batch.id, document.id, page_no),
                    storage.extraction_cache_path(batch.id, document.id, page_no),
                    source_sha256=document.sha256, page_no=page_no,
                )
            except Exception as exc:
                failed_pages.append({"document_id": str(document.id), "page_no": page_no,
                                     "stage": "ocr", "error": str(exc)[:500]})
                continue
            pages.append(payload)
            ocr_pages += payload["method"] == "ocr"
        parsed = segment_pages(pages, str(document.id))
        for number, question in parsed["questions"].items():
            proposal = proposals.setdefault(number, {"question": None, "answer": None, "conflicts": []})
            proposal["question"] = question
        for number, answer in parsed["answers"].items():
            proposal = proposals.setdefault(number, {"question": None, "answer": None, "conflicts": []})
            proposal["answer"] = answer
        for number, messages in parsed["conflicts"].items():
            proposal = proposals.setdefault(number, {"question": None, "answer": None, "conflicts": []})
            proposal["conflicts"].extend(messages)
    by_number = {item.question_no: item for item in items}
    changes = []
    for number in sorted(proposals):
        item = by_number.get(number)
        proposal = proposals[number]
        if item is None:
            continue
        question, answer = proposal["question"], proposal["answer"]
        option_keys = set(question["options"]) if question else set()
        evidence_complete = bool(
            question and len(question["stem"].strip()) >= 12
            and option_keys == {"A", "B", "C", "D"}
            and answer and len(answer["keys"]) == 1 and answer["explanation"].strip()
            and not proposal["conflicts"]
        )
        existing_conflict = any(x.get("code") == "conflicting_answers" for x in item.validation_issues)
        source_close = bool(
            question and answer and question["refs"] and answer["refs"]
            and 0 <= answer["refs"][0]["page_no"] - question["refs"][-1]["page_no"] <= 1
        )
        anchors_confident = bool(
            question and answer and question["refs"] and answer["refs"]
            and question["refs"][0].get("score", 0) >= 0.7
            and answer["refs"][0].get("score", 0) >= 0.7
        )
        # Automatic changes are limited to structurally blocked, standalone
        # items. Valid questions still appear in the preview for human review.
        structural_block = any(
            issue.get("severity") == "error" and issue.get("code") in STRUCTURAL_ISSUE_CODES
            for issue in item.validation_issues
        )
        safe = bool(evidence_complete and source_close and anchors_confident
                    and structural_block and not item.group_id and not existing_conflict
                    and not any(ref.get("needs_visual_asset") for ref in item.source_refs))
        safe_options = bool(
            question and option_keys == {"A", "B", "C", "D"}
            and question["refs"] and question["refs"][0].get("score", 0) >= 0.7
            and any(x.get("code") in {"invalid_options", "empty_option"}
                    for x in item.validation_issues)
            and not existing_conflict
        )
        differences = []
        if question:
            if question["stem"] != item.stem_markdown:
                differences.append("stem")
            if question["options"] != {x["key"]: x["content_markdown"] for x in item.options_payload}:
                differences.append("options")
        if answer:
            if answer["keys"] != item.correct_option_keys:
                differences.append("answer")
            if answer["explanation"] != (item.explanation_markdown or ""):
                differences.append("explanation")
        if differences or proposal["conflicts"]:
            applicable_fields = differences if safe else (["options"] if safe_options and "options" in differences else [])
            changes.append({
                "question_no": number, "fields": differences,
                "applicable_fields": applicable_fields,
                "auto_applicable": bool(applicable_fields),
                "conflicts": proposal["conflicts"],
                "proposed_stem": question["stem"] if question else None,
                "proposed_options": question["options"] if question else None,
                "proposed_answer": answer["keys"] if answer else None,
                "proposed_explanation": answer["explanation"] if answer else None,
                "source_refs": [*(question["refs"] if question else []),
                                *(answer["refs"] if answer else [])],
            })
    if apply and failed_pages:
        raise ImportWorkflowError(f"{len(failed_pages)} pages failed local extraction; review preview errors before applying")
    if apply:
        snapshot_dir = storage.resolve(f"{batch.id}/snapshots")
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        snapshot = snapshot_dir / f"before-reconcile-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}.json"
        raw_rows = list((await session.scalars(
            select(QuestionSourcePageExtraction).join(QuestionSourceDocument)
            .where(QuestionSourceDocument.batch_id == batch.id)
        )).all())
        repair_rows = list((await session.scalars(
            select(QuestionImportRepairRun).where(QuestionImportRepairRun.batch_id == batch.id)
        )).all())
        snapshot.write_text(json.dumps({
            "items": [{"id": str(x.id), "question_no": x.question_no, "stem": x.stem_markdown,
                       "options": x.options_payload, "answer": x.correct_option_keys,
                       "explanation": x.explanation_markdown, "refs": x.source_refs,
                       "issues": x.validation_issues} for x in items],
            "extractions": [{"id": str(x.id), "page_no": x.page_no, "role": x.role,
                             "raw": x.raw_response} for x in raw_rows],
            "repairs": [{"id": str(x.id), "pages": x.page_numbers,
                         "raw": x.raw_response} for x in repair_rows],
        }, ensure_ascii=False, default=str), encoding="utf-8")
        for change in changes:
            if not change["auto_applicable"]:
                continue
            item = by_number[change["question_no"]]
            question, answer = proposals[item.question_no]["question"], proposals[item.question_no]["answer"]
            fields = set(change["applicable_fields"])
            if "stem" in fields and question:
                item.stem_markdown = question["stem"]
            if "options" in fields and question:
                item.options_payload = [{"key": key, "content_markdown": question["options"][key]}
                                        for key in "ABCD"]
            if "answer" in fields and answer:
                item.correct_option_keys = answer["keys"]
            if "explanation" in fields and answer:
                item.explanation_markdown = answer["explanation"]
            new_refs = [*(question["refs"] if question and fields & {"stem", "options"} else []),
                        *(answer["refs"] if answer and fields & {"answer", "explanation"} else [])]
            item.source_refs = list(item.source_refs) + [ref for ref in new_refs if ref not in item.source_refs]
            item.validation_issues = [issue.model_dump() for issue in validate_import_item(item)]
            item.validation_issues.extend(
                ValidationIssue(code="source_conflict", severity="error", message=message).model_dump()
                for message in change["conflicts"]
            )
            item.status = "blocked" if any(issue["severity"] == "error" for issue in item.validation_issues) else "needs_review"
            await session.execute(delete(QuestionClassificationRun).where(
                QuestionClassificationRun.import_item_id == item.id
            ))
        await session.flush()
        updated = list((await session.scalars(
            select(QuestionImportItem).where(QuestionImportItem.batch_id == batch.id)
        )).all())
        batch.validation_summary = build_batch_validation_summary(updated, expected_count=batch.expected_question_count)
    extraction_runs = list((await session.scalars(
        select(QuestionSourcePageExtraction).join(QuestionSourceDocument)
        .where(QuestionSourceDocument.batch_id == batch.id)
    )).all())
    repair_runs = list((await session.scalars(
        select(QuestionImportRepairRun).where(QuestionImportRepairRun.batch_id == batch.id)
    )).all())
    vision_parse_calls = len({
        (row.document_id, row.page_no, row.prompt_version)
        if row.prompt_version == COMBINED_PROMPT_VERSION else (row.id,)
        for row in extraction_runs
        if row.status == "completed" and row.prompt_version != EXTRACTOR_VERSION
    })
    return {"batch_id": str(batch.id), "applied": apply, "ocr_pages": ocr_pages,
            "vision_parse_calls": vision_parse_calls,
            "vision_repair_calls": len(repair_runs),
            "failed_repair_windows": len([run for run in repair_runs if run.status == "failed"]),
            "changes": changes, "failed_pages": failed_pages,
            "auto_applicable_count": sum(x["auto_applicable"] for x in changes)}


async def _refresh_batch_status(session: AsyncSession, batch_id: uuid.UUID) -> None:
    batch = await _get_batch(session, batch_id)
    if batch.parser_version.startswith("markdown-"):
        from app.imports.markdown_workflow import refresh
        await refresh(session, batch)
        return
    rows = (
        await session.execute(
            select(QuestionImportItem.question_no, QuestionImportItem.status).where(
                QuestionImportItem.batch_id == batch_id
            )
        )
    ).all()
    if (
        len(rows) == EXPECTED_QUESTION_COUNT
        and sorted(number for number, _ in rows)
        == list(range(1, EXPECTED_QUESTION_COUNT + 1))
        and all(status == "approved" for _, status in rows)
    ):
        batch.status = "ready_to_publish"
    elif batch.status != "published":
        batch.status = "in_review"


async def get_batch_read(session: AsyncSession, batch_id: uuid.UUID) -> ImportBatchRead:
    batch = await _get_batch(session, batch_id)
    await session.refresh(batch, attribute_names=["updated_at"])
    subject = await session.get(ExamSubject, batch.subject_id)
    release = await session.get(KnowledgeTaxonomyRelease, batch.taxonomy_release_id)
    documents = list(
        (
            await session.scalars(
                select(QuestionSourceDocument)
                .where(QuestionSourceDocument.batch_id == batch.id)
                .order_by(QuestionSourceDocument.created_at)
            )
        ).all()
    )
    item_rows = (
        await session.execute(
            select(QuestionImportItem.status, func.count(QuestionImportItem.id))
            .where(QuestionImportItem.batch_id == batch.id)
            .group_by(QuestionImportItem.status)
        )
    ).all()
    extraction_rows = (
        await session.execute(
            select(QuestionSourcePageExtraction.status, func.count(QuestionSourcePageExtraction.id))
            .join(
                QuestionSourceDocument,
                QuestionSourceDocument.id == QuestionSourcePageExtraction.document_id,
            )
            .where(QuestionSourceDocument.batch_id == batch.id)
            .group_by(QuestionSourcePageExtraction.status)
        )
    ).all()
    selected_pages, _effective_extractions, parse_counts, parse_failures = _parse_page_state(
        documents,
        list((await session.scalars(
            select(QuestionSourcePageExtraction).join(QuestionSourceDocument)
            .where(QuestionSourceDocument.batch_id == batch.id)
        )).all()),
    )
    failures: list[dict] = []
    for failure in parse_failures:
        failures.append({"stage": "页面解析", "page_no": failure["page_no"],
                         "role": "、".join(failure["roles"]), "reason": failure["reason"]})
    if not selected_pages:
        for row in (await session.scalars(
            select(QuestionSourcePageExtraction).join(QuestionSourceDocument)
            .where(QuestionSourceDocument.batch_id == batch.id,
                   QuestionSourcePageExtraction.status == "failed")
            .order_by(QuestionSourcePageExtraction.page_no)
        )).all():
            failures.append({"stage": "页面解析", "page_no": row.page_no,
                             "role": row.role, "reason": row.error_summary or "未知错误"})
    if not batch.validation_summary.get("is_valid"):
        for row in (await session.scalars(
            select(QuestionImportRepairRun)
            .where(QuestionImportRepairRun.batch_id == batch.id, QuestionImportRepairRun.status == "failed")
            .order_by(QuestionImportRepairRun.created_at.desc())
        )).all():
            failures.append({"stage": "自动修复", "page_numbers": row.page_numbers, "question_numbers": row.question_numbers, "reason": row.error_summary or "未知错误"})
    classification_rows = (await session.execute(
        select(QuestionClassificationRun, QuestionImportItem.question_no)
        .join(QuestionImportItem, QuestionImportItem.id == QuestionClassificationRun.import_item_id)
        .where(QuestionImportItem.batch_id == batch.id)
        .order_by(QuestionImportItem.id, QuestionClassificationRun.created_at.desc(), QuestionClassificationRun.id.desc())
    )).all()
    seen_classification_items: set[uuid.UUID] = set()
    for row, number in classification_rows:
        if row.import_item_id in seen_classification_items:
            continue
        seen_classification_items.add(row.import_item_id)
        if row.status == "failed":
            failures.append({"stage": "知识点分类", "question_numbers": [number], "reason": row.error_summary or "未知错误"})
    if batch.status in {"published", "ready_to_publish"}:
        failures = []
    return ImportBatchRead(
        id=batch.id,
        subject_code=subject.code,
        taxonomy_version=release.version,
        year=batch.year,
        period=batch.period,
        batch_code=batch.batch_code,
        title=batch.title,
        exam_date=batch.exam_date,
        status=batch.status,
        parser_version=batch.parser_version,
        expected_question_count=batch.expected_question_count,
        validation_summary=batch.validation_summary,
        source_reference=batch.source_reference,
        error_summary=batch.error_summary,
        documents=[
            SourceDocumentRead(
                id=value.id,
                role=value.role,
                original_name=value.original_name,
                mime_type=value.mime_type,
                file_size=value.file_size,
                sha256=value.sha256,
                page_count=value.page_count,
                page_selections=value.page_selections,
                section_suggestion=value.section_suggestion,
                has_extracted_content=bool(value.extracted_content),
                status=value.status,
                error_summary=value.error_summary,
            )
            for value in documents
        ],
        item_counts={status: count for status, count in item_rows},
        extraction_counts={status: count for status, count in extraction_rows},
        parse_progress=parse_counts,
        failures=failures,
        published_paper_id=batch.published_paper_id,
        created_at=batch.created_at,
        updated_at=batch.updated_at,
    )


async def list_import_items(
    session: AsyncSession, batch_id: uuid.UUID, *, offset: int, limit: int
) -> list[ImportItemRead]:
    await _get_batch(session, batch_id)
    items = list(
        (
            await session.scalars(
                select(QuestionImportItem)
                .where(QuestionImportItem.batch_id == batch_id)
                .order_by(QuestionImportItem.question_no)
                .offset(offset)
                .limit(limit)
            )
        ).all()
    )
    return [await get_item_read(session, item.id) for item in items]


async def get_item_read(session: AsyncSession, item_id: uuid.UUID) -> ImportItemRead:
    item = await _get_item(session, item_id)
    group = await session.get(QuestionImportGroup, item.group_id) if item.group_id else None
    batch = await _get_batch(session, item.batch_id)
    valid_run_id = await matching_classification_run_id(session, item, group, batch)
    candidate_rows = (
        await session.execute(
            select(QuestionClassificationCandidate, KnowledgeNode)
            .join(KnowledgeNode, KnowledgeNode.id == QuestionClassificationCandidate.knowledge_node_id)
            .join(
                QuestionClassificationRun,
                QuestionClassificationRun.id == QuestionClassificationCandidate.run_id,
            )
            .where(
                QuestionClassificationRun.import_item_id == item.id,
                QuestionClassificationRun.status == "completed",
                QuestionClassificationRun.id == valid_run_id,
                KnowledgeNode.subject_id == batch.subject_id,
                KnowledgeNode.node_type == 'topic',
                KnowledgeNode.status == 'active',
            )
            .order_by(
                QuestionClassificationRun.created_at.desc(),
                QuestionClassificationCandidate.role,
                QuestionClassificationCandidate.rank,
            )
        )
    ).all()
    latest_run_id = candidate_rows[0][0].run_id if candidate_rows else None
    candidates = [
        ClassificationCandidateRead(
            id=candidate.id,
            node_code=node.code,
            node_name=node.name,
            role=candidate.role,
            rank=candidate.rank,
            confidence=candidate.confidence,
            rationale=candidate.rationale,
        )
        for candidate, node in candidate_rows
        if candidate.run_id == latest_run_id
    ]
    selection_rows = (
        await session.execute(
            select(QuestionImportKnowledgeSelection, KnowledgeNode)
            .join(KnowledgeNode, KnowledgeNode.id == QuestionImportKnowledgeSelection.knowledge_node_id)
            .where(QuestionImportKnowledgeSelection.import_item_id == item.id)
            .order_by(QuestionImportKnowledgeSelection.role)
        )
    ).all()
    return ImportItemRead(
        id=item.id,
        batch_id=item.batch_id,
        group_id=item.group_id,
        question_no=item.question_no,
        question_type=item.question_type,
        stem_markdown=item.stem_markdown,
        options=[ImportOption.model_validate(value) for value in item.options_payload],
        correct_option_keys=item.correct_option_keys,
        explanation_markdown=item.explanation_markdown,
        score=item.score,
        source_refs=item.source_refs,
        validation_issues=[ValidationIssue.model_validate(value) for value in item.validation_issues],
        status=item.status,
        review_note=item.review_note,
        candidates=candidates,
        selections=[
            KnowledgeSelectionRead(
                node_code=node.code,
                node_name=node.name,
                role=selection.role,
                source=selection.source,
            )
            for selection, node in selection_rows
        ],
        published_question_id=item.published_question_id,
    )


async def _get_batch(session: AsyncSession, batch_id: uuid.UUID) -> QuestionImportBatch:
    batch = await session.get(QuestionImportBatch, batch_id)
    if batch is None:
        raise ImportNotFoundError("import batch not found")
    return batch


async def _get_item(session: AsyncSession, item_id: uuid.UUID) -> QuestionImportItem:
    item = await session.get(QuestionImportItem, item_id)
    if item is None:
        raise ImportNotFoundError("import item not found")
    return item
