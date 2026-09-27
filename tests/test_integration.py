from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge.sync import sync_catalog
from app.models import (
    ExamPaper,
    ExamSubject,
    KnowledgeExamAggregate,
    KnowledgeNode,
    KnowledgeTaxonomyRelease,
    Question,
    QuestionKnowledgeAssignment,
    QuestionOption,
)
from app.services import DomainValidationError, add_knowledge_assignment, recompute_paper_aggregates

pytestmark = pytest.mark.integration


async def _subject(session: AsyncSession) -> ExamSubject:
    subject = await session.scalar(
        select(ExamSubject).where(ExamSubject.code == "software-designer.foundation")
    )
    assert subject is not None
    return subject


async def _node(session: AsyncSession, code: str) -> KnowledgeNode:
    node = await session.scalar(select(KnowledgeNode).where(KnowledgeNode.code == code))
    assert node is not None
    return node


async def test_taxonomy_sync_is_idempotent(session: AsyncSession) -> None:
    before = {
        code: node_id
        for code, node_id in (
            await session.execute(select(KnowledgeNode.code, KnowledgeNode.id))
        ).all()
    }
    result = await sync_catalog(session)
    after = {
        code: node_id
        for code, node_id in (
            await session.execute(select(KnowledgeNode.code, KnowledgeNode.id))
        ).all()
    }
    release_count = len((await session.scalars(select(KnowledgeTaxonomyRelease))).all())

    assert result["created"] == 0
    assert result["updated"] == 307
    assert before == after
    assert release_count == 1


async def test_tree_and_node_api(client: AsyncClient) -> None:
    tree_response = await client.get(
        "/api/v1/subjects/software-designer.foundation/knowledge-tree"
    )
    assert tree_response.status_code == 200
    tree = tree_response.json()
    assert len(tree) == 14
    os_chapter = next(node for node in tree if node["code"] == "os")
    assert any(module["code"] == "os.process" for module in os_chapter["children"])

    node_response = await client.get(
        "/api/v1/subjects/software-designer.foundation/knowledge-nodes/os.process.sync"
    )
    assert node_response.status_code == 200
    detail = node_response.json()
    assert [item["code"] for item in detail["ancestors"]] == ["os", "os.process"]
    assert detail["node_type"] == "topic"


async def test_assignment_constraints_and_leaf_validation(session: AsyncSession) -> None:
    subject = await _subject(session)
    sync_node = await _node(session, "os.process.sync")
    deadlock_node = await _node(session, "os.process.deadlock")
    module_node = await _node(session, "os.process")
    question = Question(
        subject_id=subject.id,
        question_type="single_choice",
        stem_markdown="测试题",
        score=Decimal("1"),
        source_type="practice",
        content_hash="a" * 64,
        status="published",
    )
    session.add(question)
    await session.flush()

    await add_knowledge_assignment(
        session,
        question_id=question.id,
        knowledge_node_id=sync_node.id,
        role="primary",
        status="confirmed",
        source="manual",
    )
    await add_knowledge_assignment(
        session,
        question_id=question.id,
        knowledge_node_id=deadlock_node.id,
        role="related",
        status="confirmed",
        source="manual",
    )
    await add_knowledge_assignment(
        session,
        question_id=question.id,
        knowledge_node_id=deadlock_node.id,
        role="primary",
        status="proposed",
        source="ai",
        confidence=Decimal("0.65"),
        candidate_rank=1,
    )
    with pytest.raises(DomainValidationError):
        await add_knowledge_assignment(
            session,
            question_id=question.id,
            knowledge_node_id=module_node.id,
            role="related",
            status="confirmed",
            source="manual",
        )

    session.add(
        QuestionKnowledgeAssignment(
            subject_id=subject.id,
            question_id=question.id,
            knowledge_node_id=deadlock_node.id,
            role="primary",
            status="confirmed",
            source="manual",
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_option_uniqueness_and_aggregate_rollup(session: AsyncSession) -> None:
    subject = await _subject(session)
    primary_node = await _node(session, "os.process.sync")
    related_node = await _node(session, "os.process.deadlock")
    paper = ExamPaper(
        subject_id=subject.id,
        year=2026,
        period="first_half",
        batch_code="test",
        title="聚合测试卷",
        status="verified",
        total_score=Decimal("75"),
    )
    session.add(paper)
    await session.flush()
    question = Question(
        subject_id=subject.id,
        paper_id=paper.id,
        question_no=1,
        question_type="single_choice",
        stem_markdown="同步互斥测试题",
        score=Decimal("2"),
        source_type="official",
        content_hash="b" * 64,
        status="published",
    )
    session.add(question)
    await session.flush()
    session.add_all(
        [
            QuestionOption(question_id=question.id, option_key="A", content_markdown="A", sort_order=1),
            QuestionOption(question_id=question.id, option_key="B", content_markdown="B", sort_order=2),
        ]
    )
    await add_knowledge_assignment(
        session,
        question_id=question.id,
        knowledge_node_id=primary_node.id,
        role="primary",
        status="confirmed",
        source="import",
    )
    await add_knowledge_assignment(
        session,
        question_id=question.id,
        knowledge_node_id=related_node.id,
        role="related",
        status="confirmed",
        source="import",
    )
    aggregates = await recompute_paper_aggregates(session, paper.id)
    by_node = {item.knowledge_node_id: item for item in aggregates}
    process_module = await _node(session, "os.process")
    os_chapter = await _node(session, "os")

    assert by_node[primary_node.id].primary_score_total == Decimal("2")
    assert by_node[related_node.id].primary_score_total == Decimal("0")
    assert by_node[process_module.id].primary_score_total == Decimal("2")
    assert by_node[process_module.id].related_question_count == 1
    assert by_node[os_chapter.id].primary_score_total == Decimal("2")
    assert by_node[os_chapter.id].related_question_count == 1
    persisted = list(
        (
            await session.scalars(
                select(KnowledgeExamAggregate).where(
                    KnowledgeExamAggregate.exam_paper_id == paper.id
                )
            )
        ).all()
    )
    assert len(persisted) == len(aggregates)
