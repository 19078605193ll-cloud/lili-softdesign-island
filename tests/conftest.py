from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import get_session
from app.knowledge.sync import sync_catalog
from app.main import app

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/software_designer_test",
)


def _assert_safe_test_database() -> None:
    database = make_url(TEST_DATABASE_URL).database or ""
    if not database.endswith("_test"):
        raise RuntimeError("TEST_DATABASE_URL must point to a database whose name ends with '_test'")


@pytest.fixture(scope="session")
def migrated_database() -> str:
    _assert_safe_test_database()
    env = os.environ.copy()
    env["DATABASE_URL"] = TEST_DATABASE_URL
    # New content migrations intentionally refuse lossy downgrades. Tests own only
    # the explicitly guarded *_test schema and rebuild it from an empty database.
    with create_engine(TEST_DATABASE_URL).begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    subprocess.run(
        ["python", "-m", "alembic", "upgrade", "head"],
        check=True,
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
    )
    return TEST_DATABASE_URL


@pytest.fixture
async def session(migrated_database: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(migrated_database)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database_session:
        await sync_catalog(database_session)
        yield database_session
        await database_session.rollback()
    await engine.dispose()


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        yield test_client
    app.dependency_overrides.clear()
