"""Operator-only additive maintenance and retention commands."""

import argparse
from datetime import datetime, timedelta, timezone
from sqlalchemy import update
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
                    < datetime.now(timezone.utc) - timedelta(days=30)
                )
                .values(raw_response=None)
            )
            print({"pruned": result.rowcount})
    await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["backfill-catalog", "prune-ai-responses"])
    run_async(execute(parser.parse_args().command))


if __name__ == "__main__":
    main()
