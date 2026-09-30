from typing import Literal
from pydantic import BaseModel


class OptionRead(BaseModel):
    key: str
    content_markdown: str
    content_html: str = ''


class Locator(BaseModel):
    question_no: int
    label: str
    marker: str | None


class PartRead(BaseModel):
    id: str
    question_no: int
    stem_markdown: str
    stem_html: str = ''
    options: list[OptionRead]
    score: str
    locator: Locator


class SourceRead(BaseModel):
    paper_id: str
    year: int
    period: str
    batch_code: str
    title: str
    source_type: str
    label: str


class QuestionRead(BaseModel):
    id: str
    content_version: int
    type: Literal["composite", "single_choice"]
    material_markdown: str
    material_html: str = ''
    parts: list[PartRead]
    subquestion_count: int
    source: SourceRead


class QuestionPage(BaseModel):
    items: list[QuestionRead]
    next_cursor: str | None


class SolutionPart(BaseModel):
    id: str
    correct_option_keys: list[str]
    explanation_markdown: str
    explanation_html: str = ''


class SolutionRead(BaseModel):
    id: str
    content_version: int
    explanation_markdown: str
    explanation_html: str = ''
    parts: list[SolutionPart]
