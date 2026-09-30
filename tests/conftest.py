from __future__ import annotations

import os
import subprocess
import sys
import asyncio
import socket
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ["APP_ENV"] = "test"
os.environ["AI_API_KEY"] = ""
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://island_test:invalid@127.0.0.1:1/island_isolated_test")

from app.db import get_session, attach_audit
from fastapi import Request
from app.knowledge.sync import sync_catalog
from app.main import app

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://island_test:invalid@127.0.0.1:1/island_isolated_test",
)


def _assert_safe_test_database() -> None:
    url = make_url(TEST_DATABASE_URL)
    run_id = os.getenv("ISLAND_TEST_RUN", "")
    if (url.database != "island_isolated_test" or url.username != "island_test"
            or url.host not in {"127.0.0.1", "localhost"} or not run_id.startswith("island-test-")):
        raise RuntimeError("Run integration tests through scripts/test_isolated.py")
    with create_engine(TEST_DATABASE_URL).connect() as connection:
        if connection.scalar(text("SELECT run_id FROM test_guard.identity")) != run_id:
            raise RuntimeError("Database does not belong to this isolated test run")


@pytest.fixture(scope="session")
def event_loop_policy():
    return asyncio.WindowsSelectorEventLoopPolicy() if sys.platform == "win32" else asyncio.DefaultEventLoopPolicy()


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    original = socket.getaddrinfo
    def guarded(host, *args, **kwargs):
        if host not in {"127.0.0.1", "localhost", "::1", b"127.0.0.1", b"localhost", b"::1", None}:
            raise RuntimeError("External network is disabled in tests")
        return original(host, *args, **kwargs)
    monkeypatch.setattr(socket, "getaddrinfo", guarded)


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
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
    )
    return TEST_DATABASE_URL


@pytest.fixture
async def session(migrated_database: str) -> AsyncIterator[AsyncSession]:
    from app.core.security import redis_client
    async with redis_client() as redis:
        await redis.flushdb()
    engine = create_async_engine(migrated_database)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database_session:
        tables = (await database_session.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'"))).all()
        if tables:
            quoted = ', '.join('"' + name.replace('"', '""') + '"' for name in tables)
            await database_session.execute(text('TRUNCATE ' + quoted + ' CASCADE'))
            await database_session.commit()
        await sync_catalog(database_session)
        await database_session.commit()
        yield database_session
        await database_session.rollback()
    await engine.dispose()


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    from app.models import User, Role, UserRole
    from app.core.security import passwords
    user = User(username="test-admin", password_hash=passwords.hash("isolated-test-password"))
    session.add_all([user, Role(name="administrator")])
    await session.flush()
    session.add(UserRole(user_id=user.id, role="administrator"))
    await session.commit()

    async def override_session(request: Request) -> AsyncIterator[AsyncSession]:
        attach_audit(session, request)
        yield session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        test_client.headers["Origin"] = "http://test"
        csrf = (await test_client.get('/api/v1/auth/csrf')).json()['csrf_token']
        test_client.headers['X-CSRF-Token'] = csrf
        login = await test_client.post('/api/v1/auth/login', json={'username': 'test-admin', 'password': 'isolated-test-password'})
        assert login.status_code == 200, login.text
        test_client.headers['X-CSRF-Token'] = login.json()['csrf_token']
        # Existing UI sends the revision from its last read; tests may override it
        # explicitly to exercise stale-write rejection.
        async def revision_header(request):
            import re
            match = re.match(r'/api/v1/admin/(?:markdown|import)-batches/([0-9a-f-]+)', request.url.path)
            if match and request.method not in {'GET', 'HEAD'} and 'if-match' not in request.headers:
                from app.models import QuestionImportBatch
                import uuid
                row = await session.get(QuestionImportBatch, uuid.UUID(match[1]))
                if row:
                    await session.refresh(row)
                    request.headers['If-Match'] = str(row.revision)
        test_client.event_hooks['request'].append(revision_header)
        test_client.island_session = session
        yield test_client
    app.dependency_overrides.clear()
