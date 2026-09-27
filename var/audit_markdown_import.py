import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import select

from app.db import SessionFactory
from app.imports.ai import OpenAICompatibleQuestionClient
from app.imports.markdown_workflow import refresh_rule_drafts
from app.imports.service import classify_batch
from app.config import get_settings
from app.models import (QuestionAsset, QuestionClassificationRun, QuestionImportBatch,
                        QuestionImportItem, QuestionSourceDocument)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--refresh-rules', action='store_true')
    parser.add_argument('--classify', action='store_true')
    args = parser.parse_args()
    report = []
    async with SessionFactory() as session:
        batches = list(await session.scalars(select(QuestionImportBatch).order_by(QuestionImportBatch.created_at)))
        for batch in batches:
            doc = await session.scalar(select(QuestionSourceDocument).where(QuestionSourceDocument.batch_id == batch.id))
            if not doc or not isinstance(doc.extracted_content, dict) or 'blocks' not in doc.extracted_content:
                continue
            changed = await refresh_rule_drafts(session, batch, doc) if args.refresh_rules else 0
            if args.refresh_rules:
                await session.commit()
                await session.refresh(batch)
                await session.refresh(doc)
            if args.classify:
                ai = OpenAICompatibleQuestionClient(get_settings())
                for _ in range(100):
                    result = await classify_batch(session, batch.id, ai_client=ai, limit=5, storage=None)
                    await session.commit()
                    if not result.remaining_items or not result.processed:
                        break
            summary = batch.validation_summary or {}
            blocks = doc.extracted_content['blocks']
            items = list(await session.scalars(select(QuestionImportItem).where(QuestionImportItem.batch_id == batch.id)))
            item_ids = [item.id for item in items]
            runs = list(await session.scalars(select(QuestionClassificationRun).where(
                QuestionClassificationRun.import_item_id.in_(item_ids)))) if item_ids else []
            assets = list(await session.scalars(select(QuestionAsset).where(QuestionAsset.document_id == doc.id)))
            root = Path(get_settings().import_storage_root)
            report.append(dict(title=batch.title, batch_id=str(batch.id), status=batch.status,
                               parser_version=batch.parser_version, changed_rules=changed,
                               practice_units=len(blocks), subquestions=summary.get('subquestion_count'),
                               ambiguous_blocks=sum(bool(b.get('issues')) or not b.get('group_id') for b in blocks if not b.get('excluded')),
                               item_counts=summary.get('item_counts'),
                               completed_classifications=sum(run.status == 'completed' for run in runs),
                               archived_images=len(assets),
                               missing_archived_files=[asset.storage_path for asset in assets
                                                       if not (root / asset.storage_path).is_file()],
                               image_errors=summary.get('missing_images'),
                               ai_suggestions=summary.get('ai_suggestion_count')))
    output = Path('var/markdown-final-audit.json')
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


asyncio.run(main())
