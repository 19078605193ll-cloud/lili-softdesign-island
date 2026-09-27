from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import ExamSubject, KnowledgeNode
from app.schemas import KnowledgeNodeDetail, KnowledgeNodeSummary, KnowledgeTreeNode


class SubjectNotFoundError(LookupError):
    pass


class KnowledgeNodeNotFoundError(LookupError):
    pass


async def get_subject(session: AsyncSession, subject_code: str) -> ExamSubject:
    subject = await session.scalar(select(ExamSubject).where(ExamSubject.code == subject_code))
    if subject is None:
        raise SubjectNotFoundError(subject_code)
    return subject


def _build_tree(
    nodes: list[KnowledgeNode], root_parent_id: uuid.UUID | None = None, max_depth: int | None = None
) -> list[KnowledgeTreeNode]:
    grouped: dict[uuid.UUID | None, list[KnowledgeNode]] = defaultdict(list)
    for node in nodes:
        grouped[node.parent_id].append(node)
    for siblings in grouped.values():
        siblings.sort(key=lambda item: (item.sort_order, item.code))

    def build(parent_id: uuid.UUID | None, depth: int) -> list[KnowledgeTreeNode]:
        result: list[KnowledgeTreeNode] = []
        for node in grouped.get(parent_id, []):
            children = [] if max_depth is not None and depth >= max_depth else build(node.id, depth + 1)
            result.append(
                KnowledgeTreeNode(
                    **KnowledgeNodeSummary.model_validate(node).model_dump(),
                    children=children,
                )
            )
        return result

    return build(root_parent_id, 0)


async def get_knowledge_tree(
    session: AsyncSession,
    subject_code: str,
    *,
    include_deprecated: bool = False,
    max_depth: int | None = None,
) -> list[KnowledgeTreeNode]:
    subject = await get_subject(session, subject_code)
    query = select(KnowledgeNode).where(KnowledgeNode.subject_id == subject.id)
    if not include_deprecated:
        query = query.where(KnowledgeNode.status == "active")
    nodes = list((await session.scalars(query)).all())
    return _build_tree(nodes, max_depth=max_depth)


async def _get_descendants(
    session: AsyncSession, root: KnowledgeNode, include_deprecated: bool
) -> list[KnowledgeNode]:
    tree = select(KnowledgeNode.id).where(KnowledgeNode.id == root.id).cte(
        name="descendant_tree", recursive=True
    )
    child = aliased(KnowledgeNode)
    tree = tree.union_all(select(child.id).where(child.parent_id == tree.c.id))
    query = select(KnowledgeNode).join(tree, KnowledgeNode.id == tree.c.id).where(
        KnowledgeNode.id != root.id
    )
    if not include_deprecated:
        query = query.where(KnowledgeNode.status == "active")
    return list((await session.scalars(query)).all())


async def _get_ancestors(session: AsyncSession, node: KnowledgeNode) -> list[KnowledgeNode]:
    tree = select(KnowledgeNode.id, KnowledgeNode.parent_id).where(
        KnowledgeNode.id == node.id
    ).cte(name="ancestor_tree", recursive=True)
    parent = aliased(KnowledgeNode)
    tree = tree.union_all(
        select(parent.id, parent.parent_id).join(tree, parent.id == tree.c.parent_id)
    )
    ancestors = list(
        (
            await session.scalars(
                select(KnowledgeNode)
                .join(tree, KnowledgeNode.id == tree.c.id)
                .where(KnowledgeNode.id != node.id)
            )
        ).all()
    )
    by_id = {item.id: item for item in ancestors}
    ordered: list[KnowledgeNode] = []
    cursor = node.parent_id
    while cursor is not None and cursor in by_id:
        parent_node = by_id[cursor]
        ordered.append(parent_node)
        cursor = parent_node.parent_id
    ordered.reverse()
    return ordered


async def get_knowledge_node_detail(
    session: AsyncSession,
    subject_code: str,
    node_code: str,
    *,
    include_ancestors: bool = True,
    include_descendants: bool = True,
    include_deprecated: bool = False,
) -> KnowledgeNodeDetail:
    subject = await get_subject(session, subject_code)
    node = await session.scalar(
        select(KnowledgeNode).where(
            KnowledgeNode.subject_id == subject.id,
            KnowledgeNode.code == node_code,
        )
    )
    if node is None or (node.status == "deprecated" and not include_deprecated):
        raise KnowledgeNodeNotFoundError(node_code)

    ancestors = await _get_ancestors(session, node) if include_ancestors else []
    descendants = (
        await _get_descendants(session, node, include_deprecated) if include_descendants else []
    )
    descendant_tree = _build_tree(descendants, root_parent_id=node.id) if descendants else []
    return KnowledgeNodeDetail(
        **KnowledgeNodeSummary.model_validate(node).model_dump(),
        parent_id=node.parent_id,
        aliases=node.aliases,
        keywords=node.keywords,
        classification_guidance=node.classification_guidance,
        syllabus_refs=node.syllabus_refs,
        created_at=node.created_at,
        updated_at=node.updated_at,
        ancestors=[KnowledgeNodeSummary.model_validate(item) for item in ancestors],
        descendants=descendant_tree,
    )

