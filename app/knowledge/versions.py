"""Catalog snapshots retain the vocabulary actually supplied to a classifier."""

from sqlalchemy import select
from app.knowledge.catalog import flatten_catalog, load_catalog
from app.models import KnowledgeTaxonomyRelease


def snapshot(catalog) -> dict:
    nodes = flatten_catalog(catalog)
    by_code = {n.code: n for n in nodes}
    topics = []
    for node in nodes:
        if node.node_type != "topic" or node.status != "active":
            continue
        path, current = [node.name], node
        while current.parent_code:
            current = by_code[current.parent_code]
            path.append(current.name)
        topics.append(
            dict(
                code=node.code,
                path=" / ".join(reversed(path)),
                aliases=node.aliases,
                keywords=node.keywords,
                guidance=node.classification_guidance,
            )
        )
    return {
        "version": catalog.version,
        "nodes": [n.model_dump(mode="json") for n in nodes],
        "topics": topics,
    }


async def backfill(session):
    catalog, checksum = load_catalog()
    releases = await session.scalars(
        select(KnowledgeTaxonomyRelease).where(
            KnowledgeTaxonomyRelease.checksum_sha256 == checksum,
            KnowledgeTaxonomyRelease.catalog_snapshot.is_(None),
        )
    )
    count = 0
    for release in releases:
        release.catalog_snapshot = snapshot(catalog)
        count += 1
    await session.flush()
    return count


def topic_codes(release) -> set[str] | None:
    return (
        {t["code"] for t in release.catalog_snapshot["topics"]}
        if release.catalog_snapshot
        else None
    )
