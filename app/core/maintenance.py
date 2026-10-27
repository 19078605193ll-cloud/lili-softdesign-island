"""Operator-only maintenance and retention commands."""

import argparse
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.core.runtime import run_async
from app.db import SessionFactory, engine
from app.knowledge.versions import backfill
from app.models import QuestionClassificationRun


async def execute(command):
    async with SessionFactory.begin() as session:
        if command == "backfill-catalog":
            print({"backfilled": await backfill(session)})
        elif command == "prune-ai-responses":
            result = await session.execute(
                update(QuestionClassificationRun)
                .where(
                    QuestionClassificationRun.created_at
                    < datetime.now(UTC) - timedelta(days=30)
                )
                .values(raw_response=None)
            )
            print({"pruned": result.rowcount})
        elif command == "prune-obsolete-recall-learning":
            from app.recall.models import Recall
            from app.recall.service import clear_obsolete_sessions

            counts = {}
            recalls = await session.scalars(
                select(Recall)
                .where(Recall.published_version_id.is_not(None))
                .order_by(Recall.id)
                .with_for_update()
            )
            for recall in recalls:
                removed = await clear_obsolete_sessions(session, recall.id, recall.published_version_id)
                for name, count in removed.items():
                    counts[name] = counts.get(name, 0) + count
            print(counts)
    await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["backfill-catalog", "prune-ai-responses", "prune-obsolete-recall-learning"])
    run_async(execute(parser.parse_args().command))


if __name__ == "__main__":
    main()
