"""Markdown import lifecycle, separate from the retired page/OCR pipeline."""
from __future__ import annotations

import hashlib
import copy
import json
import re
import uuid
from collections import Counter
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (ExamPaper, Question, QuestionGroup, QuestionOption, QuestionCorrectOption,
    QuestionKnowledgeAssignment, QuestionImportBatch, QuestionSourceDocument, QuestionImportGroup,
    QuestionImportItem, QuestionImportKnowledgeSelection, QuestionClassificationRun, QuestionAsset, QuestionAssetUsage,
    PracticeQuestion, PracticeQuestionPart, PracticeQuestionRevision)
from app.imports.markdown_parser import VERSION, IMAGE, Unit, parse_markdown, parse_block
from app.imports.markdown_storage import local_image, download_image, store_image
from app.imports.storage import LocalImportStorage
from app.imports.service import ImportWorkflowError, ImportConflictError, validate_import_item
from app.services import recompute_paper_aggregates


def is_markdown(batch):
    return batch.parser_version.startswith("markdown-")


async def document_for(session, batch_id, *, lock=False):
    query = select(QuestionSourceDocument).where(QuestionSourceDocument.batch_id == batch_id)
    if lock:
        query = query.with_for_update()
    doc = await session.scalar(query)
    if doc is None or doc.mime_type != "text/markdown":
        raise ImportWorkflowError("不是 Markdown 导入批次")
    return doc


async def initialize(session, batch, storage, document_path: Path, original_name: str, root: Path):
    try:
        source = document_path.read_text(encoding="utf-8-sig")
    except UnicodeError as exc:
        raise ImportWorkflowError("Markdown 必须使用 UTF-8 编码") from exc
    if '\x00' in source:
        raise ImportWorkflowError("Markdown 含无效控制字符，请清理后重新上传")
    batch.parser_version = VERSION
    batch.expected_question_count = None
    batch.source_reference = batch.source_reference or "考生回忆版"
    doc = QuestionSourceDocument(batch_id=batch.id, role="combined", original_name=original_name,
        mime_type="text/markdown", file_size=document_path.stat().st_size,
        sha256=hashlib.sha256(document_path.read_bytes()).hexdigest(),
        storage_path=document_path.relative_to(storage.root).as_posix(), status="uploaded",
        extracted_content={"source": source, "blocks": [], "assets": {}, "image_errors": {}})
    session.add(doc)
    await session.flush()
    blocks = [unit.model_dump() | {"id": str(uuid.uuid4()), "excluded": False, "confirmed": False}
              for unit in parse_markdown(source)]
    doc.extracted_content = dict(doc.extracted_content, blocks=blocks)
    await archive_images(session, batch, doc, storage, document_path, root)
    await materialize(session, batch, doc)
    await auto_classify(session, batch, storage)
    return doc


async def archive_images(session, batch, doc, storage, document_path=None, root=None):
    state = dict(doc.extracted_content)
    assets = dict(state.get("assets", {})); errors = {}
    document_path = document_path or storage.resolve(doc.storage_path)
    root = root or storage.resolve(f"{batch.id}/markdown")
    references = dict.fromkeys(match[2] for match in IMAGE.finditer(state["source"]))
    for reference in references:
        if reference in assets:
            continue
        try:
            if reference.startswith(("https://", "http://")):
                data = await download_image(reference, min(storage.max_bytes, 20 * 1024 * 1024))
            else:
                data = local_image(reference, document_path, root).read_bytes()
            info = store_image(storage, batch.id, data)
            asset = await session.scalar(select(QuestionAsset).where(
                QuestionAsset.document_id == doc.id, QuestionAsset.sha256 == info["sha256"]))
            if asset is None:
                asset = QuestionAsset(document_id=doc.id, page_no=None, bbox=None, kind="figure", **info)
                session.add(asset)
                await session.flush()
            assets[reference] = str(asset.id)
        except Exception as exc:
            errors[reference] = str(exc)
    doc.extracted_content = dict(state, assets=assets, image_errors=errors)
    await session.flush()


def rewritten(value, mapping):
    if value is None:
        return None
    return IMAGE.sub(lambda m: f"![{m[1]}](asset://{mapping[m[2]]})" if m[2] in mapping else m[0], value)


async def synchronize_assets(session, owner, fields, doc):
    owner_column = QuestionAssetUsage.import_group_id if isinstance(owner, QuestionImportGroup) else QuestionAssetUsage.import_item_id
    await session.execute(delete(QuestionAssetUsage).where(owner_column == owner.id))
    valid_ids = set(await session.scalars(select(QuestionAsset.id).where(QuestionAsset.document_id == doc.id)))
    for placement, value in fields:
        for position, match in enumerate(IMAGE.finditer(value or "")):
            if not match[2].startswith("asset://"):
                continue
            try:
                asset_id = uuid.UUID(match[2][8:])
            except ValueError as exc:
                raise ImportWorkflowError("素材标识无效") from exc
            if asset_id not in valid_ids:
                raise ImportWorkflowError("素材不属于当前文档")
            session.add(QuestionAssetUsage(asset_id=asset_id,
                **{owner_column.key: owner.id}, placement=placement, sort_order=position,
                alt_text=match[1][:300] or "原题素材"))


async def materialize(session, batch, doc, only_id=None):
    """Create drafts only once; reprocessing never overwrites reviewed drafts."""
    state = copy.deepcopy(doc.extracted_content); blocks = state["blocks"]
    used = set(await session.scalars(select(QuestionImportItem.question_no).where(QuestionImportItem.batch_id == batch.id)))
    for block in blocks:
        if only_id and block["id"] != only_id:
            continue
        if block.get("group_id") or not block.get("parts"):
            continue
        numbers = [p["question_no"] for p in block["parts"]]
        if len(numbers) != len(set(numbers)) or used.intersection(numbers):
            block["issues"] = list(dict.fromkeys(block["issues"] + ["存在重复题号，请修正后保存题块"]))
            continue
        group = QuestionImportGroup(batch_id=batch.id, source_label=block["id"],
            material_markdown=rewritten(block["material_markdown"], state["assets"]),
            explanation_markdown=rewritten(block.get("explanation_markdown"), state["assets"]),
            source_refs=[{"document_id": str(doc.id), "line_start": block["line_start"], "line_end": block["line_end"], "label": block["source_label"]}],
            sort_order=block["line_start"])
        session.add(group); await session.flush()
        block["group_id"] = str(group.id)
        await synchronize_assets(session, group, [("group_material", group.material_markdown), ("explanation", group.explanation_markdown)], doc)
        for part in block["parts"]:
            options = [{"key": o["key"], "content_markdown": rewritten(o["content_markdown"], state["assets"])} for o in part["options"]]
            item = QuestionImportItem(batch_id=batch.id, group_id=group.id, question_no=part["question_no"],
                stem_markdown=rewritten(part["stem_markdown"], state["assets"]), options_payload=options,
                correct_option_keys=part["correct_option_keys"],
                explanation_markdown=rewritten(part.get("explanation_markdown"), state["assets"]),
                source_refs=[{"document_id": str(doc.id), "line_start": block["line_start"], "line_end": block["line_end"], "method": block["method"]}],
                status="needs_review")
            session.add(item); await session.flush()
            await sync_item(session, item, group, doc)
            if block.get("issues"):
                item.validation_issues = item.validation_issues + [dict(code="ambiguous_block", severity="error", message="；".join(block["issues"]))]
                item.status = "blocked"
        used.update(numbers)
    doc.extracted_content = dict(state, blocks=blocks)
    await refresh(session, batch, doc)


async def refresh_rule_drafts(session, batch, doc):
    """Generate suggestions only; never overwrite reviewed or manually edited drafts."""
    if batch.status == 'published':
        return 0
    state = copy.deepcopy(doc.extracted_content)
    changed = 0
    for block in state['blocks']:
        if block.get('confirmed') or block.get('excluded'):
            continue
        suggestion = parse_block(block['raw'], block['line_start']).model_dump()
        current_material = block.get('material_markdown')
        if block.get('group_id'):
            group = await session.get(QuestionImportGroup, uuid.UUID(block['group_id']))
            current_material = group.material_markdown if group else current_material
        if current_material != suggestion['material_markdown'] or any(suggestion.get(key) != block.get(key) for key in ('material_markdown', 'parts', 'explanation_markdown', 'issues')):
            block['repair_suggestion'] = suggestion
            changed += 1
    doc.extracted_content = state
    return changed


async def sync_item(session, item, group, doc):
    fields = [("stem", item.stem_markdown), ("explanation", item.explanation_markdown)]
    fields += [("option", o["content_markdown"]) for o in item.options_payload]
    await synchronize_assets(session, item, fields, doc)
    issues = [i.model_dump() for i in validate_import_item(item, group)]
    values = fields + [("group_material", group.material_markdown if group else ""), ("explanation", group.explanation_markdown if group else None)]
    if any(not m[2].startswith("asset://") for _, value in values for m in IMAGE.finditer(value or "")):
        issues.append(dict(code="missing_image", severity="error", message="仍有图片未归档，请补图或重试"))
    item.validation_issues = issues
    if item.status != "rejected":
        item.status = "blocked" if any(i["severity"] == "error" for i in issues) else "needs_review"


async def refresh(session, batch, doc=None):
    if batch.status == "published":
        return
    doc = doc or await document_for(session, batch.id)
    await session.flush()
    items = list(await session.scalars(select(QuestionImportItem).where(QuestionImportItem.batch_id == batch.id)))
    by_group = {}
    for item in items:
        by_group.setdefault(str(item.group_id), []).append(item)
    blocks = doc.extracted_content["blocks"]
    included = [b for b in blocks if not b.get("excluded")]
    ready = bool(included) and all(b.get("confirmed") and b.get("group_id") and
        by_group.get(b["group_id"]) and all(i.status == "approved" for i in by_group[b["group_id"]]) for b in included)
    batch.status = "ready_to_publish" if ready else "in_review"
    batch.validation_summary = {
        "is_valid": ready, "actual_question_count": len(blocks), "subquestion_count": len(items),
        "excluded_count": len(blocks) - len(included), "rule_count": sum(b["method"] == "rules" for b in blocks),
        "ai_count": sum(b["method"] == "ai" for b in blocks),
        "unresolved_count": sum(bool(b.get("issues")) or not b.get("group_id") for b in included),
        "ai_suggestion_count": sum(bool(b.get("ai_suggestion")) for b in included),
        "missing_images": doc.extracted_content.get("image_errors", {}),
        "item_counts": dict(Counter(i.status for i in items))}


def fingerprint(payload, hashes):
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    raw = re.sub(r"asset://([0-9a-fA-F-]{36})", lambda m: "sha256:" + hashes.get(m[1], m[1]), raw)
    return hashlib.sha256(raw.encode()).hexdigest()


async def draft_payload(session, block):
    group = await session.get(QuestionImportGroup, uuid.UUID(block["group_id"]))
    items = list(await session.scalars(select(QuestionImportItem).where(QuestionImportItem.group_id == group.id).order_by(QuestionImportItem.question_no)))
    return group, items, {"material": group.material_markdown, "explanation": group.explanation_markdown, "parts": [dict(
        question_no=i.question_no, stem=i.stem_markdown, options=i.options_payload,
        answers=i.correct_option_keys, explanation=i.explanation_markdown, score=str(i.score)) for i in items]}


async def formal_payload(session, unit):
    group = await session.get(QuestionGroup, unit.group_id) if unit.group_id else None
    questions = list(await session.scalars(select(Question).join(PracticeQuestionPart, PracticeQuestionPart.question_id == Question.id)
        .where(PracticeQuestionPart.practice_question_id == unit.id).order_by(PracticeQuestionPart.position)))
    parts = []
    for q in questions:
        options = list(await session.scalars(select(QuestionOption).where(QuestionOption.question_id == q.id).order_by(QuestionOption.sort_order)))
        correct = set(await session.scalars(select(QuestionCorrectOption.option_id).where(QuestionCorrectOption.question_id == q.id)))
        parts.append(dict(question_no=q.question_no, stem=q.stem_markdown,
            options=[dict(key=o.option_key, content_markdown=o.content_markdown) for o in options],
            answers=[o.option_key for o in options if o.id in correct], explanation=q.explanation_markdown, score=str(q.score)))
    return questions, {"material": group.material_markdown if group else "", "explanation": group.explanation_markdown if group else None, "parts": parts}


async def publish(session: AsyncSession, batch_id, storage=None):
    batch = await session.scalar(select(QuestionImportBatch).where(QuestionImportBatch.id == batch_id).with_for_update())
    if batch.status == "published":
        return dict(batch.validation_summary["publication"], already_published=True)
    doc = await document_for(session, batch.id, lock=True)
    await refresh(session, batch, doc)
    if batch.status != "ready_to_publish":
        raise ImportConflictError("请审核完整组合题，并明确排除所有待处理题块")
    # Serialize both first publication and additions for one exam session.
    identity = f"{batch.subject_id}:{batch.year}:{batch.period}:{batch.batch_code}"
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": identity})
    paper = await session.scalar(select(ExamPaper).where(ExamPaper.subject_id == batch.subject_id,
        ExamPaper.year == batch.year, ExamPaper.period == batch.period, ExamPaper.batch_code == batch.batch_code).with_for_update())
    if paper is None:
        paper = ExamPaper(subject_id=batch.subject_id, year=batch.year, period=batch.period, batch_code=batch.batch_code,
            title=batch.title, exam_date=batch.exam_date, source_reference=batch.source_reference, status="verified")
        session.add(paper); await session.flush()
    hashes = {str(a.id): a.sha256 for a in await session.scalars(select(QuestionAsset))}
    counts = dict(added=0, replaced=0, skipped=0, excluded=0)
    for block in doc.extracted_content["blocks"]:
        if block.get("excluded"):
            counts["excluded"] += 1
            continue
        group, items, payload = await draft_payload(session, block)
        from app.imports.service import _validate_batch_asset_usages
        await _validate_batch_asset_usages(session, items)
        if storage:
            for asset_id in set(re.findall(r'asset://([0-9a-fA-F-]{36})', json.dumps(payload))):
                asset = await session.get(QuestionAsset, uuid.UUID(asset_id))
                if asset is None or not storage.resolve(asset.storage_path).is_file():
                    raise ImportWorkflowError("引用的图片文件缺失，请补图后发布")
        for item in items:
            # Revalidate at the transactional boundary, independent of cached review status.
            if any(v.severity == "error" for v in validate_import_item(item, group)) or any(v["severity"] == "error" for v in item.validation_issues):
                raise ImportWorkflowError(f"第 {item.question_no} 小问仍有阻断问题")
        digest = fingerprint(payload, hashes)
        numbers = [i.question_no for i in items]
        label = str(numbers[0]) if len(numbers) == 1 else f"{numbers[0]}-{numbers[-1]}"
        # Exact membership matters; source labels alone do not establish identity.
        existing_questions = list(await session.scalars(select(Question).where(Question.paper_id == paper.id, Question.question_no.in_(numbers))))
        unit = None; old_questions = []; old_payload = None
        if existing_questions:
            ids = set(await session.scalars(select(PracticeQuestionPart.practice_question_id).where(PracticeQuestionPart.question_id.in_([q.id for q in existing_questions]))))
            if len(ids) != 1:
                raise ImportConflictError(f"第 {label} 题与已有题目组合边界冲突，不能自动合并")
            unit = await session.get(PracticeQuestion, next(iter(ids)))
            old_questions, old_payload = await formal_payload(session, unit)
            if sorted(q.question_no for q in old_questions) != numbers:
                raise ImportConflictError(f"第 {label} 题与已有组合题小问范围不同")
            equal = fingerprint(old_payload, hashes) == digest
            decision = block.get("conflict_action")
            if not equal and decision not in {"keep", "replace"}:
                raise ImportConflictError(f"第 {label} 题内容冲突，请查看差异并选择保留或替换")
            if not equal and block.get("conflict_hash") != fingerprint(old_payload, hashes):
                raise ImportConflictError("正式题目已发生变化，请重新确认冲突")
            if equal or decision == "keep":
                for item, question in zip(items, old_questions):
                    item.published_question_id = question.id; item.status = "published"
                counts["skipped"] += 1
                continue
            assignments = list(await session.scalars(select(QuestionKnowledgeAssignment).where(QuestionKnowledgeAssignment.question_id.in_([q.id for q in old_questions]))))
            old_payload["assignments"] = [{"question_id": str(a.question_id), "node_id": str(a.knowledge_node_id), "role": a.role, "status": a.status, "source": a.source} for a in assignments]
            old_payload["question_ids"] = [str(q.id) for q in old_questions]
            session.add(PracticeQuestionRevision(practice_question_id=unit.id, batch_id=batch.id, snapshot=old_payload))
            counts["replaced"] += 1
        else:
            unit = PracticeQuestion(paper_id=paper.id, source_label=label, content_hash=digest)
            session.add(unit); await session.flush(); counts["added"] += 1
        formal_group = await session.get(QuestionGroup, unit.group_id) if unit.group_id else None
        if len(items) > 1:
            if formal_group is None:
                formal_group = QuestionGroup(subject_id=batch.subject_id, paper_id=paper.id, source_label=label,
                    material_markdown=group.material_markdown, sort_order=min(numbers))
                session.add(formal_group); await session.flush(); unit.group_id = formal_group.id
            formal_group.material_markdown = group.material_markdown
            formal_group.explanation_markdown = group.explanation_markdown
            await session.execute(delete(QuestionAssetUsage).where(QuestionAssetUsage.question_group_id == formal_group.id))
            for usage in await session.scalars(select(QuestionAssetUsage).where(QuestionAssetUsage.import_group_id == group.id)):
                session.add(QuestionAssetUsage(asset_id=usage.asset_id, question_group_id=formal_group.id,
                    placement=usage.placement, sort_order=usage.sort_order, alt_text=usage.alt_text))
        for index, item in enumerate(items):
            selections = list(await session.scalars(select(QuestionImportKnowledgeSelection).where(QuestionImportKnowledgeSelection.import_item_id == item.id)))
            if sum(s.role == "primary" for s in selections) != 1:
                raise ImportWorkflowError(f"第 {item.question_no} 小问需要一个主知识点")
            q = old_questions[index] if old_questions else Question(subject_id=batch.subject_id, paper_id=paper.id, question_no=item.question_no)
            q.group_id = formal_group.id if formal_group else None; q.group_order = index + 1 if formal_group else None
            q.question_type = "single_choice"; q.stem_markdown = item.stem_markdown
            q.explanation_markdown = item.explanation_markdown; q.score = item.score
            q.source_type = "recalled"; q.source_reference = f"import-batch:{batch.id};item:{item.id}"
            q.content_hash = digest; q.status = "published"
            session.add(q); await session.flush()
            if not old_questions:
                session.add(PracticeQuestionPart(practice_question_id=unit.id, question_id=q.id, position=index + 1))
            await session.execute(delete(QuestionOption).where(QuestionOption.question_id == q.id))
            await session.execute(delete(QuestionKnowledgeAssignment).where(QuestionKnowledgeAssignment.question_id == q.id))
            await session.execute(delete(QuestionAssetUsage).where(QuestionAssetUsage.question_id == q.id))
            for order, option in enumerate(item.options_payload):
                o = QuestionOption(question_id=q.id, option_key=option["key"], content_markdown=option["content_markdown"], sort_order=order)
                session.add(o); await session.flush()
                if o.option_key in item.correct_option_keys:
                    session.add(QuestionCorrectOption(question_id=q.id, option_id=o.id))
            for selection in selections:
                session.add(QuestionKnowledgeAssignment(question_id=q.id, subject_id=batch.subject_id,
                    knowledge_node_id=selection.knowledge_node_id, taxonomy_release_id=batch.taxonomy_release_id,
                    role=selection.role, status="confirmed", source=selection.source))
            for usage in await session.scalars(select(QuestionAssetUsage).where(QuestionAssetUsage.import_item_id == item.id)):
                session.add(QuestionAssetUsage(asset_id=usage.asset_id, question_id=q.id, placement=usage.placement,
                    sort_order=usage.sort_order, alt_text=usage.alt_text))
            item.published_question_id = q.id; item.status = "published"
        unit.content_hash = digest
    await session.flush()
    paper.total_score = await session.scalar(select(func.coalesce(func.sum(Question.score), 0)).where(Question.paper_id == paper.id, Question.status == "published"))
    await recompute_paper_aggregates(session, paper.id, calculation_version=VERSION)
    unit_count = await session.scalar(select(func.count()).select_from(PracticeQuestion).where(PracticeQuestion.paper_id == paper.id))
    part_count = await session.scalar(select(func.count()).select_from(Question).where(Question.paper_id == paper.id, Question.status == "published"))
    result = dict(batch_id=str(batch.id), paper_id=str(paper.id), question_count=unit_count,
        subquestion_count=part_count, total_score=str(paper.total_score), already_published=False, **counts)
    batch.published_paper_id = paper.id; batch.status = "published"
    batch.validation_summary = dict(batch.validation_summary, publication=result)
    await session.flush()
    return result


async def auto_classify(session, batch, storage=None, item_ids=None):
    """Classify valid pending drafts; failures remain reviewable and never publish."""
    from app.config import get_settings
    from app.imports.ai import OpenAICompatibleQuestionClient
    from app.imports.service import classify_batch
    settings = get_settings()
    if not settings.ai_api_key or not settings.ai_classification_model:
        return
    storage = storage or LocalImportStorage(settings.import_storage_root)
    client = OpenAICompatibleQuestionClient(settings)
    ids = set(await session.scalars(select(QuestionImportItem.id).where(
        QuestionImportItem.batch_id == batch.id, QuestionImportItem.status == 'needs_review')))
    if item_ids is not None:
        ids &= item_ids
    ordered = sorted(ids, key=str)
    for start in range(0, len(ordered), 5):
        await classify_batch(session, batch.id, ai_client=client, limit=5, storage=storage, item_ids=set(ordered[start:start+5]))
    await refresh(session, batch)
