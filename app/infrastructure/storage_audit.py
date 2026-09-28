"""Read-only storage reconciliation. Does not delete or modify any files."""

import json
import time
import uuid
from pathlib import Path
from sqlalchemy import select
from app.config import get_settings
from app.core.runtime import run_async
from app.db import SessionFactory, engine
from app.models import QuestionImportBatch, Job, QuestionAsset, QuestionSourceDocument


async def main():
    root = Path(get_settings().import_storage_root).resolve()
    async with SessionFactory() as session:
        batches = set(
            str(v) for v in await session.scalars(select(QuestionImportBatch.id))
        )
        active = set(
            str(v)
            for v in await session.scalars(
                select(Job.batch_id).where(
                    Job.status.in_(["queued", "running", "retry_wait"])
                )
            )
        )
        paths = list(await session.scalars(select(QuestionAsset.storage_path))) + list(
            await session.scalars(select(QuestionSourceDocument.storage_path))
        )
    missing = [p for p in paths if not (root / p).is_file()]
    orphan = []
    if root.exists():
        for path in root.iterdir():
            try:
                uuid.UUID(path.name)
            except ValueError:
                continue
            if (
                path.is_dir()
                and path.name not in batches | active
                and time.time() - path.stat().st_mtime > 86400
            ):
                orphan.append(path.name)
    print(
        json.dumps(
            {
                "dry_run": True,
                "missing_references": missing,
                "orphan_batch_directories_older_than_24h": orphan,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    await engine.dispose()


if __name__ == "__main__":
    run_async(main())
