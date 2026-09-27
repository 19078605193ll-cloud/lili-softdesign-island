from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PageRange(BaseModel):
    start: int = Field(ge=1)
    end: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_order(self) -> PageRange:
        if self.end < self.start:
            raise ValueError("page range end must be greater than or equal to start")
        return self

    def pages(self) -> list[int]:
        return list(range(self.start, self.end + 1))


class SourceSectionSelection(BaseModel):
    questions: list[PageRange] = Field(min_length=1)
    answers: list[PageRange] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_ranges(self) -> SourceSectionSelection:
        question_pages = [page for value in self.questions for page in value.pages()]
        answer_pages = [page for value in self.answers for page in value.pages()]
        if len(question_pages) != len(set(question_pages)):
            raise ValueError("question page ranges overlap")
        if len(answer_pages) != len(set(answer_pages)):
            raise ValueError("answer page ranges overlap")
        return self

    def as_json(self) -> dict[str, list[dict[str, int]]]:
        return {
            "questions": [value.model_dump() for value in self.questions],
            "answers": [value.model_dump() for value in self.answers],
        }


class BoundingBox(BaseModel):
    x0: float = Field(ge=0, le=1)
    y0: float = Field(ge=0, le=1)
    x1: float = Field(ge=0, le=1)
    y1: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_area(self) -> BoundingBox:
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("crop must have positive width and height")
        return self


class ImportOption(BaseModel):
    key: str = Field(min_length=1, max_length=10)
    content_markdown: str = ""

    @field_validator("key")
    @classmethod
    def normalize_key(cls, value: str) -> str:
        return value.strip().upper()


class SourceReference(BaseModel):
    document_id: uuid.UUID
    page_no: int = Field(ge=1)
    role: Literal["questions", "answers"]
    bbox: BoundingBox | None = None


class ValidationIssue(BaseModel):
    code: str
    severity: Literal["error", "warning"]
    message: str


class ImportItemUpdate(BaseModel):
    stem_markdown: str | None = Field(default=None, min_length=1)
    options: list[ImportOption] | None = None
    correct_option_keys: list[str] | None = None
    explanation_markdown: str | None = None
    score: Decimal | None = Field(default=None, ge=0)
    review_note: str | None = None

    @field_validator("correct_option_keys")
    @classmethod
    def normalize_answer_keys(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = [key.strip().upper() for key in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("correct option keys must be unique")
        return normalized


class ImportGroupUpdate(BaseModel):
    material_markdown: str = ""


class ImportGroupRead(BaseModel):
    id: uuid.UUID
    batch_id: uuid.UUID
    source_label: str
    material_markdown: str
    source_refs: list[dict]
    sort_order: int


class KnowledgeSelectionInput(BaseModel):
    primary_code: str = Field(min_length=1)
    related_codes: list[str] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def validate_codes(self) -> KnowledgeSelectionInput:
        if self.primary_code in self.related_codes:
            raise ValueError("primary knowledge node cannot also be related")
        if len(self.related_codes) != len(set(self.related_codes)):
            raise ValueError("related knowledge codes must be unique")
        return self


class BulkApproveItemsInput(BaseModel):
    item_ids: list[uuid.UUID] = Field(min_length=1)

    @field_validator("item_ids")
    @classmethod
    def validate_unique_item_ids(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(value) != len(set(value)):
            raise ValueError("item ids must be unique")
        return value


class BulkApproveItemsResult(BaseModel):
    batch_id: uuid.UUID
    approved_item_ids: list[uuid.UUID]
    approved_count: int
    batch_status: str


class RejectItemInput(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class AssetCropCreate(BaseModel):
    document_id: uuid.UUID
    page_no: int = Field(ge=1)
    bbox: BoundingBox
    kind: Literal["figure", "table", "code", "source_snapshot"]
    placement: Literal["stem", "group_material"] = "stem"
    alt_text: str = Field(default="原题素材", min_length=1, max_length=300)


class AssetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    page_no: int | None
    bbox: dict | None
    kind: str
    mime_type: str
    width: int
    height: int
    created_at: datetime


class ClassificationCandidateRead(BaseModel):
    id: uuid.UUID
    node_code: str
    node_name: str
    role: Literal["primary", "related"]
    rank: int
    confidence: Decimal
    rationale: str


class KnowledgeSelectionRead(BaseModel):
    node_code: str
    node_name: str
    role: Literal["primary", "related"]
    source: Literal["manual", "ai"]


class ImportItemRead(BaseModel):
    id: uuid.UUID
    batch_id: uuid.UUID
    group_id: uuid.UUID | None
    question_no: int
    question_type: str
    stem_markdown: str
    options: list[ImportOption]
    correct_option_keys: list[str]
    explanation_markdown: str | None
    score: Decimal
    source_refs: list[dict]
    validation_issues: list[ValidationIssue]
    status: str
    review_note: str | None
    candidates: list[ClassificationCandidateRead] = Field(default_factory=list)
    selections: list[KnowledgeSelectionRead] = Field(default_factory=list)
    published_question_id: uuid.UUID | None = None


class SourceDocumentRead(BaseModel):
    id: uuid.UUID
    role: str
    original_name: str
    mime_type: str
    file_size: int
    sha256: str
    page_count: int | None
    page_selections: dict
    section_suggestion: dict
    has_extracted_content: bool
    status: str
    error_summary: str | None


class ImportBatchRead(BaseModel):
    id: uuid.UUID
    subject_code: str
    taxonomy_version: str
    year: int
    period: str
    batch_code: str
    title: str
    exam_date: date | None
    status: str
    parser_version: str
    expected_question_count: int | None
    validation_summary: dict
    source_reference: str | None
    error_summary: str | None
    documents: list[SourceDocumentRead]
    item_counts: dict[str, int]
    extraction_counts: dict[str, int]
    parse_progress: dict[str, int] = Field(default_factory=dict)
    failures: list[dict] = Field(default_factory=list)
    published_paper_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class ImportBatchMetadataUpdate(BaseModel):
    year: int = Field(ge=2000, le=2100)
    period: Literal["first_half", "second_half", "other"]
    title: str = Field(min_length=1, max_length=300)


class VisionPageRole(BaseModel):
    page_no: int = Field(ge=1)
    questions: bool
    answers: bool


class VisionPageRoles(BaseModel):
    pages: list[VisionPageRole]


class ParseProgress(BaseModel):
    batch_id: uuid.UUID
    processed: int
    completed: int
    failed: int
    remaining: int
    batch_status: str
    total_pages: int = 0
    completed_pages: int = 0
    pending_pages: int = 0
    failed_pages: int = 0
    processed_pages: int = 0
    failed_page_details: list[dict] = Field(default_factory=list)


class RepairProgress(BaseModel):
    batch_id: uuid.UUID
    processed_runs: int
    completed_runs: int
    failed_runs: int
    remaining_question_numbers: list[int]
    batch_status: str
    validation_summary: dict


class ClassificationProgress(BaseModel):
    batch_id: uuid.UUID
    processed: int
    completed_items: int
    failed_items: int
    remaining_items: int
    batch_status: str
    eligible_items: int = 0
    blocked_items: int = 0
    results: list[dict[str, Any]] = Field(default_factory=list)


class PublishResult(BaseModel):
    batch_id: uuid.UUID
    paper_id: uuid.UUID
    question_count: int
    total_score: Decimal
    already_published: bool = False


class VisionItem(BaseModel):
    question_no: int = Field(ge=1, le=75)
    stem_markdown: str = ""
    options: list[ImportOption] = Field(default_factory=list)
    needs_visual_asset: bool = False
    visual_description: str | None = None


class VisionGroup(BaseModel):
    source_label: str
    material_markdown: str = ""
    items: list[VisionItem] = Field(min_length=1)


class VisionQuestionPage(BaseModel):
    groups: list[VisionGroup] = Field(default_factory=list)


class VisionAnswer(BaseModel):
    question_no: int = Field(ge=1, le=75)
    correct_option_keys: list[str] = Field(min_length=1)
    explanation_markdown: str | None = None

    @field_validator("correct_option_keys")
    @classmethod
    def normalize_keys(cls, value: list[str]) -> list[str]:
        return [key.strip().upper() for key in value]


class VisionAnswerPage(BaseModel):
    answers: list[VisionAnswer] = Field(default_factory=list)


class VisionCombinedPage(BaseModel):
    groups: list[VisionGroup] = Field(min_length=1)
    answers: list[VisionAnswer] = Field(min_length=1)


class AICandidate(BaseModel):
    code: str
    confidence: Decimal = Field(ge=0, le=1)
    rationale: str


class AIQuestionClassification(BaseModel):
    question_no: int
    primary_candidates: list[AICandidate] = Field(min_length=1, max_length=3)
    related_candidates: list[AICandidate] = Field(default_factory=list, max_length=3)


class AIClassificationBatch(BaseModel):
    results: list[AIQuestionClassification]
