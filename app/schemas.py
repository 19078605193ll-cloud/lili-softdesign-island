from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.enums import NodeType, RecordStatus


class KnowledgeNodeSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    node_type: NodeType
    description: str
    sort_order: int
    status: RecordStatus


class KnowledgeTreeNode(KnowledgeNodeSummary):
    children: list[KnowledgeTreeNode] = Field(default_factory=list)


class KnowledgeNodeDetail(KnowledgeNodeSummary):
    parent_id: uuid.UUID | None
    aliases: list[str]
    keywords: list[str]
    classification_guidance: str
    syllabus_refs: list[str]
    created_at: datetime
    updated_at: datetime
    ancestors: list[KnowledgeNodeSummary] = Field(default_factory=list)
    descendants: list[KnowledgeTreeNode] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str

