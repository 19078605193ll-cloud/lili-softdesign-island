import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, event
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.main import app
from app.models import User, UserRole, Job, Attempt, PracticeQuestion
from tests.test_markdown_integration import upload, review
from tests.test_markdown_integration import storage as storage

pytestmark = pytest.mark.integration


async def test_anonymous_admin_and_retired_routes_are_protected(client):
    async with AsyncClient(
        transport=ASGITransport(app), base_url="http://test"
    ) as anonymous:
        assert (await anonymous.get("/admin/imports")).status_code == 303
        assert (await anonymous.get("/api/v1/admin/import-batches")).status_code == 401
        assert (await anonymous.post("/api/v2/admin/import-batches")).status_code == 401
        assert (
            await anonymous.get("/api/v1/admin/question-assets/" + str(uuid.uuid4()))
        ).status_code == 401
    assert (await client.post("/api/v1/admin/import-batches")).status_code == 410


async def test_csrf_roles_revocation_and_logout(client, session):
    assert (
        await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "bad"})
    ).status_code == 403
    assert (
        await client.post(
            "/api/v1/auth/logout", headers={"Origin": "https://attacker.invalid"}
        )
    ).status_code == 403
    user = await session.scalar(select(User).where(User.username == "test-admin"))
    user.auth_version += 1
    await session.commit()
    assert (await client.get("/api/v1/auth/me")).status_code == 401


async def test_learner_has_no_admin_permissions(client, session):
    from sqlalchemy import delete

    await session.execute(delete(UserRole))
    await session.commit()
    assert (await client.get("/api/v1/auth/me")).status_code == 200
    assert (await client.get("/admin/imports")).status_code == 403
    assert (await client.get("/api/v1/admin/import-batches")).status_code == 403


async def test_redis_failure_fails_closed(client, monkeypatch):
    from redis.exceptions import ConnectionError

    monkeypatch.setattr(
        "redis.asyncio.Redis.get", AsyncMock(side_effect=ConnectionError())
    )
    assert (await client.get("/api/v1/admin/import-batches")).status_code == 503


async def test_job_idempotency_and_stale_write(client, session, storage):
    batch = await upload(client)
    state = (await client.get("/api/v1/admin/markdown-batches/" + batch)).json()
    block = state["blocks"][0]
    stale = await client.post(
        f"/api/v1/admin/markdown-batches/{batch}/blocks/{block['id']}/review",
        headers={"If-Match": "0"},
        json={"action": "exclude", "reason": "test"},
    )
    assert stale.status_code == 409
    key = uuid.uuid4().hex
    payload = {
        "kind": "classify",
        "revision": state["revision"],
        "block_id": block["id"],
    }
    a = await client.post(
        f"/api/v2/admin/import-batches/{batch}/jobs",
        headers={"Idempotency-Key": key},
        json=payload,
    )
    b = await client.post(
        f"/api/v2/admin/import-batches/{batch}/jobs",
        headers={"Idempotency-Key": key},
        json=payload,
    )
    assert a.status_code == b.status_code == 202
    assert a.json()["job_id"] == b.json()["job_id"]
    c = await client.post(
        f"/api/v2/admin/import-batches/{batch}/jobs",
        headers={"Idempotency-Key": key},
        json=dict(payload, force=True),
    )
    assert c.status_code == 409


async def test_learning_idempotency_versions_and_ownership(client, session, storage):
    batch = await upload(client)
    await review(client, session, batch)
    published = await client.post(f"/api/v1/admin/markdown-batches/{batch}/publish")
    assert published.status_code == 200, published.text
    paper = published.json()["paper_id"]
    listing = (await client.get(f"/api/v2/practice/papers/{paper}/questions")).json()
    unit = listing["items"][0]
    assert "correct_option_keys" not in str(unit) and "explanation_markdown" not in str(
        unit
    )
    body = {
        "practice_question_id": unit["id"],
        "content_version": unit["content_version"],
        "answers": {p["id"]: "A" for p in unit["parts"]},
    }
    headers = {"Idempotency-Key": uuid.uuid4().hex}
    first = await client.post("/api/v2/learning/attempts", headers=headers, json=body)
    assert first.status_code == 201, first.text
    again = await client.post("/api/v2/learning/attempts", headers=headers, json=body)
    assert again.status_code == 200 and again.json() == first.json()
    unit_row = await session.get(PracticeQuestion, uuid.UUID(unit["id"]))
    unit_row.content_version += 1
    await session.commit()
    assert (
        await client.post("/api/v2/learning/attempts", headers=headers, json=body)
    ).status_code == 200
    assert (
        await client.post(
            "/api/v2/learning/attempts",
            headers={"Idempotency-Key": uuid.uuid4().hex},
            json=body,
        )
    ).status_code == 409
    assert (await client.get("/api/v2/learning/attempts/" + first.json()["id"])).json()[
        "snapshot"
    ]["content_version"] == 1
    user = User(username="other", password_hash="unusable")
    session.add(user)
    await session.flush()
    attempt = await session.get(Attempt, uuid.UUID(first.json()["id"]))
    attempt.user_id = user.id
    await session.commit()
    assert (
        await client.get("/api/v2/learning/attempts/" + first.json()["id"])
    ).status_code == 404


async def test_worker_fencing_and_recovery(client, session, storage):
    from app.infrastructure.worker import prepare, apply
    from app.infrastructure.dispatcher import dispatch_once
    from datetime import datetime, timedelta, timezone

    batch = await upload(client)
    state = (await client.get("/api/v1/admin/markdown-batches/" + batch)).json()
    r = await client.post(
        f"/api/v2/admin/import-batches/{batch}/jobs",
        headers={"Idempotency-Key": uuid.uuid4().hex},
        json={"kind": "classify", "revision": state["revision"]},
    )
    jid = uuid.UUID(r.json()["job_id"])
    await session.commit()
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    first = await prepare(factory, jid)
    assert await prepare(factory, jid) is None
    async with factory.begin() as db:
        job = await db.get(Job, jid)
        job.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    sent = []
    await dispatch_once(factory, sent.append)
    second = await prepare(factory, jid)
    assert second["generation"] > first["generation"]
    await apply(factory, first, None, {})
    async with factory() as db:
        assert (await db.get(Job, jid)).status == "running"
    assert str(jid) in sent


async def test_query_budget_and_hidden_units(client, session, storage):
    batch = await upload(client)
    await review(client, session, batch)
    published = (
        await client.post(f"/api/v1/admin/markdown-batches/{batch}/publish")
    ).json()
    count = 0

    def hit(*args):
        nonlocal count
        count += 1

    event.listen(session.bind.sync_engine, "before_cursor_execute", hit)
    try:
        r = await client.get(
            "/api/v2/practice/papers/" + published["paper_id"] + "/questions"
        )
    finally:
        event.remove(session.bind.sync_engine, "before_cursor_execute", hit)
    assert r.status_code == 200, r.text
    assert count <= 8


async def test_catalog_snapshot_and_schema_drift(session):
    from app.models import Base, KnowledgeTaxonomyRelease
    from alembic.migration import MigrationContext
    from alembic.autogenerate import compare_metadata

    release = await session.scalar(select(KnowledgeTaxonomyRelease))
    assert len(release.catalog_snapshot["topics"]) > 0
    connection = await session.connection()

    def compare(conn):
        return compare_metadata(
            MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata
        )

    assert await connection.run_sync(compare) == []


async def test_login_rate_limit_and_redacted_validation(client):
    token = (await client.get("/api/v1/auth/csrf")).json()["csrf_token"]
    # Start a separate pre-login browser, so an authenticated session cannot supply
    # the login nonce and accidentally hide a missing CSRF guard.
    async with AsyncClient(
        transport=ASGITransport(app),
        base_url="http://test",
        headers={"Origin": "http://test"},
    ) as browser:
        token = (await browser.get("/api/v1/auth/csrf")).json()["csrf_token"]
        browser.headers["X-CSRF-Token"] = token
        invalid = await browser.post(
            "/api/v1/auth/login", json={"username": "test-admin", "password": "secret"}
        )
        assert invalid.status_code == 422 and "secret" not in invalid.text
        for _ in range(10):
            response = await browser.post(
                "/api/v1/auth/login",
                json={"username": "missing-user", "password": "wrong-password-123"},
            )
        assert response.status_code == 401
        assert (
            await browser.post(
                "/api/v1/auth/login",
                json={"username": "missing-user", "password": "wrong-password-123"},
            )
        ).status_code == 429


async def test_publisher_cannot_edit_but_can_publish(client, session, storage):
    from sqlalchemy import delete
    from app.models import Role

    batch = await upload(client)
    await review(client, session, batch)
    user = await session.scalar(select(User).where(User.username == "test-admin"))
    await session.execute(delete(UserRole).where(UserRole.user_id == user.id))
    session.add(Role(name="publisher"))
    await session.flush()
    session.add(UserRole(user_id=user.id, role="publisher"))
    await session.commit()
    assert (await client.post("/api/v2/admin/import-batches")).status_code == 403
    assert (
        await client.post("/api/v1/admin/markdown-batches/" + batch + "/publish")
    ).status_code == 200
