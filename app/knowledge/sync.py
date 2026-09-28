from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionFactory
from app.core.runtime import run_async
from app.knowledge.catalog import default_catalog_path, flatten_catalog, load_catalog
from app.models import ExamSubject, KnowledgeNode, KnowledgeTaxonomyRelease
from app.knowledge.versions import snapshot


async def sync_catalog(session: AsyncSession, path: Path | None = None) -> dict[str, int | str]:
    catalog, checksum = load_catalog(path)
    subject = await session.scalar(
        select(ExamSubject).where(ExamSubject.code == catalog.subject.code)
    )
    if subject is None:
        subject = ExamSubject(
            code=catalog.subject.code,
            name=catalog.subject.name,
            qualification_level=catalog.subject.qualification_level,
            paper_kind=catalog.subject.paper_kind,
        )
        session.add(subject)
        await session.flush()
    else:
        subject.name = catalog.subject.name
        subject.qualification_level = catalog.subject.qualification_level
        subject.paper_kind = catalog.subject.paper_kind
        subject.is_active = True

    existing_release = await session.scalar(
        select(KnowledgeTaxonomyRelease).where(
            KnowledgeTaxonomyRelease.subject_id == subject.id,
            KnowledgeTaxonomyRelease.version == catalog.version,
        )
    )
    if existing_release is not None and existing_release.checksum_sha256 != checksum:
        raise ValueError(
            f"taxonomy version {catalog.version} already exists with a different checksum; "
            "publish a new version instead"
        )
    if existing_release is not None and existing_release.catalog_snapshot is None:
        existing_release.catalog_snapshot = snapshot(catalog)

    existing_nodes = {
        node.code: node
        for node in (
            await session.scalars(
                select(KnowledgeNode).where(KnowledgeNode.subject_id == subject.id)
            )
        ).all()
    }
    created = 0
    updated = 0
    node_ids = {code: node.id for code, node in existing_nodes.items()}
    for item in flatten_catalog(catalog):
        node = existing_nodes.get(item.code)
        parent_id = node_ids.get(item.parent_code) if item.parent_code else None
        if item.parent_code and parent_id is None:
            raise ValueError(f"parent node has not been synchronized: {item.parent_code}")
        values = item.model_dump(exclude={"parent_code"})
        if node is None:
            node = KnowledgeNode(subject_id=subject.id, parent_id=parent_id, **values)
            session.add(node)
            await session.flush()
            existing_nodes[item.code] = node
            node_ids[item.code] = node.id
            created += 1
        else:
            node.parent_id = parent_id
            for key, value in values.items():
                setattr(node, key, value)
            updated += 1

    if existing_release is None:
        session.add(
            KnowledgeTaxonomyRelease(
                subject_id=subject.id,
                version=catalog.version,
                source_name=catalog.source_name,
                source_reference=catalog.source_reference,
                checksum_sha256=checksum,
                catalog_snapshot=snapshot(catalog),
            )
        )
    await session.flush()
    return {
        "subject": subject.code,
        "version": catalog.version,
        "created": created,
        "updated": updated,
        "total": len(existing_nodes),
    }


async def _run(path: Path) -> None:
    async with SessionFactory.begin() as session:
        result = await sync_catalog(session, path)
        print(
            f"Synced {result['subject']} taxonomy {result['version']}: "
            f"created={result['created']} updated={result['updated']} total={result['total']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Synchronize the versioned knowledge taxonomy")
    parser.add_argument("--file", type=Path, default=default_catalog_path())
    args = parser.parse_args()
    run_async(_run(args.file))


if __name__ == "__main__":
    main()
