from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

NodeStatus = Literal["active", "deprecated"]
CODE_PATTERN = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
REQUIRED_CHAPTER_CODES = {
    "cs-math",
    "architecture",
    "os",
    "data-structures-algorithms",
    "programming-languages",
    "database",
    "network",
    "software-engineering",
    "oo-uml-patterns",
    "security",
    "standards-ip",
    "multimedia",
    "new-technology",
    "professional-english",
}


class TopicDefinition(BaseModel):
    code: str
    name: str
    description: str = Field(min_length=2)
    aliases: list[str] = Field(min_length=1)
    keywords: list[str] = Field(min_length=1)
    classification_guidance: str = Field(min_length=2)
    syllabus_refs: list[str] = Field(min_length=1)
    status: NodeStatus = "active"


class ModuleDefinition(BaseModel):
    code: str
    name: str
    description: str
    status: NodeStatus = "active"
    topics: list[TopicDefinition] = Field(min_length=1)


class ChapterDefinition(BaseModel):
    code: str
    name: str
    description: str
    status: NodeStatus = "active"
    modules: list[ModuleDefinition] = Field(min_length=1)


class SubjectDefinition(BaseModel):
    code: str
    name: str
    qualification_level: str
    paper_kind: str


class TaxonomyCatalog(BaseModel):
    version: str
    source_name: str
    source_reference: str
    subject: SubjectDefinition
    chapters: list[ChapterDefinition]

    @model_validator(mode="after")
    def validate_structure(self) -> "TaxonomyCatalog":
        all_codes: set[str] = set()
        chapter_codes = {chapter.code for chapter in self.chapters}
        missing = REQUIRED_CHAPTER_CODES - chapter_codes
        if missing:
            raise ValueError(f"missing required chapters: {sorted(missing)}")

        for chapter in self.chapters:
            self._check_code(chapter.code, all_codes)
            for module in chapter.modules:
                self._check_code(module.code, all_codes)
                if not module.code.startswith(f"{chapter.code}."):
                    raise ValueError(f"module {module.code} must be under {chapter.code}")
                for topic in module.topics:
                    self._check_code(topic.code, all_codes)
                    if not topic.code.startswith(f"{module.code}."):
                        raise ValueError(f"topic {topic.code} must be under {module.code}")
        return self

    @staticmethod
    def _check_code(code: str, known: set[str]) -> None:
        if not CODE_PATTERN.fullmatch(code):
            raise ValueError(f"invalid taxonomy code: {code}")
        if code in known:
            raise ValueError(f"duplicate taxonomy code: {code}")
        known.add(code)


class FlatNode(BaseModel):
    code: str
    parent_code: str | None
    name: str
    node_type: Literal["chapter", "module", "topic"]
    description: str
    aliases: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    classification_guidance: str = ""
    syllabus_refs: list[str] = Field(default_factory=list)
    sort_order: int
    status: NodeStatus


def default_catalog_path() -> Path:
    return Path(__file__).with_name("software_designer_foundation_v1.yaml")


def load_catalog(path: Path | None = None) -> tuple[TaxonomyCatalog, str]:
    resolved = path or default_catalog_path()
    raw = resolved.read_bytes()
    catalog = TaxonomyCatalog.model_validate(yaml.safe_load(raw))
    return catalog, hashlib.sha256(raw).hexdigest()


def flatten_catalog(catalog: TaxonomyCatalog) -> list[FlatNode]:
    result: list[FlatNode] = []
    for chapter_order, chapter in enumerate(catalog.chapters, 1):
        result.append(
            FlatNode(
                code=chapter.code,
                parent_code=None,
                name=chapter.name,
                node_type="chapter",
                description=chapter.description,
                sort_order=chapter_order,
                status=chapter.status,
            )
        )
        for module_order, module in enumerate(chapter.modules, 1):
            result.append(
                FlatNode(
                    code=module.code,
                    parent_code=chapter.code,
                    name=module.name,
                    node_type="module",
                    description=module.description,
                    sort_order=module_order,
                    status=module.status,
                )
            )
            for topic_order, topic in enumerate(module.topics, 1):
                result.append(
                    FlatNode(
                        code=topic.code,
                        parent_code=module.code,
                        name=topic.name,
                        node_type="topic",
                        description=topic.description,
                        aliases=topic.aliases,
                        keywords=topic.keywords,
                        classification_guidance=topic.classification_guidance,
                        syllabus_refs=topic.syllabus_refs,
                        sort_order=topic_order,
                        status=topic.status,
                    )
                )
    return result

