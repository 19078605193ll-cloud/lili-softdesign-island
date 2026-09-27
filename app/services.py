from __future__ import annotations

import uuid
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ExamPaper,
    KnowledgeExamAggregate,
    KnowledgeNode,
    Question,
    QuestionKnowledgeAssignment,
)


class DomainValidationError(ValueError):
    pass


async def add_knowledge_assignment(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
    knowledge_node_id: uuid.UUID,
    role: str,
    status: str,
    source: str,
    confidence: Decimal | None = None,
    candidate_rank: int | None = None,
    rationale: str | None = None,
    taxonomy_release_id: uuid.UUID | None = None,
) -> QuestionKnowledgeAssignment:
    question = await session.get(Question, question_id)
    node = await session.get(KnowledgeNode, knowledge_node_id)
    if question is None:
        raise DomainValidationError("question does not exist")
    if node is None:
        raise DomainValidationError("knowledge node does not exist")
    if question.subject_id != node.subject_id:
        raise DomainValidationError("question and knowledge node belong to different subjects")
    if status == "confirmed" and (node.node_type != "topic" or node.status != "active"):
        raise DomainValidationError("confirmed assignments must target an active topic node")

    assignment = QuestionKnowledgeAssignment(
        subject_id=question.subject_id,
        question_id=question.id,
        knowledge_node_id=node.id,
        taxonomy_release_id=taxonomy_release_id,
        role=role,
        status=status,
        source=source,
        confidence=confidence,
        candidate_rank=candidate_rank,
        rationale=rationale,
    )
    session.add(assignment)
    await session.flush()
    return assignment


async def recompute_paper_aggregates(
    session: AsyncSession, paper_id: uuid.UUID, *, calculation_version: str = "v1"
) -> list[KnowledgeExamAggregate]:
    paper = await session.get(ExamPaper, paper_id)
    if paper is None:
        raise DomainValidationError("exam paper does not exist")

    nodes = list(
        (
            await session.scalars(
                select(KnowledgeNode).where(KnowledgeNode.subject_id == paper.subject_id)
            )
        ).all()
    )
    parent_by_node = {node.id: node.parent_id for node in nodes}

    rows = (
        await session.execute(
            select(
                Question.id,
                Question.score,
                QuestionKnowledgeAssignment.knowledge_node_id,
                QuestionKnowledgeAssignment.role,
            )
            .join(
                QuestionKnowledgeAssignment,
                QuestionKnowledgeAssignment.question_id == Question.id,
            )
            .where(
                Question.paper_id == paper.id,
                Question.status == "published",
                QuestionKnowledgeAssignment.status == "confirmed",
            )
        )
    ).all()

    primary: dict[uuid.UUID, dict[uuid.UUID, Decimal]] = defaultdict(dict)
    related: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)

    def lineage(node_id: uuid.UUID) -> list[uuid.UUID]:
        result: list[uuid.UUID] = []
        cursor: uuid.UUID | None = node_id
        visited: set[uuid.UUID] = set()
        while cursor is not None:
            if cursor in visited:
                raise DomainValidationError("cycle detected in knowledge tree")
            visited.add(cursor)
            result.append(cursor)
            cursor = parent_by_node.get(cursor)
        return result

    for question_id, score, node_id, role in rows:
        for ancestor_id in lineage(node_id):
            if role == "primary":
                primary[ancestor_id][question_id] = score
            else:
                related[ancestor_id].add(question_id)

    await session.execute(
        delete(KnowledgeExamAggregate).where(KnowledgeExamAggregate.exam_paper_id == paper.id)
    )
    aggregates: list[KnowledgeExamAggregate] = []
    for node_id in primary.keys() | related.keys():
        aggregate = KnowledgeExamAggregate(
            subject_id=paper.subject_id,
            knowledge_node_id=node_id,
            exam_paper_id=paper.id,
            primary_question_count=len(primary[node_id]),
            related_question_count=len(related[node_id]),
            primary_score_total=sum(primary[node_id].values(), Decimal("0")),
            calculation_version=calculation_version,
        )
        session.add(aggregate)
        aggregates.append(aggregate)
    await session.flush()
    return aggregates

