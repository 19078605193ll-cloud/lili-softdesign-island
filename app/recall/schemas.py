import re
import unicodedata
import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Token(Strict):
    text: str | None = Field(None, max_length=2000)
    blank_key: str | None = Field(None, pattern=r"^[a-zA-Z0-9_-]{1,80}$")

    @model_validator(mode="after")
    def one_value(self):
        if (self.text is None) == (self.blank_key is None):
            raise ValueError("文字和空位必须且只能选择一个")
        return self


TableCell = Token | Annotated[list[Token], Field(min_length=1, max_length=50)]


class TableContent(Strict):
    type: Literal["table"]
    columns: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        min_length=2, max_length=12
    )
    rows: list[list[TableCell]] = Field(min_length=1, max_length=50)
    note: str = Field("", max_length=4000)

    @model_validator(mode="after")
    def rectangular(self):
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("每行单元格数量必须与列数一致")
        for row in self.rows:
            for cell in row:
                tokens = cell if isinstance(cell, list) else [cell]
                if not any(token.blank_key or (token.text and token.text.strip()) for token in tokens):
                    raise ValueError("表格单元格不能为空")
        return self


class SentenceContent(Strict):
    type: Literal["sentence"]
    paragraphs: list[list[Token]] = Field(min_length=1, max_length=50)
    note: str = Field("", max_length=4000)


class RelationNode(Strict):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    label: Token
    x: int = Field(ge=0, le=1600)
    y: int = Field(ge=0, le=1600)


class RelationEdge(Strict):
    source: str
    target: str
    label: Token


class RelationContent(Strict):
    type: Literal["relation"]
    nodes: list[RelationNode] = Field(min_length=2, max_length=40)
    edges: list[RelationEdge] = Field(min_length=1, max_length=60)
    steps: list[list[Token]] = Field(default_factory=list, max_length=40)
    note: str = Field("", max_length=4000)

    @model_validator(mode="after")
    def valid_graph(self):
        ids = {n.id for n in self.nodes}
        if len(ids) != len(self.nodes) or any(
            e.source not in ids or e.target not in ids for e in self.edges
        ):
            raise ValueError("节点 ID 重复或连线指向不存在的节点")
        return self


Content = Annotated[
    TableContent | SentenceContent | RelationContent, Field(discriminator="type")
]


class Normalization(Strict):
    case_sensitive: bool = True
    whitespace: Literal["preserve", "collapse", "remove"] = "preserve"
    aliases: dict[str, str] = Field(default_factory=dict, max_length=30)
    unordered_set: bool = False
    separator: str = Field(",", min_length=1, max_length=1)


def normalize(value: str, rules: Normalization) -> str:
    # Fold width only: broad NFKC would incorrectly turn n² into n2.
    value = "".join(
        unicodedata.normalize("NFKC", c)
        if c == "\u3000" or 0xFF01 <= ord(c) <= 0xFF5E
        else c
        for c in value
    )
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    for old in sorted(rules.aliases, key=len, reverse=True):
        if old:
            value = value.replace(old, rules.aliases[old])
    if rules.whitespace == "collapse":
        value = re.sub(r"\s+", " ", value)
    elif rules.whitespace == "remove":
        value = re.sub(r"\s+", "", value)
    if not rules.case_sensitive:
        value = value.casefold()
    if rules.unordered_set:
        value = rules.separator.join(
            sorted({v.strip() for v in value.split(rules.separator)})
        )
    return value


class BlankIn(Strict):
    key: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    label: str = Field(min_length=1, max_length=200)
    answer: str = Field(min_length=1, max_length=2000)
    acceptable_answers: list[str] = Field(default_factory=list, max_length=30)
    normalization: Normalization = Field(default_factory=Normalization)
    hints: list[str] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def usable(self):
        if any(
            not x.strip() or len(x) > 2000 for x in self.hints + self.acceptable_answers
        ):
            raise ValueError("提示与可接受答案不能为空或过长")
        if not normalize(self.answer, self.normalization):
            raise ValueError("归一化后的答案不能为空")
        return self


def blank_refs(value):
    if isinstance(value, dict):
        if value.get("blank_key"):
            yield value["blank_key"]
        for item in value.values():
            yield from blank_refs(item)
    elif isinstance(value, list):
        for item in value:
            yield from blank_refs(item)


class VersionIn(Strict):
    title: str = Field(min_length=1, max_length=200)
    content: Content
    blanks: list[BlankIn] = Field(min_length=1, max_length=100)
    knowledge_node_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
    source: Literal["manual", "ai"] = "manual"
    provenance: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def references(self):
        keys = [b.key for b in self.blanks]
        refs = list(blank_refs(self.content.model_dump()))
        if (
            len(set(keys)) != len(keys)
            or set(keys) != set(refs)
            or len(refs) != len(set(refs))
        ):
            raise ValueError("每个空位必须在正文中恰好出现一次，且不能引用未定义空位")
        if len(set(self.knowledge_node_ids)) != len(self.knowledge_node_ids):
            raise ValueError("知识点不能重复")
        return self


class CreateIn(VersionIn):
    subject_id: uuid.UUID
    slug: str = Field(pattern=r"^[a-z0-9_-]{1,160}$")


class SessionIn(Strict):
    recall_id: uuid.UUID


class AnswerIn(Strict):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    revision: int = Field(ge=1)
    blank_key: str = Field(max_length=80)
    answer: str = Field(min_length=1, max_length=2000)


class DraftIn(AnswerIn):
    answer: str = Field(max_length=2000)


class RevisionIn(Strict):
    revision: int = Field(ge=1)


class ActionIn(RevisionIn):
    note: str = Field("", max_length=2000)


class GenerationTarget(Strict):
    mode: Literal["new", "new_version"]
    recall_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def target_matches_mode(self):
        if (self.mode == "new_version") != (self.recall_id is not None):
            raise ValueError("生成新版本时必须且只能指定主动回忆内容")
        return self


class GenerationItemIn(Strict):
    knowledge_node_id: uuid.UUID
    type: Literal["auto", "table", "sentence", "relation"] = "auto"
    target: GenerationTarget


class GenerationBatchIn(Strict):
    subject_id: uuid.UUID
    items: list[GenerationItemIn] = Field(min_length=1, max_length=20)
    instruction: str = Field("", max_length=2000)

    @model_validator(mode="after")
    def unique_nodes(self):
        ids = [item.knowledge_node_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("同一批次不能重复选择知识点")
        return self


class EvidenceReference(Strict):
    path: str = Field(min_length=1, max_length=300)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)


class AISupplement(Strict):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    path: str = Field(min_length=1, max_length=300)
    snippet: str = Field(min_length=1, max_length=2000)
    reason: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)


class GeneratedRecall(Strict):
    title: str = Field(min_length=1, max_length=200)
    content: Content
    blanks: list[BlankIn] = Field(min_length=1, max_length=100)
    evidence_references: list[EvidenceReference] = Field(
        default_factory=list, max_length=300
    )
    supplements: list[AISupplement] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def valid_content(self):
        VersionIn(
            title=self.title,
            content=self.content,
            blanks=self.blanks,
            knowledge_node_ids=[uuid.UUID(int=0)],
        )
        ids = [item.id for item in self.supplements]
        if len(ids) != len(set(ids)):
            raise ValueError("AI 补充内容标识不能重复")
        return self


