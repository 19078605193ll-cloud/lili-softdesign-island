from sqlalchemy import select, delete
from app.models import (
    QuestionImportGroup,
    QuestionImportItem,
    QuestionImportKnowledgeSelection,
)
from app.imports import markdown_workflow as workflow
from app.imports.service import classification_fingerprint
from app.imports.review_context import replace_block


async def apply_asset_mapping(session, batch, doc):
    mapping = doc.extracted_content["assets"]
    changed_groups = set()
    for group in await session.scalars(
        select(QuestionImportGroup).where(QuestionImportGroup.batch_id == batch.id)
    ):
        items = list(
            await session.scalars(
                select(QuestionImportItem).where(
                    QuestionImportItem.group_id == group.id
                )
            )
        )
        before = {item.id: classification_fingerprint(item, group) for item in items}
        group.material_markdown = workflow.rewritten(group.material_markdown, mapping)
        group.explanation_markdown = workflow.rewritten(
            group.explanation_markdown, mapping
        )
        await workflow.synchronize_assets(
            session,
            group,
            [
                ("group_material", group.material_markdown),
                ("explanation", group.explanation_markdown),
            ],
            doc,
        )
        for item in items:
            item.stem_markdown = workflow.rewritten(item.stem_markdown, mapping)
            item.explanation_markdown = workflow.rewritten(
                item.explanation_markdown, mapping
            )
            item.options_payload = [
                dict(
                    o,
                    content_markdown=workflow.rewritten(o["content_markdown"], mapping),
                )
                for o in item.options_payload
            ]
            if before[item.id] != classification_fingerprint(item, group):
                await workflow.sync_item(session, item, group, doc)
                await session.execute(
                    delete(QuestionImportKnowledgeSelection).where(
                        QuestionImportKnowledgeSelection.import_item_id == item.id
                    )
                )
                changed_groups.add(str(group.id))
    for raw in list(doc.extracted_content["blocks"]):
        if raw.get("group_id") in changed_groups:
            replace_block(doc, dict(raw, confirmed=False))
    await workflow.refresh(session, batch, doc)
