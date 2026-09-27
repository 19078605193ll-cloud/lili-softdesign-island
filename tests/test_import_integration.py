from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pymupdf
import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.imports.ai import QuestionAIClient, VISION_PROMPT_VERSION, COMBINED_PROMPT_VERSION
import app.imports.service as import_service
from app.imports.api import get_ai_client, get_import_storage
from app.imports.schemas import (
    AICandidate,
    AIClassificationBatch,
    AIQuestionClassification,
    AssetCropCreate,
    BoundingBox,
    ImportOption,
    VisionAnswer,
    VisionAnswerPage,
    VisionCombinedPage,
    VisionGroup,
    VisionItem,
    VisionQuestionPage,
    VisionPageRole,
    VisionPageRoles,
)
from app.imports.service import (
    ImportConflictError,
    classify_batch,
    create_question_asset,
    parse_batch,
    publish_batch,
    repair_batch,
    reconcile_batch,
    suggest_document_sections,
)
from app.imports.storage import LocalImportStorage
from app.main import app
from app.models import (
    ExamPaper,
    ExamSubject,
    KnowledgeExamAggregate,
    KnowledgeNode,
    KnowledgeTaxonomyRelease,
    Question,
    QuestionAsset,
    QuestionAssetUsage,
    QuestionImportBatch,
    QuestionClassificationCandidate,
    QuestionClassificationRun,
    QuestionImportGroup,
    QuestionImportItem,
    QuestionImportKnowledgeSelection,
    QuestionImportRepairRun,
    QuestionKnowledgeAssignment,
    QuestionSourceDocument,
    QuestionSourcePageExtraction,
)

pytestmark = pytest.mark.integration


async def test_local_ocr_parse_uses_position_rows_without_vision(
    session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject, release = await _subject_and_release(session)
    batch = QuestionImportBatch(subject_id=subject.id, taxonomy_release_id=release.id,
                                year=2023, period="first_half", batch_code="ocr-test",
                                title="OCR test", status="sectioned")
    session.add(batch)
    await session.flush()
    storage = LocalImportStorage(tmp_path / "imports")
    source = storage.resolve(f"{batch.id}/source/test.pdf")
    source.parent.mkdir(parents=True, exist_ok=True)
    pdf = pymupdf.open()
    pdf.new_page()
    pdf.save(source)
    pdf.close()
    document = QuestionSourceDocument(
        batch_id=batch.id, role="combined", original_name="test.pdf",
        mime_type="application/pdf", file_size=source.stat().st_size,
        sha256="a" * 64, storage_path=str(source.relative_to(storage.root)),
        page_count=1, page_selections={"questions": [{"start": 1, "end": 1}],
                                       "answers": [{"start": 1, "end": 1}]},
        status="rendered",
    )
    session.add(document)
    await session.flush()
    image = storage.resolve(f"{batch.id}/pages/{document.id}/page-0001.png")
    image.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (100, 100), "white").save(image)

    async def fake_extract(*args, **kwargs):
        return {"extractor_version": "position-v1", "page_no": 1, "method": "ocr",
                "lines": [{"text": text, "bbox": [0.1, y, 0.8, y + 0.02],
                           "score": 0.99, "furniture": False}
                          for y, text in [(0.2, "测试题目（1）"),
                                          (0.3, "A. 甲 B. 乙 C. 丙 D. 丁"),
                                          (0.4, "参考答案：B"),
                                          (0.5, "【解析】：乙正确")]]}

    monkeypatch.setattr(import_service, "extract_page", fake_extract)
    ai = FakeAI()
    progress = await parse_batch(session, batch.id, ai_client=ai, storage=storage, limit=1)
    assert progress.remaining == 0
    assert ai.calls == []
    item = await session.scalar(select(QuestionImportItem).where(
        QuestionImportItem.batch_id == batch.id, QuestionImportItem.question_no == 1))
    assert item is not None
    assert item.correct_option_keys == ["B"]
    assert {option["key"] for option in item.options_payload} == set("ABCD")
    before = (item.stem_markdown, list(item.options_payload), list(item.correct_option_keys))
    preview = await reconcile_batch(session, batch.id, storage)
    await session.refresh(item)
    assert preview["applied"] is False
    assert (item.stem_markdown, item.options_payload, item.correct_option_keys) == before
    item.status = "approved"
    await session.flush()
    with pytest.raises(ImportConflictError):
        await reconcile_batch(session, batch.id, storage, apply=True)


async def test_classification_skips_only_blocked_question(session: AsyncSession) -> None:
    subject, release = await _subject_and_release(session)
    batch = QuestionImportBatch(subject_id=subject.id, taxonomy_release_id=release.id,
                                year=2023, period="first_half", batch_code="classify-partial",
                                title="Partial classification", status="in_review")
    session.add(batch)
    await session.flush()
    good = QuestionImportItem(
        batch_id=batch.id, question_no=1, question_type="single_choice",
        stem_markdown="完整题干", options_payload=[{"key": k, "content_markdown": k} for k in "ABCD"],
        correct_option_keys=["A"], explanation_markdown="解析", score=Decimal("1"),
        status="needs_review", validation_issues=[],
    )
    bad = QuestionImportItem(
        batch_id=batch.id, question_no=2, question_type="single_choice",
        stem_markdown="缺选项", options_payload=[], correct_option_keys=["A"],
        score=Decimal("1"), status="blocked",
        validation_issues=[{"code": "invalid_options", "severity": "error", "message": "missing"}],
    )
    session.add_all([good, bad])
    await session.flush()
    result = await classify_batch(session, batch.id, ai_client=FakeAI(), limit=5)
    assert result.eligible_items == 1
    assert result.blocked_items == 1
    assert result.completed_items == 1
    assert await session.scalar(select(func.count(QuestionClassificationRun.id)).where(
        QuestionClassificationRun.import_item_id == bad.id)) == 0
    good.stem_markdown = "修改后的题干"
    await session.flush()
    rerun = await classify_batch(session, batch.id, ai_client=FakeAI(), limit=5)
    assert rerun.processed == 1
    assert await session.scalar(select(func.count(QuestionClassificationRun.id)).where(
        QuestionClassificationRun.import_item_id == good.id)) == 2


class FakeAI(QuestionAIClient):
    provider_name = "fake"
    vision_model = "fake-vision"
    classification_model = "fake-classifier"

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def classify_page_roles(self, pages: list[tuple[int, Path]]) -> VisionPageRoles:
        return VisionPageRoles(pages=[
            VisionPageRole(page_no=number, questions=number == 1, answers=number == 1)
            for number, _ in (pages[:-1] if len(pages) > 1 else pages)
        ])

    async def parse_question_page(
        self, image_path: Path, page_no: int
    ) -> tuple[VisionQuestionPage, dict]:
        self.calls.append(("questions", page_no))
        options = [
            ImportOption(key=key, content_markdown=f"选项 {key}")
            for key in ["A", "B", "C", "D"]
        ]
        groups = [
            VisionGroup(
                source_label="4-5",
                material_markdown="第 4-5 题共享材料",
                items=[
                    VisionItem(question_no=4, stem_markdown="第一个空", options=options),
                    VisionItem(question_no=5, stem_markdown="第二个空", options=options),
                ],
            )
        ]
        groups.extend(
            VisionGroup(
                source_label=str(number),
                material_markdown="",
                items=[
                    VisionItem(
                        question_no=number,
                        stem_markdown=f"第 {number} 题题干",
                        options=options,
                    )
                ],
            )
            for number in range(1, 76)
            if number not in {4, 5}
        )
        parsed = VisionQuestionPage(groups=groups)
        return parsed, parsed.model_dump(mode="json")

    async def parse_answer_page(
        self, image_path: Path, page_no: int
    ) -> tuple[VisionAnswerPage, dict]:
        self.calls.append(("answers", page_no))
        parsed = VisionAnswerPage(
            answers=[
                VisionAnswer(
                    question_no=number,
                    correct_option_keys=["A"],
                    explanation_markdown=f"第 {number} 题解析",
                )
                for number in range(1, 76)
            ]
        )
        return parsed, parsed.model_dump(mode="json")

    async def parse_combined_page(
        self, image_path: Path, page_no: int
    ) -> tuple[VisionCombinedPage, dict]:
        questions, _ = await self.parse_question_page(image_path, page_no)
        answers, _ = await self.parse_answer_page(image_path, page_no)
        self.calls = self.calls[:-2]
        self.calls.append(("combined", page_no))
        parsed = VisionCombinedPage(groups=questions.groups, answers=answers.answers)
        return parsed, parsed.model_dump(mode="json")

    async def repair_question_pages(
        self,
        image_paths: list[Path],
        page_nos: list[int],
        question_numbers: list[int],
        previous_items: list[dict],
    ) -> tuple[VisionQuestionPage, dict]:
        return await self.parse_question_page(image_paths[0], page_nos[0])

    async def repair_answer_pages(
        self,
        image_paths: list[Path],
        page_nos: list[int],
        question_numbers: list[int],
        previous_items: list[dict],
    ) -> tuple[VisionAnswerPage, dict]:
        return await self.parse_answer_page(image_paths[0], page_nos[0])

    async def classify_questions(
        self, items: list[dict], catalog: list[dict]
    ) -> tuple[AIClassificationBatch, dict]:
        code = "os.process.sync"
        parsed = AIClassificationBatch(
            results=[
                AIQuestionClassification(
                    question_no=item["question_no"],
                    primary_candidates=[
                        AICandidate(code=code, confidence=Decimal("0.9"), rationale="测试")
                    ],
                    related_candidates=[],
                )
                for item in items
            ]
        )
        return parsed, parsed.model_dump(mode="json")


class FakeRenderedStorage:
    def __init__(self, image_path: Path) -> None:
        self.image_path = image_path

    def page_image_path(self, batch_id: uuid.UUID, document_id: uuid.UUID, page_no: int) -> Path:
        return self.image_path


class RepairingFakeAI(FakeAI):
    async def parse_question_page(
        self, image_path: Path, page_no: int
    ) -> tuple[VisionQuestionPage, dict]:
        options = [
            ImportOption(key=key, content_markdown=f"option {key}")
            for key in ["A", "B", "C", "D"]
        ]
        parsed = VisionQuestionPage(
            groups=[
                VisionGroup(
                    source_label=str(number),
                    items=[
                        VisionItem(
                            question_no=number,
                            stem_markdown=f"question {number}",
                            options=options,
                        )
                    ],
                )
                for number in [1, 3]
            ]
        )
        return parsed, parsed.model_dump(mode="json")

    async def parse_answer_page(
        self, image_path: Path, page_no: int
    ) -> tuple[VisionAnswerPage, dict]:
        parsed = VisionAnswerPage(
            answers=[
                VisionAnswer(
                    question_no=number,
                    correct_option_keys=["A"],
                    explanation_markdown=f"answer {number}",
                )
                for number in [1, 2, 3]
            ]
        )
        return parsed, parsed.model_dump(mode="json")

    async def repair_question_pages(
        self,
        image_paths: list[Path],
        page_nos: list[int],
        question_numbers: list[int],
        previous_items: list[dict],
    ) -> tuple[VisionQuestionPage, dict]:
        options = [
            ImportOption(key=key, content_markdown=f"option {key}")
            for key in ["A", "B", "C", "D"]
        ]
        parsed = VisionQuestionPage(
            groups=[
                VisionGroup(
                    source_label=str(number),
                    items=[
                        VisionItem(
                            question_no=number,
                            stem_markdown=f"question {number}",
                            options=options,
                        )
                    ],
                )
                for number in question_numbers
            ]
        )
        return parsed, parsed.model_dump(mode="json")


async def _subject_and_release(
    session: AsyncSession,
) -> tuple[ExamSubject, KnowledgeTaxonomyRelease]:
    subject = await session.scalar(
        select(ExamSubject).where(ExamSubject.code == "software-designer.foundation")
    )
    release = await session.scalar(
        select(KnowledgeTaxonomyRelease).where(
            KnowledgeTaxonomyRelease.subject_id == subject.id
        )
    )
    assert subject and release
    return subject, release


async def _create_review_batch(
    session: AsyncSession,
    *,
    item_count: int = 2,
) -> tuple[QuestionImportBatch, list[QuestionImportItem], KnowledgeNode, KnowledgeNode]:
    subject, release = await _subject_and_release(session)
    primary_node = await session.scalar(
        select(KnowledgeNode).where(KnowledgeNode.code == "os.process.sync")
    )
    related_node = await session.scalar(
        select(KnowledgeNode).where(KnowledgeNode.code == "os.process.scheduling")
    )
    assert primary_node and related_node
    batch = QuestionImportBatch(
        subject_id=subject.id,
        taxonomy_release_id=release.id,
        year=2031,
        period="other",
        batch_code=f"approval-{uuid.uuid4()}",
        title="审核接口测试",
        status="in_review",
        validation_summary={"is_valid": True},
    )
    session.add(batch)
    await session.flush()
    items: list[QuestionImportItem] = []
    for number in range(1, item_count + 1):
        item = QuestionImportItem(
            batch_id=batch.id,
            question_no=number,
            question_type="single_choice",
            stem_markdown=f"第 {number} 题",
            options_payload=[
                {"key": key, "content_markdown": f"选项 {key}"}
                for key in ["A", "B", "C", "D"]
            ],
            correct_option_keys=["A"],
            explanation_markdown="解析",
            score=Decimal("1"),
            source_refs=[],
            validation_issues=[],
            status="needs_review",
        )
        session.add(item)
        await session.flush()
        run = QuestionClassificationRun(
            import_item_id=item.id,
            taxonomy_release_id=release.id,
            provider="fake",
            model="fake",
            prompt_version="test",
            catalog_checksum=release.checksum_sha256,
            input_fingerprint=import_service.classification_fingerprint(item),
            status="completed",
        )
        session.add(run)
        await session.flush()
        session.add_all(
            [
                QuestionClassificationCandidate(
                    run_id=run.id,
                    knowledge_node_id=primary_node.id,
                    role="primary",
                    rank=1,
                    confidence=Decimal("0.9"),
                    rationale="首选",
                ),
                QuestionClassificationCandidate(
                    run_id=run.id,
                    knowledge_node_id=related_node.id,
                    role="related",
                    rank=1,
                    confidence=Decimal("0.5"),
                    rationale="相关",
                ),
            ]
        )
        items.append(item)
    await session.commit()
    return batch, items, primary_node, related_node


async def test_parse_classify_review_and_publish_pipeline(
    session: AsyncSession, tmp_path: Path
) -> None:
    subject, release = await _subject_and_release(session)
    batch = QuestionImportBatch(
        subject_id=subject.id,
        taxonomy_release_id=release.id,
        year=2020,
        period="second_half",
        batch_code="pipeline-test",
        title="2020 年下半年测试导入",
        status="sectioned",
    )
    session.add(batch)
    await session.flush()
    document = QuestionSourceDocument(
        batch_id=batch.id,
        role="combined",
        original_name="fixture.pdf",
        mime_type="application/pdf",
        file_size=100,
        sha256="f" * 64,
        storage_path="fixture.pdf",
        page_count=2,
        page_selections={
            "questions": [{"start": 1, "end": 1}],
            "answers": [{"start": 2, "end": 2}],
        },
        status="rendered",
    )
    session.add(document)
    await session.commit()
    page_image = tmp_path / "page.png"
    Image.new("RGB", (100, 100), "white").save(page_image)
    ai = FakeAI()

    progress = await parse_batch(
        session,
        batch.id,
        ai_client=ai,
        storage=FakeRenderedStorage(page_image),  # type: ignore[arg-type]
        limit=2,
    )
    assert progress.remaining == 0
    assert progress.total_pages == 2
    assert progress.processed_pages == 2
    assert ai.calls == [("questions", 1), ("answers", 2)]
    assert progress.batch_status == "in_review"
    items = list(
        (
            await session.scalars(
                select(QuestionImportItem)
                .where(QuestionImportItem.batch_id == batch.id)
                .order_by(QuestionImportItem.question_no)
            )
        ).all()
    )
    assert len(items) == 75
    assert all(item.status == "needs_review" for item in items)
    storage = LocalImportStorage(tmp_path / "imports")
    rendered_page = storage.resolve(
        f"{batch.id}/pages/{document.id}/page-0001.png"
    )
    rendered_page.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (200, 200), "white").save(rendered_page)
    asset = await create_question_asset(
        session,
        items[0].id,
        AssetCropCreate(
            document_id=document.id,
            page_no=1,
            bbox=BoundingBox(x0=0.1, y0=0.1, x1=0.9, y1=0.9),
            kind="figure",
            placement="stem",
            alt_text="测试图",
        ),
        storage,
    )
    items[0].stem_markdown += f"\n\n![测试图](asset://{asset.id})"
    groups = list(
        (
            await session.scalars(
                select(QuestionImportGroup).where(QuestionImportGroup.batch_id == batch.id)
            )
        ).all()
    )
    assert [group.source_label for group in groups] == ["4-5"]

    classification = await classify_batch(
        session, batch.id, ai_client=ai, limit=5, storage=storage
    )
    assert classification.processed == 5
    assert classification.completed_items == 5

    node = await session.scalar(select(KnowledgeNode).where(KnowledgeNode.code == "os.process.sync"))
    assert node is not None
    for item in items:
        item.status = "approved"
        session.add(
            QuestionImportKnowledgeSelection(
                import_item_id=item.id,
                knowledge_node_id=node.id,
                role="primary",
                source="manual",
            )
        )
    batch.status = "ready_to_publish"
    await session.flush()

    result = await publish_batch(session, batch.id)
    await session.commit()
    assert result.question_count == 75
    assert result.total_score == Decimal("75")
    assert await session.scalar(
        select(func.count(Question.id)).where(Question.paper_id == result.paper_id)
    ) == 75
    first_question = await session.scalar(
        select(Question).where(
            Question.paper_id == result.paper_id,
            Question.question_no == 1,
        )
    )
    assert first_question is not None
    formal_usage = await session.scalar(
        select(QuestionAssetUsage).where(
            QuestionAssetUsage.asset_id == asset.id,
            QuestionAssetUsage.question_id == first_question.id,
        )
    )
    assert formal_usage is not None
    assert formal_usage.alt_text == "测试图"
    assert await session.scalar(
        select(func.count(QuestionKnowledgeAssignment.id)).where(
            QuestionKnowledgeAssignment.question_id.in_(
                select(Question.id).where(Question.paper_id == result.paper_id)
            )
        )
    ) == 75
    aggregate = await session.scalar(
        select(KnowledgeExamAggregate).where(
            KnowledgeExamAggregate.exam_paper_id == result.paper_id,
            KnowledgeExamAggregate.knowledge_node_id == node.id,
        )
    )
    assert aggregate is not None
    assert aggregate.primary_question_count == 75
    assert aggregate.primary_score_total == Decimal("75")
    repeated = await publish_batch(session, batch.id)
    assert repeated.already_published is True
    assert repeated.paper_id == result.paper_id


async def test_auto_repair_recovers_missing_cross_page_item(
    session: AsyncSession, tmp_path: Path
) -> None:
    subject, release = await _subject_and_release(session)
    batch = QuestionImportBatch(
        subject_id=subject.id,
        taxonomy_release_id=release.id,
        year=2021,
        period="other",
        batch_code="repair-test",
        title="repair test",
        status="sectioned",
        expected_question_count=3,
    )
    session.add(batch)
    await session.flush()
    document = QuestionSourceDocument(
        batch_id=batch.id,
        role="combined",
        original_name="repair.pdf",
        mime_type="application/pdf",
        file_size=100,
        sha256="e" * 64,
        storage_path="repair.pdf",
        page_count=2,
        page_selections={
            "questions": [{"start": 1, "end": 1}],
            "answers": [{"start": 2, "end": 2}],
        },
        status="rendered",
    )
    session.add(document)
    await session.commit()
    page_image = tmp_path / "repair-page.png"
    Image.new("RGB", (100, 100), "white").save(page_image)
    ai = RepairingFakeAI()
    storage = FakeRenderedStorage(page_image)

    parsed = await parse_batch(
        session,
        batch.id,
        ai_client=ai,
        storage=storage,  # type: ignore[arg-type]
        limit=2,
    )
    assert parsed.batch_status == "in_review"
    await session.refresh(batch)
    assert batch.validation_summary["is_valid"] is False
    assert batch.validation_summary["repairable_question_numbers"] == [2]

    repaired = await repair_batch(
        session,
        batch.id,
        ai_client=ai,
        storage=storage,  # type: ignore[arg-type]
        limit=1,
    )
    await session.commit()
    assert repaired.validation_summary["is_valid"] is True
    assert repaired.remaining_question_numbers == []


async def test_upload_api_retires_pdf_upload(
    client: AsyncClient, session: AsyncSession, tmp_path: Path
) -> None:
    storage = LocalImportStorage(tmp_path / "imports", render_dpi=72)
    app.dependency_overrides[get_import_storage] = lambda: storage
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "sample")
    pdf_bytes = document.tobytes()
    document.close()
    try:
        response = await client.post(
            "/api/v1/admin/import-batches",
            data={
                "subject_code": "software-designer.foundation",
                "year": "2030",
                "period": "other",
                "batch_code": "upload-test",
                "title": "上传接口测试",
            },
            files={"file": ("fixture.pdf", pdf_bytes, "application/pdf")},
        )
        assert response.status_code == 422, response.text
        assert "PDF/DOCX" in response.json()["detail"]
        admin_response = await client.get("/admin/imports")
        assert admin_response.status_code == 200
        assert "Markdown" in admin_response.text
    finally:
        app.dependency_overrides.pop(get_import_storage, None)


async def test_single_approval_api_persists_selected_knowledge(
    client: AsyncClient, session: AsyncSession
) -> None:
    _batch, items, primary_node, related_node = await _create_review_batch(session)

    response = await client.post(
        f"/api/v1/admin/import-items/{items[0].id}/approve",
        json={
            "primary_code": primary_node.code,
            "related_codes": [related_node.code],
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "approved"
    assert {(value["role"], value["node_code"]) for value in payload["selections"]} == {
        ("primary", primary_node.code),
        ("related", related_node.code),
    }


async def test_single_approval_api_rejects_empty_primary_code(
    client: AsyncClient, session: AsyncSession
) -> None:
    _batch, items, _primary_node, _related_node = await _create_review_batch(session)

    response = await client.post(
        f"/api/v1/admin/import-items/{items[0].id}/approve",
        json={"primary_code": "", "related_codes": []},
    )

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


async def test_bulk_approval_uses_ai_defaults_and_updates_all_selected_items(
    client: AsyncClient, session: AsyncSession
) -> None:
    batch, items, primary_node, related_node = await _create_review_batch(session)

    response = await client.post(
        f"/api/v1/admin/import-batches/{batch.id}/approve-items",
        json={"item_ids": [str(item.id) for item in items]},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["approved_count"] == 2
    assert set(payload["approved_item_ids"]) == {str(item.id) for item in items}
    assert payload["batch_status"] == "in_review"
    for item in items:
        item_response = await client.get(f"/api/v1/admin/import-items/{item.id}")
        assert item_response.status_code == 200
        item_payload = item_response.json()
        assert item_payload["status"] == "approved"
        assert {(value["role"], value["node_code"]) for value in item_payload["selections"]} == {
            ("primary", primary_node.code),
            ("related", related_node.code),
        }


async def test_bulk_approval_is_atomic_and_reports_item_errors(
    client: AsyncClient, session: AsyncSession
) -> None:
    batch, items, _primary_node, _related_node = await _create_review_batch(session)
    items[1].options_payload = items[1].options_payload[:2]
    await session.commit()
    item_ids = [item.id for item in items]

    response = await client.post(
        f"/api/v1/admin/import-batches/{batch.id}/approve-items",
        json={"item_ids": [str(item_id) for item_id in item_ids]},
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["message"] == "批量批准失败，未修改任何题目"
    assert detail["items"][0]["item_id"] == str(item_ids[1])
    assert detail["items"][0]["question_no"] == 2
    session.expire_all()
    statuses = list(
        (
            await session.scalars(
                select(QuestionImportItem.status)
                .where(QuestionImportItem.id.in_(item_ids))
                .order_by(QuestionImportItem.question_no)
            )
        ).all()
    )
    assert statuses == ["needs_review", "needs_review"]


async def test_bulk_approval_marks_complete_75_question_batch_ready(
    client: AsyncClient, session: AsyncSession
) -> None:
    batch, items, _primary_node, _related_node = await _create_review_batch(
        session, item_count=75
    )

    response = await client.post(
        f"/api/v1/admin/import-batches/{batch.id}/approve-items",
        json={"item_ids": [str(item.id) for item in items]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["approved_count"] == 75
    assert response.json()["batch_status"] == "ready_to_publish"


async def test_bulk_approval_uses_latest_ranked_defaults_and_deduplicates_related(
    client: AsyncClient, session: AsyncSession
) -> None:
    batch, items, old_primary, related_node = await _create_review_batch(
        session, item_count=1
    )
    alternate_primary = await session.scalar(
        select(KnowledgeNode).where(
            KnowledgeNode.code == "architecture.storage.hierarchy"
        )
    )
    assert alternate_primary is not None
    release = await session.get(KnowledgeTaxonomyRelease, batch.taxonomy_release_id)
    assert release is not None
    latest_run = QuestionClassificationRun(
        import_item_id=items[0].id,
        taxonomy_release_id=release.id,
        provider="fake",
        model="fake-v2",
        prompt_version="test-v2",
        catalog_checksum=release.checksum_sha256,
        input_fingerprint=import_service.classification_fingerprint(items[0]),
        status="completed",
        created_at=datetime.now(timezone.utc) + timedelta(seconds=1),
    )
    session.add(latest_run)
    await session.flush()
    session.add_all(
        [
            QuestionClassificationCandidate(
                run_id=latest_run.id,
                knowledge_node_id=alternate_primary.id,
                role="primary",
                rank=1,
                confidence=Decimal("0.95"),
                rationale="最新首选",
            ),
            QuestionClassificationCandidate(
                run_id=latest_run.id,
                knowledge_node_id=old_primary.id,
                role="primary",
                rank=2,
                confidence=Decimal("0.8"),
                rationale="最新备选",
            ),
            QuestionClassificationCandidate(
                run_id=latest_run.id,
                knowledge_node_id=alternate_primary.id,
                role="related",
                rank=1,
                confidence=Decimal("0.7"),
                rationale="与主项重复",
            ),
            QuestionClassificationCandidate(
                run_id=latest_run.id,
                knowledge_node_id=related_node.id,
                role="related",
                rank=2,
                confidence=Decimal("0.6"),
                rationale="有效关联",
            ),
        ]
    )
    await session.commit()

    response = await client.post(
        f"/api/v1/admin/import-batches/{batch.id}/approve-items",
        json={"item_ids": [str(items[0].id)]},
    )

    assert response.status_code == 200, response.text
    item_response = await client.get(f"/api/v1/admin/import-items/{items[0].id}")
    selections = item_response.json()["selections"]
    assert [value["node_code"] for value in selections if value["role"] == "primary"] == [
        alternate_primary.code
    ]
    assert [value["node_code"] for value in selections if value["role"] == "related"] == [
        related_node.code
    ]


async def test_bulk_approval_rejects_items_from_another_batch_atomically(
    client: AsyncClient, session: AsyncSession
) -> None:
    batch, items, _primary_node, _related_node = await _create_review_batch(
        session, item_count=1
    )
    _other_batch, other_items, _other_primary, _other_related = await _create_review_batch(
        session, item_count=1
    )
    item_ids = [items[0].id, other_items[0].id]

    response = await client.post(
        f"/api/v1/admin/import-batches/{batch.id}/approve-items",
        json={"item_ids": [str(item_id) for item_id in item_ids]},
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["items"][0]["reason"] == "题目不属于当前批次"
    statuses = list(
        (
            await session.scalars(
                select(QuestionImportItem.status).where(
                    QuestionImportItem.id.in_(item_ids)
                )
            )
        ).all()
    )
    assert statuses == ["needs_review", "needs_review"]


async def test_batch_admin_page_keeps_legacy_questions_readable(
    client: AsyncClient, session: AsyncSession
) -> None:
    batch, items, primary_node, related_node = await _create_review_batch(session)

    response = await client.get(f"/admin/imports/{batch.id}")

    assert response.status_code == 200
    assert "历史 PDF/DOCX 导入" in response.text
    assert "已提取小问" in response.text
    assert "第 1 题" in response.text


async def test_retired_parse_controls_are_absent_from_history(
    client: AsyncClient, session: AsyncSession
) -> None:
    batch, _items, _primary, _related = await _create_review_batch(session)
    batch.validation_summary = {}
    batch.status = "sectioned"
    await session.commit()
    response = await client.get(f"/admin/imports/{batch.id}")
    assert response.status_code == 200
    assert "历史 PDF/DOCX 导入" in response.text
    assert "解析全部" not in response.text


async def test_parse_reuses_ten_legacy_question_pages_and_combines_new_pages(
    session: AsyncSession, tmp_path: Path
) -> None:
    subject, release = await _subject_and_release(session)
    batch = QuestionImportBatch(
        subject_id=subject.id, taxonomy_release_id=release.id, year=2023,
        period="first_half", batch_code=f"reuse-{uuid.uuid4()}", title="复用测试",
        status="sectioned",
    )
    session.add(batch)
    await session.flush()
    document = QuestionSourceDocument(
        batch_id=batch.id, role="combined", original_name="inline.pdf",
        mime_type="application/pdf", file_size=100, sha256="a" * 64,
        storage_path="inline.pdf", page_count=12,
        page_selections={"questions": [{"start": 1, "end": 12}],
                         "answers": [{"start": 1, "end": 12}]},
        status="rendered",
    )
    session.add(document)
    await session.flush()
    ai = FakeAI()
    image = tmp_path / "inline.png"
    Image.new("RGB", (100, 100), "white").save(image)
    _, question_raw = await ai.parse_question_page(image, 1)
    ai.calls.clear()
    session.add_all([
        QuestionSourcePageExtraction(
            document_id=document.id, page_no=page_no, role="questions",
            provider="fake", model="fake-vision", prompt_version=VISION_PROMPT_VERSION,
            status="completed", raw_response=question_raw,
        ) for page_no in range(1, 11)
    ])
    await session.commit()
    storage = FakeRenderedStorage(image)
    first = await parse_batch(session, batch.id, ai_client=ai, storage=storage, limit=5)  # type: ignore[arg-type]
    assert first.processed_pages == 5
    assert first.completed_pages == 5
    assert first.completed == 15
    assert first.pending_pages == 7
    assert ai.calls == [("answers", page_no) for page_no in range(1, 6)]
    await parse_batch(session, batch.id, ai_client=ai, storage=storage, limit=5)  # type: ignore[arg-type]
    final = await parse_batch(session, batch.id, ai_client=ai, storage=storage, limit=2)  # type: ignore[arg-type]
    assert final.completed_pages == 12
    assert final.completed == 24
    assert final.batch_status == "in_review"
    assert ai.calls == [*(('answers', page_no) for page_no in range(1, 11)),
                        ('combined', 11), ('combined', 12)]
    rows = list((await session.scalars(
        select(QuestionSourcePageExtraction).where(QuestionSourcePageExtraction.document_id == document.id)
    )).all())
    assert len(rows) == 24
    assert {row.prompt_version for row in rows if row.page_no == 11} == {COMBINED_PROMPT_VERSION}
    assert len((await session.scalars(select(QuestionImportItem).where(QuestionImportItem.batch_id == batch.id))).all()) == 75


async def test_parse_continues_after_page_failure_and_retries_only_failed_page(
    session: AsyncSession, tmp_path: Path
) -> None:
    class FailingOnceAI(FakeAI):
        fail = True

        async def parse_combined_page(self, image_path: Path, page_no: int) -> tuple[VisionCombinedPage, dict]:
            if page_no == 2 and self.fail:
                self.fail = False
                self.calls.append(("combined", page_no))
                raise ValueError("第 2 页模型输出缺少答案")
            return await super().parse_combined_page(image_path, page_no)

    subject, release = await _subject_and_release(session)
    batch = QuestionImportBatch(
        subject_id=subject.id, taxonomy_release_id=release.id, year=2023,
        period="first_half", batch_code=f"retry-{uuid.uuid4()}", title="失败续跑测试",
        status="sectioned",
    )
    session.add(batch)
    await session.flush()
    document = QuestionSourceDocument(
        batch_id=batch.id, role="combined", original_name="mixed.pdf",
        mime_type="application/pdf", file_size=100, sha256="b" * 64,
        storage_path="mixed.pdf", page_count=3,
        page_selections={"questions": [{"start": 1, "end": 3}],
                         "answers": [{"start": 2, "end": 3}]},
        status="rendered",
    )
    session.add(document)
    await session.commit()
    image = tmp_path / "mixed.png"
    Image.new("RGB", (100, 100), "white").save(image)
    storage = FakeRenderedStorage(image)
    ai = FailingOnceAI()
    first = await parse_batch(session, batch.id, ai_client=ai, storage=storage, limit=3)  # type: ignore[arg-type]
    assert first.processed_pages == 3
    assert first.completed_pages == 2
    assert first.failed_pages == 1
    assert first.pending_pages == 0
    assert "缺少答案" in first.failed_page_details[0]["reason"]
    assert ai.calls == [("questions", 1), ("combined", 2), ("combined", 3)]
    skipped = await parse_batch(session, batch.id, ai_client=ai, storage=storage, limit=5, retry_failed=False)  # type: ignore[arg-type]
    assert skipped.processed_pages == 0
    assert ai.calls == [("questions", 1), ("combined", 2), ("combined", 3)]
    retried = await parse_batch(session, batch.id, ai_client=ai, storage=storage, limit=5)  # type: ignore[arg-type]
    assert retried.processed_pages == 1
    assert retried.completed_pages == 3
    assert retried.failed_pages == 0
    assert retried.batch_status == "in_review"
    assert ai.calls[-1] == ("combined", 2)


async def test_conflicting_repeated_answer_is_blocked_for_manual_review(
    session: AsyncSession, tmp_path: Path
) -> None:
    class ConflictingAI(FakeAI):
        async def parse_answer_page(self, image_path: Path, page_no: int) -> tuple[VisionAnswerPage, dict]:
            parsed, _ = await super().parse_answer_page(image_path, page_no)
            if page_no == 2:
                parsed.answers[0].correct_option_keys = ["B"]
            return parsed, parsed.model_dump(mode="json")

    subject, release = await _subject_and_release(session)
    batch = QuestionImportBatch(
        subject_id=subject.id, taxonomy_release_id=release.id, year=2023,
        period="first_half", batch_code=f"conflict-{uuid.uuid4()}", title="答案冲突测试",
        status="sectioned",
    )
    session.add(batch)
    await session.flush()
    document = QuestionSourceDocument(
        batch_id=batch.id, role="combined", original_name="conflict.pdf",
        mime_type="application/pdf", file_size=100, sha256="c" * 64,
        storage_path="conflict.pdf", page_count=2,
        page_selections={"questions": [{"start": 1, "end": 1}],
                         "answers": [{"start": 1, "end": 2}]},
        status="rendered",
    )
    session.add(document)
    await session.commit()
    image = tmp_path / "conflict.png"
    Image.new("RGB", (100, 100), "white").save(image)
    result = await parse_batch(
        session, batch.id, ai_client=ConflictingAI(),
        storage=FakeRenderedStorage(image), limit=2,  # type: ignore[arg-type]
    )
    assert result.completed_pages == 2
    item = await session.scalar(select(QuestionImportItem).where(
        QuestionImportItem.batch_id == batch.id, QuestionImportItem.question_no == 1
    ))
    assert item is not None and item.status == "blocked"
    assert item.correct_option_keys == ["A"]
    assert any(issue["code"] == "conflicting_answers" for issue in item.validation_issues)


async def test_retired_page_endpoints(client: AsyncClient) -> None:
    batch_id = uuid.uuid4()
    for action in ["suggest-sections", "parse", "repair", "reconcile"]:
        response = await client.post(f"/api/v1/admin/import-batches/{batch_id}/{action}")
        assert response.status_code == 410
    response = await client.put(f"/api/v1/admin/import-batches/{batch_id}/sections", json={})
    assert response.status_code == 410


async def test_page_suggestion_rechecks_cover_and_last_question_page(
    client: AsyncClient, session: AsyncSession, tmp_path: Path
) -> None:
    class BoundaryAI(FakeAI):
        async def classify_page_roles(self, pages: list[tuple[int, Path]]) -> VisionPageRoles:
            if len(pages) > 1:
                return VisionPageRoles(pages=[
                    VisionPageRole(page_no=1, questions=True, answers=False),
                    VisionPageRole(page_no=2, questions=True, answers=True),
                    VisionPageRole(page_no=3, questions=False, answers=False),
                ])
            number = pages[0][0]
            return VisionPageRoles(pages=[
                VisionPageRole(page_no=number, questions=number != 1, answers=number != 1)
            ])

    storage = LocalImportStorage(tmp_path / "boundary-imports", render_dpi=72)
    subject, release = await _subject_and_release(session)
    batch = QuestionImportBatch(subject_id=subject.id, taxonomy_release_id=release.id,
        year=2023, period="first_half", batch_code="boundary-test", title="Boundary test", status="uploaded")
    session.add(batch)
    await session.flush()
    document = QuestionSourceDocument(batch_id=batch.id, role="combined", original_name="boundary.pdf",
        mime_type="application/pdf", file_size=100, sha256="f" * 64,
        storage_path=f"{batch.id}/source/boundary.pdf", page_count=3, status="rendered")
    session.add(document)
    await session.flush()
    for page_no in range(1, 4):
        image = storage.resolve(f"{batch.id}/pages/{document.id}/page-{page_no:04d}.png")
        image.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (100, 100), "white").save(image)
    result = await suggest_document_sections(session, batch.id, ai_client=BoundaryAI(), storage=storage)
    suggestion = result.documents[0].section_suggestion
    assert suggestion["status"] == "completed"
    assert suggestion["questions"] == [{"start": 2, "end": 3}]
    assert suggestion["answers"] == [{"start": 2, "end": 3}]


async def test_page_suggestion_rechecks_gaps_inside_question_span(
    session: AsyncSession, tmp_path: Path
) -> None:
    class GapAI(FakeAI):
        async def classify_page_roles(self, pages: list[tuple[int, Path]]) -> VisionPageRoles:
            if len(pages) > 1:
                return VisionPageRoles(pages=[
                    VisionPageRole(page_no=number, questions=number != 3, answers=True)
                    for number, _ in pages
                ])
            number = pages[0][0]
            return VisionPageRoles(pages=[
                VisionPageRole(page_no=number, questions=number != 3, answers=True)
            ])

    subject, release = await _subject_and_release(session)
    batch = QuestionImportBatch(
        subject_id=subject.id, taxonomy_release_id=release.id,
        year=2023, period="first_half", batch_code="gap-test", title="Gap test", status="uploaded",
    )
    session.add(batch)
    await session.flush()
    document = QuestionSourceDocument(
        batch_id=batch.id, role="combined", original_name="gap.pdf",
        mime_type="application/pdf", file_size=100, sha256="f" * 64,
        storage_path=f"{batch.id}/source/gap.pdf", page_count=4, status="rendered",
    )
    session.add(document)
    await session.commit()
    image_path = tmp_path / "gap.png"
    Image.new("RGB", (100, 100), "white").save(image_path)
    result = await suggest_document_sections(
        session, batch.id, ai_client=GapAI(),
        storage=FakeRenderedStorage(image_path),  # type: ignore[arg-type]
    )
    assert result.documents[0].section_suggestion["questions"] == [{"start": 1, "end": 4}]
    assert result.documents[0].section_suggestion["inferred_question_pages"] == [3]


async def test_published_batch_cannot_be_edited_or_deleted(
    client: AsyncClient, session: AsyncSession
) -> None:
    batch, _items, _primary, _related = await _create_review_batch(session, item_count=1)
    batch_id = batch.id
    batch.status = "published"
    await session.commit()
    removed = await client.delete(f"/api/v1/admin/import-batches/{batch_id}")
    assert removed.status_code == 409
    changed = await client.patch(
        f"/api/v1/admin/import-batches/{batch_id}",
        json={"year": 2024, "period": "first_half", "title": "Changed"},
    )
    assert changed.status_code == 409
    assert await session.get(QuestionImportBatch, batch_id) is not None


async def test_recorded_stage_failures_are_visible_by_page_and_question(
    client: AsyncClient, session: AsyncSession
) -> None:
    batch, items, _primary, _related = await _create_review_batch(session, item_count=1)
    batch.validation_summary = {"is_valid": False}
    document = QuestionSourceDocument(
        batch_id=batch.id, role="combined", original_name="failed.pdf",
        mime_type="application/pdf", file_size=100, sha256="d" * 64,
        storage_path=f"{batch.id}/source/failed.pdf", page_count=2,
        status="rendered", error_summary=None,
    )
    session.add(document)
    await session.flush()
    session.add(QuestionSourcePageExtraction(
        document_id=document.id, page_no=2, role="answers", provider="fake",
        model="fake", prompt_version="test", status="failed", error_summary="答案页无法读取",
    ))
    session.add(QuestionImportRepairRun(
        batch_id=batch.id, document_id=document.id, role="questions",
        page_numbers=[1, 2], question_numbers=[1], attempt=1, provider="fake",
        model="fake", prompt_version="test", status="failed", error_summary="跨页修复失败",
    ))
    release = await session.get(KnowledgeTaxonomyRelease, batch.taxonomy_release_id)
    session.add(QuestionClassificationRun(
        import_item_id=items[0].id, taxonomy_release_id=release.id,
        provider="fake", model="fake", prompt_version="test-failure",
        catalog_checksum="c" * 64, status="failed", error_summary="候选知识点无效",
        created_at=datetime.now(timezone.utc) + timedelta(seconds=1),
    ))
    await session.commit()
    response = await client.get(f"/admin/imports/{batch.id}")
    assert response.status_code == 200
    assert "第 2 页" in response.text and "答案页无法读取" in response.text
    assert "跨页修复失败" in response.text and "候选知识点无效" in response.text


async def test_delete_reports_file_cleanup_failure_after_database_deletion(
    client: AsyncClient, session: AsyncSession, tmp_path: Path
) -> None:
    batch, _items, _primary, _related = await _create_review_batch(session, item_count=1)
    batch_id = batch.id

    class FailingCleanupStorage(LocalImportStorage):
        def remove_batch(self, _batch_id: uuid.UUID) -> None:
            raise OSError("文件被占用")

    app.dependency_overrides[get_import_storage] = lambda: FailingCleanupStorage(tmp_path / "imports")
    try:
        response = await client.delete(f"/api/v1/admin/import-batches/{batch_id}")
        assert response.status_code == 200, response.text
        assert response.json()["deleted"] is True
        assert "文件被占用" in response.json()["warning"]
        assert (await client.get(f"/api/v1/admin/import-batches/{batch_id}")).status_code == 404
    finally:
        app.dependency_overrides.pop(get_import_storage, None)


async def test_delete_unpublished_batch_removes_cropped_assets_and_usages(
    client: AsyncClient, session: AsyncSession, tmp_path: Path
) -> None:
    batch, items, _primary, _related = await _create_review_batch(session, item_count=1)
    batch_id = batch.id
    document = QuestionSourceDocument(
        batch_id=batch_id, role="combined", original_name="asset.pdf",
        mime_type="application/pdf", file_size=100, sha256="e" * 64,
        storage_path=f"{batch_id}/source/asset.pdf", page_count=1, status="rendered",
        page_selections={"questions": [{"start": 1, "end": 1}], "answers": [{"start": 1, "end": 1}]},
    )
    session.add(document)
    await session.flush()
    storage = LocalImportStorage(tmp_path / "asset-imports")
    page_image = storage.resolve(f"{batch_id}/pages/{document.id}/page-0001.png")
    page_image.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (100, 100), "white").save(page_image)
    asset = await create_question_asset(
        session, items[0].id,
        AssetCropCreate(
            document_id=document.id, page_no=1,
            bbox=BoundingBox(x0=0.1, y0=0.1, x1=0.9, y1=0.9),
            kind="figure", placement="stem", alt_text="crop",
        ), storage,
    )
    asset_id = asset.id
    await session.commit()
    app.dependency_overrides[get_import_storage] = lambda: storage
    try:
        response = await client.delete(f"/api/v1/admin/import-batches/{batch_id}")
        assert response.status_code == 200, response.text
        assert not storage.resolve(str(batch_id)).exists()
        session.expire_all()
        assert await session.get(QuestionAsset, asset_id) is None
        assert await session.scalar(
            select(func.count(QuestionAssetUsage.id)).where(QuestionAssetUsage.asset_id == asset_id)
        ) == 0
    finally:
        app.dependency_overrides.pop(get_import_storage, None)
