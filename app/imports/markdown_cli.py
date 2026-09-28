"""Import a local directory without publishing. Run with --dry-run first."""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import re
from pathlib import Path

from sqlalchemy import select
from app.db import SessionFactory
from app.config import get_settings
from app.imports.markdown_parser import parse_markdown, IMAGE
from app.imports.markdown_storage import local_image
from app.imports.markdown_workflow import initialize
from app.imports.service import create_batch_record
from app.imports.storage import LocalImportStorage
from app.models import QuestionSourceDocument


async def run(root: Path, dry_run: bool):
    root = root.resolve()
    storage = LocalImportStorage(get_settings().import_storage_root)
    report = []
    for path in sorted(root.glob('*.md')):
        source = path.read_text(encoding='utf-8-sig')
        units = parse_markdown(source)
        entry = dict(file=path.name, question_count=len(units), subquestion_count=sum(len(u.parts) for u in units),
            unresolved=[dict(label=u.source_label, lines=[u.line_start,u.line_end], issues=u.issues) for u in units if u.issues])
        local = remote = 0; missing=[]
        for match in IMAGE.finditer(source):
            if match[2].startswith(('https://','http://')):
                remote += 1
            else:
                local += 1
                try: local_image(match[2],path,root)
                except Exception as exc: missing.append(str(exc))
        entry.update(local_images=local, remote_images=remote, missing_images=missing)
        if not dry_run:
            year = re.search(r'(20\d{2})',path.name)
            month = re.search(r'年\s*(\d{1,2})\s*月',path.name)
            if not year or not (month or '上半年' in path.name or '下半年' in path.name):
                raise ValueError(f'无法确定年份/期次：{path.name}，请使用管理页面填写')
            period = 'first_half' if '上半年' in path.name or (month and int(month[1]) <= 6) else 'second_half'
            async with SessionFactory() as session:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                existing = await session.scalar(select(QuestionSourceDocument).where(QuestionSourceDocument.sha256 == digest,
                    QuestionSourceDocument.mime_type == 'text/markdown'))
                if existing:
                    entry.update(batch_id=str(existing.batch_id), skipped=True)
                else:
                    batch = await create_batch_record(session, subject_code='software-designer.foundation', taxonomy_version=None,
                        year=int(year[1]), period=period, batch_code='default', title=path.stem, exam_date=None, source_reference='考生回忆版')
                    batch_id = batch.id
                    try:
                        target = storage.resolve(f'{batch.id}/markdown/source.md'); target.parent.mkdir(parents=True,exist_ok=True)
                        target.write_bytes(path.read_bytes())
                        await initialize(session,batch,storage,target,path.name,root)
                        await session.commit(); entry['batch_id']=str(batch.id)
                        from app.imports.markdown_api import assist_pending
                        while True:
                            progress = await assist_pending(batch.id, session)
                            print(json.dumps(dict(file=path.name, auxiliary=progress),ensure_ascii=False),flush=True)
                            if not progress.get('configured') or not progress.get('remaining') or not progress.get('processed'):
                                break
                    except Exception:
                        await session.rollback()
                        # A committed draft and its images must survive an auxiliary failure.
                        if 'batch_id' not in entry:
                            storage.remove_batch(batch_id)
                        raise
        report.append(entry)
        print(json.dumps(entry,ensure_ascii=False),flush=True)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    if not args.dry_run:
        parser.error("同步写入入口已停用；请通过管理后台或 /api/v2/admin/import-batches 提交持久任务。此命令只支持 --dry-run。")
    if __import__('sys').platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    result=asyncio.run(run(args.directory,args.dry_run))
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__ == '__main__':
    main()
