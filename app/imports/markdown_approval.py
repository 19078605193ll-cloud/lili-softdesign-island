"""Shared, read-only approval preview for Markdown blocks."""
import uuid

from pydantic import ValidationError
from sqlalchemy import select

from app.models import KnowledgeNode, QuestionImportGroup, QuestionImportItem, QuestionImportKnowledgeSelection
from app.imports.markdown_parser import IMAGE
from app.imports.schemas import KnowledgeSelectionInput
from app.imports.service import _load_approval_suggestions, validate_import_item


async def approval_preview(
    session,
    batch,
    block,
    *,
    group=None,
    items=None,
    suggestions=None,
    selection_rows_by_item=None,
):
    reasons = list(block.get("issues", []))
    warnings = []
    prepared = []
    knowledge = []
    if batch.status == "published":
        reasons.append("批次已发布")
    if block.get("excluded"):
        reasons.append("题目已排除")
    if block.get("confirmed"):
        reasons.append("题目已批准")
    if group is None and block.get("group_id"):
        group = await session.get(QuestionImportGroup, uuid.UUID(block["group_id"]))
    if items is None:
        items = list(await session.scalars(select(QuestionImportItem).where(
            QuestionImportItem.group_id == group.id).order_by(QuestionImportItem.question_no))) if group else []
    if not items or len(items) != len(block.get("parts", [])):
        reasons.append("题目结构不完整，请逐题核对")
    if suggestions is None:
        suggestions = await _load_approval_suggestions(session, batch, items)
    for item in items:
        prefix = f"第 {item.question_no} 小问："
        if item.status != "needs_review":
            reasons.append(prefix + {"approved": "已批准", "published": "已发布", "blocked": "存在待修复错误", "rejected": "已排除"}.get(item.status, "当前状态不可批准"))
        issues = list(item.validation_issues or []) + [v.model_dump() for v in validate_import_item(item, group)]
        for issue in issues:
            (reasons if issue["severity"] == "error" else warnings).append(prefix + issue["message"])
        texts = [group.material_markdown, group.explanation_markdown, item.stem_markdown, item.explanation_markdown]
        texts += [o["content_markdown"] for o in item.options_payload]
        if any(not m[2].startswith("asset://") for value in texts for m in IMAGE.finditer(value or "")):
            reasons.append(prefix + "图片尚未归档，请补图或重试下载")
        if selection_rows_by_item is None:
            rows = list(await session.execute(select(QuestionImportKnowledgeSelection, KnowledgeNode).join(
                KnowledgeNode, KnowledgeNode.id == QuestionImportKnowledgeSelection.knowledge_node_id).where(
                QuestionImportKnowledgeSelection.import_item_id == item.id)))
        else:
            rows = selection_rows_by_item.get(item.id, [])
        valid = rows and all(n.status == "active" and n.node_type == "topic" and n.subject_id == batch.subject_id for _, n in rows)
        primary = [n for s, n in rows if s.role == "primary"]
        selection = None
        source = "saved"
        if valid and len(primary) == 1:
            try:
                selection = KnowledgeSelectionInput(primary_code=primary[0].code,
                    related_codes=[n.code for s, n in rows if s.role == "related"])
            except ValidationError:
                pass
        if selection is None and item.id in suggestions:
            selection = suggestions[item.id].as_selection()
            source = "ai"
        if selection is None:
            reasons.append(prefix + "缺少有效主知识点，请推荐知识点或逐题选择")
        else:
            prepared.append((item, selection))
            knowledge.append(dict(question_no=item.question_no, primary_code=selection.primary_code, source=source))
    reasons = list(dict.fromkeys(reasons))
    return dict(eligible=not reasons, reasons=reasons, warnings=list(dict.fromkeys(warnings)), knowledge=knowledge), prepared
